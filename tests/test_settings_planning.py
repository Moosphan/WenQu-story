import pytest
from story_core.errors import StoryError
from story_core.service import StoryService
from test_workflow import make, result_for


def prepared(tmp_path):
    service, book = make(tmp_path, chapters=2)
    for _ in range(2):
        task = service.next_task(book)
        service.submit_task(task['task_id'], task['lease_id'], result_for(task))
    return service, book


def test_settings_plan_applies_atomically_and_does_not_write_chapters(tmp_path):
    service, book = prepared(tmp_path)
    before = service.get_book(book)
    service.update_book_settings(book, title='待应用书名', chapter_count=4)
    assert service.get_book(book)['settings'] == before['settings']
    assert service.get_book(book)['title'] == before['title']
    assert service.status(book)['settings_planning']['target_settings']['chapter_count'] == 4
    task = service.next_task(book)
    assert task['stage'] == 'outline' and task['input']['settings']['chapter_count'] == 4
    service.submit_task(task['task_id'], task['lease_id'], result_for(task))
    assert service.get_book(book)['settings']['chapter_count'] == 4
    assert service.get_book(book)['title'] == '待应用书名'
    assert not service.next_task(book).get('task_id')
    assert service.status(book)['run']['stage'] == 'draft'


def test_cancel_settings_plan_restores_original_state_and_fences_result(tmp_path):
    service, book = prepared(tmp_path)
    before = service.get_book(book)
    service.update_book_settings(book, chapter_count=4)
    task = service.next_task(book)
    service.control(book, 'cancel_planning')
    after = service.get_book(book)
    assert after['settings'] == before['settings'] and after['plan'] == before['plan']
    with pytest.raises(StoryError): service.submit_task(task['task_id'], task['lease_id'], result_for(task))
    assert service.status(book)['settings_planning'] is None


def test_planning_prevents_write_resume_and_survives_restart(tmp_path):
    service, book = prepared(tmp_path)
    service.update_book_settings(book, chapter_count=61)
    with pytest.raises(StoryError): service.control(book, 'resume')
    task = service.next_task(book); result = result_for(task)
    result['chapters'] = result['chapters'][:20]
    service.submit_task(task['task_id'], task['lease_id'], result)
    service = StoryService(tmp_path)
    assert service.next_task(book)['input']['outline_batch']['start'] == 21
    with pytest.raises(StoryError): service.update_book_settings(book, chapter_count=80)


def test_http_save_starts_planning_without_worker_button(tmp_path, monkeypatch):
    import threading
    from fastapi.testclient import TestClient
    from story_core.http import create_app
    service, book = prepared(tmp_path)
    called = threading.Event()
    class Provider:
        name = 'fixture'
    monkeypatch.setattr('story_core.http.configured_provider', lambda **kwargs: Provider())
    def execute(self, session, provider):
        called.set()
    monkeypatch.setattr('story_core.runtime.WorkbenchRuntime.execute', execute)
    with TestClient(create_app(tmp_path)) as client:
        response = client.patch(f'/api/books/{book}/settings', json={'chapter_count': 4})
        assert response.status_code == 200
        assert response.json()['planning_pending']
        assert called.wait(2)
        assert client.get(f'/api/books/{book}').json()['settings']['chapter_count'] == 2


def test_queued_plan_recovers_after_restart_and_budget_can_be_raised(tmp_path):
    from story_core.runtime import WorkbenchRuntime
    service, book = prepared(tmp_path)
    service.update_book_settings(book, chapter_count=61)
    WorkbenchRuntime(service).recover()
    assert service.status(book)['settings_planning']['status'] == 'paused'
    service.control(book, 'resume_planning', budget_tokens=1)
    assert service.next_task(book)['reason'] == 'budget'
    service.control(book, 'resume_planning', budget_tokens=5000000, max_steps=500)
    assert service.next_task(book)['input']['outline_batch']['start'] == 1


def test_completed_book_cancel_preserves_ending_and_complete_status(tmp_path):
    from test_workflow import finish
    service, book = make(tmp_path, chapters=1)
    finish(service, book)
    before = service.get_book(book)
    service.update_book_settings(book, chapter_count=3)
    assert service.get_book(book)['ending'] == before['ending']
    service.control(book, 'cancel_planning')
    after = service.get_book(book)
    for key in ('ending', 'status', 'settings', 'plan', 'chapters'):
        assert after[key] == before[key]


def test_http_planner_finishes_without_generating_chapter(tmp_path, monkeypatch):
    import threading
    from fastapi.testclient import TestClient
    from story_core.http import create_app
    from story_core.runtime import WorkbenchRuntime
    service, book = prepared(tmp_path)
    finished = threading.Event(); calls = []
    original = WorkbenchRuntime.execute
    class Provider:
        name = 'fixture'
        def generate(self, task):
            calls.append(task['stage'])
            assert task['stage'] == 'outline'
            return result_for(task)
    def execute(self, session, provider):
        try: return original(self, session, provider)
        finally: finished.set()
    monkeypatch.setattr('story_core.runtime.WorkbenchRuntime.execute', execute)
    monkeypatch.setattr('story_core.http.configured_provider', lambda **kwargs: Provider())
    with TestClient(create_app(tmp_path)) as client:
        response = client.patch(f'/api/books/{book}/settings', json={'chapter_count': 4})
        assert response.status_code == 200
        assert finished.wait(3)
        current = client.get(f'/api/books/{book}').json()
        assert current['settings']['chapter_count'] == 4 and not current['chapters']
        status = client.get(f'/api/books/{book}/status').json()
        assert status['settings_planning'] is None and status['run']['status'] == 'paused'
        assert calls == ['outline']
