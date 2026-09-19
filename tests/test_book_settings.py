from fastapi.testclient import TestClient
from story_core.http import create_app


def test_settings_cancel_old_worker_even_after_resume(tmp_path):
    from test_workflow import make, to_stage
    from story_core.runtime import WorkbenchRuntime
    service, bid = make(tmp_path)
    to_stage(service, bid, 'draft')
    service.control(bid, 'pause')
    service.control(bid, 'resume')
    runtime = WorkbenchRuntime(service)
    worker = runtime.reserve(bid, 'test')
    service.next_task(bid, worker['id'])
    service.update_book_settings(bid, target_words=150)
    service.control(bid, 'resume')
    assert runtime.latest(bid)['status'] == 'cancelled'
    assert service.next_task(bid, worker['id'])['task_id'] is None
    assert runtime.reserve(bid, 'test')['id'] != worker['id']


def test_completed_book_expansion_reopens_without_overwriting_chapters(tmp_path):
    from test_workflow import make, finish
    service, bid = make(tmp_path, chapters=1)
    finish(service, bid)
    before = service.get_book(bid)['chapters']
    service.update_book_settings(bid, chapter_count=2)
    assert service.get_book(bid)['status'] == 'draft'
    assert service.get_book(bid)['chapters'] == before
    service.start_run(bid)
    assert service.next_task(bid)['stage'] == 'outline'


def test_settings_save_and_revision_fence(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        book = client.post('/api/books', json={'request': '修仙经营', 'chapter_count': 3}).json()
        path = f"/api/books/{book['book_id']}"
        response = client.patch(path + '/settings', json={'title': '新书名', 'chapter_count': 5, 'target_words': 3000, 'expected_revision': book['revision']})
        assert response.status_code == 200, response.text
        saved = client.get(path).json()
        assert saved['title'] == '新书名'
        assert saved['settings']['chapter_count'] == 5
        assert saved['settings']['target_words'] == 3000
        assert client.patch(path + '/settings', json={'title': '旧页面', 'expected_revision': book['revision']}).status_code >= 400
        assert client.patch(path + '/settings', json={'title': '   '}).status_code >= 400


def test_settings_preserve_checkpoint_and_request_replanning(tmp_path):
    from story_core.service import StoryService
    from test_workflow import result_for
    service = StoryService(tmp_path)
    book = service.open_book('经营修仙', chapter_count=3, target_words=80)
    book_id = book['book_id']
    service.start_run(book_id)
    for _ in range(2):
        task = service.next_task(book_id)
        service.submit_task(task['task_id'], task['lease_id'], result_for(task))
    task = service.next_task(book_id)
    service.update_book_settings(book_id, chapter_count=5, target_words=100)
    run = service.status(book_id)['run']
    assert run['status'] == 'paused'
    assert run['chapter_number'] == 1
    assert run['stage'] == 'outline'
    with service.store.read() as conn:
        assert conn.execute('SELECT status FROM tasks WHERE id=?', (task['task_id'],)).fetchone()[0] == 'cancelled'
    assert len(service.get_book(book_id)['plan']['chapters']) == 3
