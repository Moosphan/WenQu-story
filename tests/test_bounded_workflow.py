from test_workflow import make,step,result_for


def run_to_review(s,b):
    for _ in range(10):
        t=s.next_task(b)
        if t.get('stage')=='continuity':return t
        assert t.get('task_id'),t
        s.submit_task(t['task_id'],t['lease_id'],result_for(t))
    raise AssertionError('no review')


def test_single_requires_author_confirmation_and_medium_is_advisory(tmp_path):
    s,b=make(tmp_path,review_mode='bounded',chapter_limit=1)
    t=run_to_review(s,b);r=result_for(t,True);r['issues'][0]['severity']='medium'
    s.submit_task(t['task_id'],t['lease_id'],r)
    assert s.status(b)['run']['status']=='awaiting_author'
    ch=s.get_book(b)['chapters'][0]
    assert ch['quality']['author_status']=='pending'
    assert s.next_task(b)['task_id'] is None
    s.control(b,'approve_chapter',chapter_number=1,version_id=ch['version_id'])
    assert s.get_book(b)['chapters'][0]['quality']['author_status']=='approved'
    assert s.status(b)['run']['status']=='batch_complete'


def test_batch_third_review_releases_with_label_and_resumes_next_chapter(tmp_path):
    s,b=make(tmp_path,review_mode='bounded',chapter_limit=2)
    for n in range(3):
        t=run_to_review(s,b)
        assert t['input']['quality_policy']['review_number']==n+1
        s.submit_task(t['task_id'],t['lease_id'],result_for(t,True))
    ch=s.get_book(b)['chapters'][0]
    assert ch['quality']['release_reason']=='review_limit'
    assert ch['quality']['review_count']==3
    assert ch['quality']['author_status']=='assumed'
    assert s.status(b)['run']['chapter_number']==2
    s.control(b,'pause');assert s.next_task(b)['task_id'] is None
    s.control(b,'resume');assert s.next_task(b)['chapter_number']==2


def test_confirmation_is_bound_to_version_and_revision_reopens_single_review(tmp_path):
    import pytest
    from story_core.errors import StoryError
    s,b=make(tmp_path,review_mode='bounded',chapter_limit=1)
    t=run_to_review(s,b);s.submit_task(t['task_id'],t['lease_id'],result_for(t))
    ch=s.get_book(b)['chapters'][0]
    with pytest.raises(StoryError):s.control(b,'approve_chapter',chapter_number=1,version_id='old')
    s.control(b,'revise',chapter_number=1,title=ch['title'],body=ch['body'],feedback='压缩重复说明')
    assert s.status(b)['run']['quality_policy']['single']
    assert s.next_task(b)['stage']=='revise'


def test_selected_chapter_review_does_not_review_next_unwritten_chapter(tmp_path):
    s,b=make(tmp_path,review_mode='bounded',chapter_limit=1)
    t=run_to_review(s,b);s.submit_task(t['task_id'],t['lease_id'],result_for(t))
    ch=s.get_book(b)['chapters'][0]
    s.control(b,'approve_chapter',chapter_number=1,version_id=ch['version_id'])
    s.control(b,'review_chapter',chapter_number=1)
    t=s.next_task(b)
    assert t['chapter_number']==1 and t['stage']=='extract'


def test_batch_does_not_pause_for_arc_review(tmp_path):
    s,b=make(tmp_path,chapters=6,review_mode='bounded',chapter_limit=6)
    for number in range(1,6):
        t=run_to_review(s,b)
        assert t['chapter_number']==number
        s.submit_task(t['task_id'],t['lease_id'],result_for(t))
    t=s.next_task(b)
    assert t['stage']=='draft' and t['chapter_number']==6
