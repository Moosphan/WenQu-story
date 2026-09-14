from test_workflow import make, to_stage
from story_core.errors import StoryError


def test_failed_revision_switches_to_full_body_without_changing_candidate(tmp_path):
    service, book = make(tmp_path)
    task = to_stage(service, book, 'draft')
    from test_workflow import result_for
    service.submit_task(task['task_id'], task['lease_id'], result_for(task))
    with service.store.write(book) as conn:
        conn.execute("UPDATE runs SET stage='revise' WHERE book_id=?", (book,))
    failed = service.next_task(book)
    candidate = failed['input']['candidate']
    service.fail_task(failed['task_id'], failed['lease_id'], StoryError('HOST_NATIVE_ERROR', '非法 JSON'))
    service.control(book, 'resume')
    retry = service.next_task(book)
    assert retry['input']['revision_mode'] == 'full_body'
    assert retry['input']['candidate'] == candidate
    assert 'body' in retry['output_schema']['properties']
    assert 'oneOf' not in retry['output_schema']
    assert '不返回 patches' in retry['input']['instruction']
