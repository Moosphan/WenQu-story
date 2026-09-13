from test_workflow import make, result_for


def test_title_guidance_reaches_planning_writing_and_bounded_review(tmp_path):
    service, book = make(tmp_path, review_mode='bounded', chapter_limit=1)
    seen = set()
    for _ in range(6):
        task = service.next_task(book)
        if task['stage'] in ('outline', 'draft', 'continuity'):
            assert '章节标题' in task['input']['instruction']
            seen.add(task['stage'])
        if task['stage'] == 'continuity':
            assert '不单独触发返修' in task['input']['instruction']
            assert task['input']['quality_policy']['max_reviews'] == 3
            break
        service.submit_task(task['task_id'], task['lease_id'], result_for(task))
    assert seen == {'outline', 'draft', 'continuity'}
