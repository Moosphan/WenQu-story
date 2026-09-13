from test_workflow import make, result_for


def test_prose_style_reaches_draft_and_default_review_without_extra_stage(tmp_path):
    s,b=make(tmp_path,review_mode='bounded',chapter_limit=1)
    seen=[]
    for _ in range(6):
        t=s.next_task(b);seen.append(t['stage'])
        if t['stage'] in ('draft','continuity'):
            assert '正文自然度规范 prose-v1' in t['input']['instruction']
            assert '不以检测分数作为放行门槛' in t['input']['instruction']
        s.submit_task(t['task_id'],t['lease_id'],result_for(t))
        if t['stage']=='continuity':break
    assert seen==['brief','outline','draft','extract','continuity']
    assert s.status(b)['run']['status']=='awaiting_author'
