from test_workflow import make,step,result_for


def test_memory_only_repairs_do_not_spend_manuscript_revisions_and_are_bounded(tmp_path):
    s,b=make(tmp_path,max_revisions=0)
    for _ in range(4):step(s,b)
    for attempt in range(3):
        t=s.next_task(b)
        assert t['stage']=='continuity'
        result=result_for(t,block=True);result['repair_target']='memory'
        s.submit_task(t['task_id'],t['lease_id'],result)
        r=s.status(b)['run']
        assert r['attempts']==0
        if attempt<2:
            assert r['stage']=='extract'
            step(s,b)
        else:
            assert r['status']=='needs_attention'
            assert '记忆' in r['reason']
