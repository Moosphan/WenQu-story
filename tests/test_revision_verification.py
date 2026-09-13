import pytest
from story_core.errors import StoryError


def test_verification_rejects_missing_items_and_unanchored_claims():
    from story_core.revision_response import validate_verification
    context={'feedback_items':[{'feedback_id':'F-1'}]}
    result={'revision_verification':[{'feedback_id':'F-1','status':'verified','evidence':'他开门','explanation':'已改为行动'}]}
    validate_verification(result,context,{'body':'他开门。'})
    with pytest.raises(StoryError): validate_verification(result,context,{'body':'他关窗。'})
    result['revision_verification']=[]
    with pytest.raises(StoryError): validate_verification(result,context,{'body':'他开门。'})


def test_revision_context_is_bound_to_candidate_and_not_reader(tmp_path):
    from test_workflow import make, finish, result_for
    service,book=make(tmp_path,chapters=1); finish(service,book)
    chapter=service.get_book(book)['chapters'][0]
    service.control(book,'revise',chapter_number=1,body=chapter['body'],title=chapter['title'],feedback='保留结尾')
    task=service.next_task(book); result=result_for(task)
    service.submit_task(task['task_id'],task['lease_id'],result)
    extract=service.next_task(book); service.submit_task(extract['task_id'],extract['lease_id'],result_for(extract))
    review=service.next_task(book)
    assert review['input']['revision_check']['task_id']==task['task_id']
    assert review['input']['revision_check']['feedback_items'][0]['request']=='保留结尾'
    answer=result_for(review)
    feedback_id=review['input']['revision_check']['feedback_items'][0]['feedback_id']
    answer['revision_verification']=[{'feedback_id':feedback_id,'status':'uncertain','evidence':'','explanation':'仍需作者判断'}]
    service.submit_task(review['task_id'],review['lease_id'],answer)
    reader=service.next_task(book)
    assert reader['stage']=='reader'
    assert 'revision_check' not in reader['input']
    assert 'revision_response' not in reader['input']['candidate']

    history=service.review_history(book)
    assert history[-1]['result']['revision_verification']==answer['revision_verification']
    assert history[-1]['revision_check']['task_id']==task['task_id']


@pytest.mark.parametrize('hard', [False, True])
def test_invalid_optional_quote_does_not_discard_hard_review(tmp_path, hard):
    from test_workflow import make, step, result_for
    service,book=make(tmp_path,chapters=1)
    for _ in range(4): step(service,book)
    task=service.next_task(book)
    assert task['stage']=='continuity'
    result=result_for(task)
    if hard:
        result['issues']=[{'severity':'major','dimension':'规则','evidence':'原文','explanation':'明确冲突','suggestion':'修复规则冲突'}]
        result['verdict']='revise'
    result['revision_verification']=[{'feedback_id':'wrong','status':'verified','evidence':'missing','explanation':'unsupported'}]
    response=service.submit_task(task['task_id'],task['lease_id'],result)
    assert response['next_stage']==('revise' if hard else 'reader')
    assert service.submit_task(task['task_id'],task['lease_id'],result)==response
    review=service.review_history(book)[-1]
    assert review['supplement_warning']['code']=='INVALID_REVISION_VERIFICATION'
    assert 'revision_verification' not in review['result']
