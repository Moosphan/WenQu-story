import pytest

from story_core.errors import StoryError
from story_core.service import StoryService
from test_workflow import make, result_for


def test_long_outline_batches_resume_and_only_publish_when_complete(tmp_path):
    service, book = make(tmp_path, chapters=61, budget_tokens=2000000)
    task = service.next_task(book)
    service.submit_task(task['task_id'], task['lease_id'], result_for(task))
    initial = service.get_book(book)['revision']
    for start in (1, 21, 41, 61):
        service = StoryService(tmp_path)
        task = service.next_task(book)
        batch = task['input']['outline_batch']
        assert batch['start'] == start and batch['end'] == min(start+19, 61)
        assert task['output_schema']['properties']['chapters']['minItems'] == batch['end']-start+1
        result = result_for(task)
        result['chapters'] = [c for c in result['chapters'] if start <= c['number'] <= batch['end']]
        if start == 1:
            bad = {**result, 'chapters': result['chapters'][:-1]}
            with pytest.raises(StoryError): service.submit_task(task['task_id'], task['lease_id'], bad)
        response = service.submit_task(task['task_id'], task['lease_id'], result)
        assert service.submit_task(task['task_id'], task['lease_id'], result) == response
        current = service.get_book(book)
        if start < 61:
            assert current['revision'] == initial and not current['plan']
            assert response['next_stage'] == 'outline'
        else:
            assert len(current['plan']['chapters']) == 61
            assert response['next_stage'] == 'draft'


def test_expansion_keeps_candidate_and_published_plan_until_all_batches_finish(tmp_path):
    service, book = make(tmp_path, chapters=40, budget_tokens=5000000)
    for _ in range(3):
        task = service.next_task(book)
        service.submit_task(task['task_id'], task['lease_id'], result_for(task))
    with service.store.write(book) as conn:
        conn.execute('UPDATE runs SET chapter_number=20 WHERE book_id=?', (book,))
    before = service.get_book(book)
    service.update_book_settings(book, chapter_count=200)
    for start in range(21, 201, 20):
        task = service.next_task(book)
        assert task['input']['outline_batch']['start'] == start
        assert len(task['input']['previous_plan']['chapters']) <= 20
        result = result_for(task)
        result['chapters'] = [c for c in result['chapters'] if start <= c['number'] <= start+19]
        response = service.submit_task(task['task_id'], task['lease_id'], result)
        if start < 181: assert service.get_book(book)['plan'] == before['plan']
    after = service.get_book(book)
    assert after['plan']['chapters'][:20] == before['plan']['chapters'][:20]
    assert len(after['plan']['chapters']) == 200
    assert response['next_stage'] == 'extract'
    assert not service.next_task(book).get('task_id')
    service.control(book, 'resume')
    assert service.next_task(book)['input']['candidate']['body'] == result_for({'stage': 'draft'})['body']


def test_shrink_discards_obsolete_inherited_volume(tmp_path):
    from story_core.outline_batches import progress
    service, book = make(tmp_path, chapters=100)
    current = service.get_book(book)
    current['plan'] = {'chapters': [], 'promises': [], 'volumes': [
        {'number': 1, 'title': '旧卷', 'range': [1, 200], 'goal': '', 'world_expansion': '', 'climax': ''}]}
    with service.store.read() as conn:
        assembled, seed = progress(conn, current, {'run_id': 'fixture', 'chapter_number': 1, 'candidate': None})
    assert assembled['volumes'] == []
