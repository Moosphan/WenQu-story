import pytest
from story_core.errors import StoryError


def test_evidence_is_resolved_from_immutable_paragraph_not_model_quote():
    from story_core.memory_sources import resolve_sources, source_paragraphs
    body='第一段。\n\n第二段：父亲签名。'
    assert source_paragraphs(body)[1]=={'id':2,'text':'第二段：父亲签名。'}
    result={'memories':[{'kind':'fact','key':'签名','value':'发现签名','visibility':'reader','evidence_paragraph':2}]}
    resolved=resolve_sources(result,{'body':body})
    assert resolved['memories'][0]['evidence']=='第二段：父亲签名。'
    assert 'evidence_paragraph' not in resolved['memories'][0]
    assert 'evidence_paragraph' in result['memories'][0]
    result['memories'][0]['evidence_paragraph']=99
    with pytest.raises(StoryError):resolve_sources(result,{'body':body})


def test_source_submission_is_idempotent_and_cannot_invent_evidence(tmp_path):
    from test_workflow import make,step
    service,book=make(tmp_path,chapters=1)
    for _ in range(3):step(service,book)
    task=service.next_task(book)
    assert task['input']['source_paragraphs']
    result={'memories':[{'kind':'summary','key':'摘要','value':'本章事件','visibility':'reader','evidence_paragraph':1}]}
    response=service.submit_task(task['task_id'],task['lease_id'],result)
    assert service.submit_task(task['task_id'],task['lease_id'],result)==response
    assert service.status(book)['run']['extraction']['memories'][0]['evidence']==task['input']['source_paragraphs'][0]['text']


def test_missing_source_identifies_memory_and_repair_action():
    from story_core.memory_sources import resolve_sources
    result={'memories':[{'kind':'promise','key':'未回收线索','value':'仍未回收'}]}
    with pytest.raises(StoryError) as error: resolve_sources(result,{'body':'正文'})
    assert error.value.code=='INVALID_EVIDENCE_SOURCE'
    assert error.value.details['key']=='未回收线索'
    assert error.value.details['next_action']


def test_old_failed_session_does_not_override_newer_success(tmp_path):
    import json
    from test_workflow import make,step
    from story_core.http import create_app
    from fastapi.testclient import TestClient
    service,book=make(tmp_path,chapters=1)
    run=service.status(book)['run']
    with service.store.write(book) as conn:
        conn.execute("INSERT INTO workbench_executions(id,book_id,run_id,executor,status,error,started_at,finished_at) VALUES (?,?,?,?,?,?,?,?)",('old',book,run['run_id'],'claude','failed',json.dumps({'code':'INVALID_RESULT','message':'旧错误'}),1,2))
    step(service,book)
    with TestClient(create_app(tmp_path)) as client:
        status=client.get(f'/api/books/{book}/status').json()
    assert status['execution']['outcome']=='succeeded'
    assert status['worker_error'] is None
    assert status['worker_session'] is None
