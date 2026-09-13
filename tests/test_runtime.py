import threading

import pytest

from story_core.errors import StoryError
from test_workflow import make, result_for


def test_restart_recovers_owned_task_and_preserves_candidate(tmp_path):
    from story_core.runtime import WorkbenchRuntime
    service, book = make(tmp_path)
    from test_workflow import step
    for _ in range(3):
        step(service, book)
    candidate = service.status(book)['run']['candidate']
    assert candidate
    runtime = WorkbenchRuntime(service)
    session = runtime.reserve(book, 'fake')
    task = service.next_task(book, session['id'])
    # A newly created server finds a persisted session from a process that died.
    recovered = WorkbenchRuntime(service)
    recovered.recover()
    status = service.status(book)
    assert status['run']['status'] == 'paused'
    assert status['run']['stage'] == task['stage']
    assert status['run']['candidate'] == candidate
    assert recovered.latest(book)['error']['code'] == 'SERVER_INTERRUPTED'
    with pytest.raises(StoryError) as error:
        service.submit_task(task['task_id'], task['lease_id'], result_for(task), session['id'])
    assert error.value.code == 'INVALID_LEASE'
    recovered.recover()
    assert len([e for e in service.status(book)['events'] if e['kind'] == 'worker_interrupted']) == 1


def test_runtime_does_not_take_live_external_task_or_pause_external_run(tmp_path):
    from story_core.runtime import WorkbenchRuntime
    service, book = make(tmp_path)
    task = service.next_task(book, 'mcp-reader')
    runtime = WorkbenchRuntime(service)
    runtime.recover()
    with pytest.raises(StoryError) as error:
        runtime.reserve(book, 'fake')
    assert error.value.code == 'TASK_BUSY'
    assert service.task_active(task['task_id'], task['lease_id'])
    assert service.status(book)['run']['status'] == 'running'


def test_runtime_persists_failure_and_retries_to_complete(tmp_path):
    from story_core.runtime import WorkbenchRuntime
    service, book = make(tmp_path, chapters=1)
    runtime = WorkbenchRuntime(service)

    class Failure:
        name = 'fake'
        def generate(self, task):
            raise StoryError('PROVIDER_ERROR', '测试连接失败')

    session = runtime.reserve(book, Failure.name)
    runtime.execute(session, Failure())
    reloaded = WorkbenchRuntime(service)
    assert reloaded.latest(book)['status'] == 'failed'
    assert reloaded.latest(book)['error']['code'] == 'PROVIDER_ERROR'
    service.control(book, 'resume')

    class Success:
        name = 'fake'
        def generate(self, task): return result_for(task)

    session = reloaded.reserve(book, Success.name)
    reloaded.execute(session, Success())
    assert reloaded.latest(book)['status'] == 'complete'
    assert service.status(book)['chapters']['committed'] == 1


def test_runtime_rejects_duplicate_session_and_server(tmp_path):
    from story_core.runtime import WorkbenchRuntime
    service, book = make(tmp_path)
    first = WorkbenchRuntime(service)
    second = WorkbenchRuntime(service)
    with first.server_lock():
        with pytest.raises(StoryError) as error:
            with second.server_lock(): pass
        assert error.value.code == 'SERVER_ALREADY_RUNNING'
    first.reserve(book, 'fake')
    with pytest.raises(StoryError) as error:
        second.reserve(book, 'fake')
    assert error.value.code == 'WORKER_RUNNING'


def test_shutdown_fences_late_provider_result(tmp_path):
    from story_core.runtime import WorkbenchRuntime
    service, book = make(tmp_path)
    runtime = WorkbenchRuntime(service)
    entered, release = threading.Event(), threading.Event()

    class Slow:
        name = 'fake'
        def generate(self, task):
            entered.set()
            assert release.wait(5)
            return result_for(task)

    session = runtime.reserve(book, Slow.name)
    thread = threading.Thread(target=runtime.execute, args=(session, Slow()))
    thread.start()
    try:
        assert entered.wait(5)
        runtime.recover()
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive()
    assert runtime.latest(book)['status'] == 'interrupted'
    assert service.get_book(book)['brief'] is None
    assert service.status(book)['run']['status'] == 'paused'


def test_http_restart_exposes_recovery_and_rejects_busy_task(tmp_path):
    from fastapi.testclient import TestClient
    from story_core.http import create_app
    from story_core.runtime import WorkbenchRuntime
    service, book = make(tmp_path)
    runtime = WorkbenchRuntime(service)
    session = runtime.reserve(book, 'fake')
    service.next_task(book, session['id'])
    with TestClient(create_app(tmp_path)) as client:
        result = client.get(f'/api/books/{book}/status').json()
        assert result['worker_running'] is False
        assert result['worker_error']['code'] == 'SERVER_INTERRUPTED'
        assert result['run']['status'] == 'paused'
    with TestClient(create_app(tmp_path)) as client:
        assert client.get(f'/api/books/{book}/status').json()['worker_error']['code'] == 'SERVER_INTERRUPTED'


def test_stopped_session_cannot_lease_work_after_resume(tmp_path):
    from story_core.runtime import WorkbenchRuntime
    service, book = make(tmp_path)
    runtime = WorkbenchRuntime(service)
    session = runtime.reserve(book, 'fake')
    runtime.recover()
    service.control(book, 'resume')
    assert not service.next_task(book, session['id']).get('task_id')
    assert service.next_task(book, 'new-host').get('task_id')


def test_expired_task_not_reported_as_running(tmp_path):
    service, book = make(tmp_path)
    task = service.next_task(book)
    with service.store.write() as conn:
        conn.execute('UPDATE tasks SET lease_until=0 WHERE id=?', (task['task_id'],))
    status = service.status(book)
    assert status['execution']['outcome'] == 'failed'
    assert status['execution']['failure']['code'] == 'TASK_LEASE_EXPIRED'


def test_http_worker_failure_survives_restart_then_finishes(tmp_path, monkeypatch):
    import time
    from fastapi.testclient import TestClient
    from story_core.http import create_app
    service, book = make(tmp_path, chapters=1)

    class Provider:
        name = 'fake'
        fail = True
        def generate(self, task):
            if self.fail:
                raise StoryError('PROVIDER_ERROR', '连接不可用')
            return result_for(task)

    provider = Provider()
    monkeypatch.setattr('story_core.http.configured_provider', lambda **kw: provider)

    def wait(client):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            data = client.get(f'/api/books/{book}/status').json()
            if not data['worker_running']:
                return data
            time.sleep(.01)
        raise AssertionError('worker did not finish')

    with TestClient(create_app(tmp_path)) as client:
        assert client.post(f'/api/books/{book}/worker').status_code == 200
        assert wait(client)['worker_session']['status'] == 'failed'
    with TestClient(create_app(tmp_path)) as client:
        assert client.get(f'/api/books/{book}/status').json()['worker_error']['code'] == 'PROVIDER_ERROR'
        provider.fail = False
        client.post(f'/api/books/{book}/control', json={'action': 'resume'}).raise_for_status()
        client.post(f'/api/books/{book}/worker').raise_for_status()
        data = wait(client)
        assert data['worker_error'] is None
        assert data['worker_session']['status'] == 'complete'
        assert data['chapters']['committed'] == 1
        exported = client.post(f'/api/books/{book}/export', json={})
        assert exported.status_code == 200
        assert exported.json()['complete'] is True


def test_process_crash_releases_lock_and_keeps_recovery_point(tmp_path):
    import subprocess
    import sys
    from fastapi.testclient import TestClient
    from story_core.http import create_app
    from story_core.runtime import WorkbenchRuntime
    service, book = make(tmp_path)
    command = '''
import os, sys
from story_core.service import StoryService
from story_core.runtime import WorkbenchRuntime
service = StoryService(sys.argv[1])
runtime = WorkbenchRuntime(service)
with runtime.server_lock():
    session = runtime.reserve(sys.argv[2], 'fake')
    service.next_task(sys.argv[2], session['id'])
    os._exit(9)
'''
    result = subprocess.run([sys.executable, '-c', command, str(tmp_path), book], timeout=10)
    assert result.returncode == 9
    with TestClient(create_app(tmp_path)) as client:
        status = client.get(f'/api/books/{book}/status').json()
        assert status['run']['status'] == 'paused'
        assert status['worker_session']['status'] == 'interrupted'
        assert status['worker_error']['code'] == 'SERVER_INTERRUPTED'


def test_cancelled_sample_with_runtime_history_can_be_deleted(tmp_path):
    from story_core.runtime import WorkbenchRuntime
    service, book = make(tmp_path)
    runtime = WorkbenchRuntime(service)
    runtime.reserve(book, 'fake')
    runtime.recover()
    service.control(book, 'cancel')
    service.set_book_kind(book, 'sample')
    assert service.delete_sample(book)['deleted'] is True


def test_http_does_not_claim_success_when_external_worker_owns_task(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from story_core.http import create_app
    service, book = make(tmp_path)
    task = service.next_task(book, 'external-host')
    class Provider:
        name = 'fake'
        def generate(self, task): raise AssertionError('must not be called')
    monkeypatch.setattr('story_core.http.configured_provider', lambda **kw: Provider())
    with TestClient(create_app(tmp_path)) as client:
        response = client.post(f'/api/books/{book}/worker')
        assert response.status_code == 409
        assert response.json()['detail']['code'] == 'TASK_BUSY'
        assert client.get(f'/api/books/{book}/status').json()['worker_running'] is False
    assert service.task_active(task['task_id'], task['lease_id'])
