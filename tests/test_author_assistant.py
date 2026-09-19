import pytest
from story_core import author_assistant as chat
from story_core.errors import StoryError
from test_settings_planning import prepared
from test_workflow import result_for


def ready(tmp_path):
    service, book = prepared(tmp_path)
    service.control(book, 'pause')
    return service, book


class Provider:
    name = 'fixture'
    def __init__(self, result): self.result, self.tasks = result, []
    def generate(self, task):
        self.tasks.append(task)
        return self.result


def send(service, book, proposal=False):
    before = service.get_book(book)
    turn = chat.begin(service, book, '讨论人物和全书方向', before['revision'])
    result = {'reply': '建议先讨论人物动机。', 'proposal': {'direction': '扩展世界与主线', 'brief': {**before['brief'], 'premise': '新的主线'}} if proposal else None}
    provider = Provider(result)
    chat.execute(service, book, turn['id'], provider)
    return chat.history(service, book)['turns'][-1], provider


def test_discussion_is_persistent_bounded_and_never_mutates_book(tmp_path):
    service, book = ready(tmp_path)
    before = service.get_book(book)
    first, _ = send(service, book)
    second, provider = send(service, book)
    assert first['status'] == second['status'] == 'complete'
    assert second['proposal'] is None and not second['can_apply']
    assert provider.tasks[0]['input']['history'][0]['message'] == first['message']
    assert service.get_book(book) == before


def test_apply_fences_old_and_stale_proposals_and_is_idempotent(tmp_path):
    service, book = ready(tmp_path)
    old, _ = send(service, book, True)
    turn, _ = send(service, book, True)
    with pytest.raises(StoryError): chat.apply(service, book, old['id'])
    original = service.get_book(book)
    applied = chat.apply(service, book, turn['id'])
    assert applied == chat.apply(service, book, turn['id'])
    assert service.get_book(book)['brief'] == original['brief']
    task = service.next_task(book)
    assert '扩展世界与主线' in task['input']['instruction']
    assert task['input']['brief']['premise'] == '新的主线'
    result = result_for(task)
    result['volumes'] = [{'number': 1, 'title': '全书', 'range': [1, 2], 'goal': '扩展', 'world_expansion': '远方', 'climax': '结局'}]
    service.submit_task(task['task_id'], task['lease_id'], result)
    assert service.get_book(book)['brief']['premise'] == '新的主线'
    assert not service.next_task(book).get('task_id')


def test_recovery_failure_scope_pending_and_cancel(tmp_path):
    service, book = ready(tmp_path)
    turn = chat.begin(service, book, '你好', service.get_book(book)['revision'])
    with pytest.raises(StoryError): chat.begin(service, book, '重复', service.get_book(book)['revision'])
    chat.recover(service)
    assert chat.history(service, book)['turns'][-1]['status'] == 'failed'
    other = service.open_book('其他书')['book_id']
    with pytest.raises(StoryError): chat.apply(service, other, turn['id'])
    before = service.get_book(book)
    proposed, _ = send(service, book, True)
    chat.apply(service, book, proposed['id'])
    service.control(book, 'cancel_planning')
    for key in ('brief', 'plan', 'chapters'): assert service.get_book(book)[key] == before[key]
    stale, _ = send(service, book, True)
    service.update_book_settings(book, target_words=100)
    with pytest.raises(StoryError): chat.apply(service, book, stale['id'])


def test_http_dispatch_and_provider_setup_failure_is_durable(tmp_path, monkeypatch):
    import threading
    from fastapi.testclient import TestClient
    from story_core.http import create_app
    service, book = ready(tmp_path)
    done = threading.Event()
    class HTTPProvider:
        name = 'fixture'
        def generate(self, task):
            done.set()
            return {'reply': '普通讨论', 'proposal': None}
    monkeypatch.setattr('story_core.http.configured_provider', lambda **kwargs: HTTPProvider())
    with TestClient(create_app(tmp_path)) as client:
        response = client.post(f'/api/books/{book}/assistant', json={'message': '人物分析', 'expected_revision': service.get_book(book)['revision'], 'chapter_number': 1})
        assert response.status_code == 200
        assert done.wait(2)
        import time
        for _ in range(100):
            data = client.get(f'/api/books/{book}/assistant').json()
            if data['turns'][-1]['status'] == 'complete': break
            time.sleep(.01)
        assert data['turns'][-1]['reply'] == '普通讨论'
        def fail_provider(**kwargs): raise RuntimeError('secret=NEVER_PERSIST')
        monkeypatch.setattr('story_core.http.configured_provider', fail_provider)
        response = client.post(f'/api/books/{book}/assistant', json={'message': '继续', 'expected_revision': service.get_book(book)['revision']})
        assert response.status_code == 200
        assert response.json()['status'] == 'failed'
        assert 'NEVER_PERSIST' not in str(client.get(f'/api/books/{book}/assistant').json())


def test_proposed_planning_requires_full_volume_roadmap(tmp_path):
    service, book = ready(tmp_path)
    turn, _ = send(service, book, True)
    chat.apply(service, book, turn['id'])
    task = service.next_task(book)
    assert 'volumes' in task['output_schema']['required']
    with pytest.raises(StoryError): service.submit_task(task['task_id'], task['lease_id'], result_for(task))


def test_context_keeps_complete_brief_all_chapters_and_latest_proposal(tmp_path):
    import json
    service, book = ready(tmp_path)
    with service.store.write(book) as conn:
        value = service.get_book(book)
        value['brief']['premise'] = '设定' * 2000
        value['plan']['chapters'] = [{**value['plan']['chapters'][0], 'number': n, 'goal': '详细计划' * 80} for n in range(1, 201)]
        conn.execute('UPDATE books SET brief=?,plan=? WHERE id=?', (json.dumps(value['brief']), json.dumps(value['plan']), book))
    turn, _ = send(service, book, True)
    _, provider = send(service, book)
    snapshot = provider.tasks[0]['input']
    assert snapshot['brief'] == service.get_book(book)['brief']
    assert len(snapshot['chapter_roadmap']) == 200
    assert snapshot['chapter_roadmap'][-1]['number'] == 200
    assert snapshot['latest_proposal'] == turn['proposal']


def test_proposal_cannot_erase_existing_character_identity(tmp_path):
    service, book = ready(tmp_path)
    current = service.get_book(book)
    turn = chat.begin(service, book, '扩展格局', current['revision'])
    brief = {**current['brief'], 'characters': [{**current['brief']['characters'][0], 'name': '不同的人'}]}
    chat.execute(service, book, turn['id'], Provider({'reply': '建议扩展', 'proposal': {'direction': '更大世界', 'brief': brief}}))
    assert chat.history(service, book)['turns'][-1]['status'] == 'failed'


def test_candidate_context_precedes_committed_and_protected_chapters_survive(tmp_path):
    import json
    service, book = ready(tmp_path)
    original = service.get_book(book)
    with service.store.write(book) as conn:
        conn.execute("UPDATE runs SET candidate=?,chapter_number=2 WHERE book_id=?", (json.dumps({'title': '候选', 'body': '正在修订的实际正文'}), book))
    turn = chat.begin(service, book, '讨论正文', original['revision'], 2)
    provider = Provider({'reply': '分析', 'proposal': {'direction': '更新卷纲', 'brief': original['brief']}})
    chat.execute(service, book, turn['id'], provider)
    assert provider.tasks[0]['input']['selected_chapter']['content']['status'] == 'candidate'
    assert not chat.history(service, book)['turns'][-1]['can_apply']
    with pytest.raises(StoryError, match='所有章节'):
        chat.apply(service, book, turn['id'])
    assert service.get_book(book)['plan']['chapters'] == original['plan']['chapters']


def test_every_batch_has_direction_and_preserves_candidate_skeleton(tmp_path):
    import json
    service, book = ready(tmp_path)
    original = service.get_book(book)
    candidate = {'title': '候选', 'body': '真实候选稿'}
    with service.store.write(book) as conn:
        settings = {**original['settings'], 'chapter_count': 45}
        plan = {**original['plan'], 'chapters': [{**original['plan']['chapters'][0], 'number': n} for n in range(1, 46)]}
        conn.execute('UPDATE books SET config=?,plan=? WHERE id=?', (json.dumps(settings), json.dumps(plan), book))
        conn.execute('UPDATE runs SET candidate=?,chapter_number=1 WHERE book_id=?', (json.dumps(candidate), book))
    turn, _ = send(service, book, True)
    chat.apply(service, book, turn['id'])
    batches = []
    for _ in range(3):
        task = service.next_task(book)
        bounds = task['input']['outline_batch']
        batches.append((bounds['start'], bounds['end']))
        assert '扩展世界与主线' in task['input']['instruction']
        result = result_for(task)
        result['chapters'] = result['chapters'][bounds['start']-1:bounds['end']]
        if len(batches) == 1:
            assert 'volumes' in task['output_schema']['required']
            result['volumes'] = [{'number': 1, 'title': '全书', 'range': [1, 45], 'goal': '扩展', 'world_expansion': '远方', 'climax': '结局'}]
        else:
            assert 'volumes' not in task['output_schema']['required']
            with pytest.raises(StoryError):
                service.submit_task(task['task_id'], task['lease_id'], {**result, 'volumes': []})
        accepted = service.submit_task(task['task_id'], task['lease_id'], result)
        assert service.submit_task(task['task_id'], task['lease_id'], result) == accepted
    assert batches == [(2, 21), (22, 41), (42, 45)]
    assert service.get_book(book)['plan']['chapters'][0] == plan['chapters'][0]
    assert service.get_book(book)['plan']['volumes'][0]['range'] == [1, 45]
    with service.store.read() as conn: assert service._latest_run(conn, book)['candidate'] == candidate
    assert not service.next_task(book).get('task_id')


def test_restart_recovers_and_malformed_result_fails_without_changes(tmp_path):
    from fastapi.testclient import TestClient
    from story_core.http import create_app
    service, book = ready(tmp_path)
    before = service.get_book(book)
    turn = chat.begin(service, book, '重启之前', before['revision'])
    with TestClient(create_app(tmp_path)) as client:
        assert client.get(f'/api/books/{book}/assistant').json()['turns'][-1]['status'] == 'failed'
    chat.execute(service, book, turn['id'], Provider({'reply': '迟到结果', 'proposal': None}))
    assert chat.history(service, book)['turns'][-1]['status'] == 'failed'
    turn = chat.begin(service, book, '再试一次', before['revision'])
    chat.execute(service, book, turn['id'], Provider({'reply': '缺少必需字段'}))
    assert chat.history(service, book)['turns'][-1]['status'] == 'failed'
    assert service.get_book(book) == before


def test_http_apply_dispatches_only_planning_and_repeated_apply_is_safe(tmp_path, monkeypatch):
    import threading
    from fastapi.testclient import TestClient
    from story_core.http import create_app
    from story_core.runtime import WorkbenchRuntime
    service, book = ready(tmp_path)
    turn, _ = send(service, book, True)
    done = threading.Event()
    original_execute = WorkbenchRuntime.execute
    class Planner:
        name = 'fixture'
        def generate(self, task):
            assert task['stage'] == 'outline'
            result = result_for(task)
            result['volumes'] = [{'number': 1, 'title': '全书', 'range': [1, 2], 'goal': '扩展', 'world_expansion': '远方', 'climax': '结局'}]
            return result
    def execute(self, session, provider):
        try: return original_execute(self, session, provider)
        finally: done.set()
    monkeypatch.setattr('story_core.runtime.WorkbenchRuntime.execute', execute)
    monkeypatch.setattr('story_core.http.configured_provider', lambda **kwargs: Planner())
    with TestClient(create_app(tmp_path)) as client:
        url = f'/api/books/{book}/assistant/{turn["id"]}/apply'
        response = client.post(url)
        assert response.status_code == 200 and response.json()['planning_pending']
        assert done.wait(3)
        assert client.post(url).json() == response.json()
        assert service.status(book)['run']['status'] == 'paused'
        assert not service.get_book(book)['chapters']
