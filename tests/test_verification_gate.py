from story_core.revision_response import feedback_items


def test_feedback_preserves_severity_and_memory_target():
    items=feedback_items([{'stage':'continuity','repair_target':'memory','issues':[{'severity':'major','dimension':'事实','evidence':'原句','suggestion':'改记忆'}]}])
    assert items[0]['severity']=='major'
    assert items[0]['repair_target']=='memory'


def test_unresolved_hard_issue_cannot_silently_advance(tmp_path):
    from test_workflow import make, finish, result_for
    service,book=make(tmp_path,chapters=1); finish(service,book)
    chapter=service.get_book(book)['chapters'][0]
    service.control(book,'revise',chapter_number=1,body=chapter['body'],title=chapter['title'],feedback='修改')
    with service.store.write(book) as conn:
        import json
        run=service._latest_run(conn,book)
        reviews=[{'stage':'continuity','issues':[{'severity':'major','dimension':'知情','evidence':'原文','suggestion':'避免泄密'}]}]
        conn.execute('UPDATE runs SET reviews=? WHERE id=?',(json.dumps(reviews),run['run_id']))
    for _ in range(2):
        task=service.next_task(book);service.submit_task(task['task_id'],task['lease_id'],result_for(task))
    task=service.next_task(book);answer=result_for(task)
    answer['revision_verification']=[{'feedback_id':task['input']['revision_check']['feedback_items'][0]['feedback_id'],'status':'unresolved','evidence':'','explanation':'仍然泄密'}]
    service.submit_task(task['task_id'],task['lease_id'],answer)
    status=service.status(book)['run']
    assert status['status']=='needs_attention'
    assert status['stage']=='continuity'
    assert '核验' in status['reason']


def test_advisory_and_uncertain_items_do_not_add_blockers():
    from story_core.revision_response import unresolved_hard_requirements
    context={'revision_check':{'feedback_items':[{'feedback_id':'a','severity':'major','source':'reader'},{'feedback_id':'b','severity':'minor','source':'continuity'},{'feedback_id':'c','severity':'major','source':'continuity'}]}}
    result={'revision_verification':[{'feedback_id':'a','status':'unresolved'},{'feedback_id':'b','status':'unresolved'},{'feedback_id':'c','status':'uncertain'}]}
    assert unresolved_hard_requirements(result,context)==[]
