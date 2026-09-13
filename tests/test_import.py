import pytest

from story_core.errors import StoryError
from story_core.service import StoryService
from test_workflow import result_for


def setup_import(tmp_path):
    service = StoryService(tmp_path)
    book = service.open_book('写修仙小说', chapter_count=5, target_words=80)
    chapter = {'chapter_number': 1, **result_for({'stage': 'draft'})}
    return service, book['book_id'], chapter


def test_import_is_persisted_readable_but_not_canon(tmp_path):
    service, book, chapter = setup_import(tmp_path)
    result = service.import_chapters(book, [chapter], expected_revision=0)
    assert result['imported'] == 1
    restored = StoryService(tmp_path)
    assert restored.get_book(book)['chapters'][0]['body'] == chapter['body']
    assert restored.get_book(book)['chapters'][0]['status'] == 'imported'
    assert restored.query(book, '沈知秋')['hits'] == []
    with pytest.raises(StoryError):
        restored.export(book)


def test_import_replay_no_duplicate_and_conflict_keeps_original(tmp_path):
    service, book, chapter = setup_import(tmp_path)
    service.import_chapters(book, [chapter])
    revision = service.get_book(book)['revision']
    assert service.import_chapters(book, [chapter])['imported'] == 0
    assert service.get_book(book)['revision'] == revision
    with pytest.raises(StoryError) as error:
        service.import_chapters(book, [{**chapter, 'body': chapter['body'].replace('冷掉', '温热')}])
    assert error.value.code == 'CHAPTER_EXISTS'
    assert service.get_book(book)['chapters'][0]['body'] == chapter['body']


def test_import_batch_atomic_and_no_gaps(tmp_path):
    service, book, chapter = setup_import(tmp_path)
    with pytest.raises(StoryError):
        service.import_chapters(book, [chapter, {**chapter, 'chapter_number': 3}])
    assert service.get_book(book)['chapters'] == []
    with pytest.raises(StoryError):
        service.import_chapters(book, [chapter, {**chapter, 'chapter_number': 2, 'body': ''}])
    assert service.get_book(book)['chapters'] == []


def test_imported_text_drives_plan_then_review_without_regeneration(tmp_path):
    service, book, chapter = setup_import(tmp_path)
    service.import_chapters(book, [chapter, {**chapter, 'chapter_number': 2}])
    service.start_run(book, chapter_limit=2, review_mode="legacy")
    seen = []
    for _ in range(20):
        task = service.next_task(book)
        if not task.get('task_id'):
            break
        seen.append(task['stage'])
        if task['stage'] in ('brief', 'outline'):
            assert task['input']['imported_chapters'][0]['body'] == chapter['body']
        if task['stage'] == 'extract':
            assert task['input']['candidate']['body'] == chapter['body']
        service.submit_task(task['task_id'], task['lease_id'], result_for(task))
    assert seen == ['brief', 'outline', 'extract', 'continuity', 'reader', 'reader', 'extract', 'continuity', 'reader', 'reader']
    assert task['status'] == 'batch_complete'
    assert len(service.get_book(book)['chapters']) == 2
    assert all(c['status'] == 'committed' for c in service.get_book(book)['chapters'])
    assert service.query(book, '收音机')['hits']
    with pytest.raises(StoryError):
        service.export(book)


def test_import_refuses_active_run_and_stale_revision(tmp_path):
    service, book, chapter = setup_import(tmp_path)
    with pytest.raises(StoryError) as error:
        service.import_chapters(book, [chapter], expected_revision=12)
    assert error.value.code == 'STALE_REVISION'
    service.start_run(book, review_mode="legacy")
    with pytest.raises(StoryError) as error:
        service.import_chapters(book, [chapter])
    assert error.value.code == 'RUN_ACTIVE'


def test_edit_import_before_planning_retains_reverse_planning(tmp_path):
    service, book, chapter = setup_import(tmp_path)
    service.import_chapters(book, [chapter])
    edited = chapter['body'].replace('冷掉', '温热')
    service.control(book, 'revise', chapter_number=1, body=edited)
    task = service.next_task(book)
    assert task['stage'] == 'brief'
    assert task['input']['imported_chapters'][0]['body'] == edited


def test_imported_original_remains_distinguishable_during_downstream_review(tmp_path):
    service, book, chapter = setup_import(tmp_path)
    service.import_chapters(book, [chapter, {**chapter, 'chapter_number': 2}])
    service.start_run(book, chapter_limit=1, review_mode="legacy")
    for _ in range(5):
        task = service.next_task(book)
        service.submit_task(task['task_id'], task['lease_id'], result_for(task))
    assert service.get_book(book)['chapters'][1]['status'] == 'imported'
