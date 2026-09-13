import pytest
from story_core.errors import StoryError


def test_feedback_items_include_author_and_evidence():
    from story_core.revision_response import feedback_items
    reviews=[{'stage':'author','notes':'保留结尾','issues':[]},{'stage':'continuity','issues':[{'dimension':'知情','evidence':'他知道了','suggestion':'删去说明'}]}]
    items=feedback_items(reviews)
    assert len(items)==2
    assert items==feedback_items(reviews)
    assert items[0]['request']=='保留结尾'


def test_response_requires_coverage_and_real_quote():
    from story_core.revision_response import validate_response
    items=[{'feedback_id':'F-1'}]
    result={'body':'他推开门。','revision_response':[{'feedback_id':'F-1','status':'changed','explanation':'改为行动','evidence':'他推开门'}]}
    validate_response(result,items)
    result['revision_response'][0]['evidence']='不存在'
    with pytest.raises(StoryError): validate_response(result,items)
    result['revision_response']=[]
    with pytest.raises(StoryError): validate_response(result,items)
    validate_response({'body':'旧协议'},items)


def test_response_persists_without_entering_reader_candidate(tmp_path):
    from test_workflow import make, finish, result_for
    service, book=make(tmp_path, chapters=1)
    finish(service,book)
    chapter=service.get_book(book)['chapters'][0]
    service.control(book,'revise',chapter_number=1,body=chapter['body'],title=chapter['title'],feedback='保留结尾')
    task=service.next_task(book)
    assert task['stage']=='revise'
    item=task['input']['feedback_items'][0]
    result=result_for(task)
    result['revision_response']=[{'feedback_id':item['feedback_id'],'status':'not_changed','explanation':'保留当前结尾','evidence':''}]
    service.submit_task(task['task_id'],task['lease_id'],result)
    version=service.candidate_versions(book)[-1]
    assert version['revision_response']==result['revision_response']
    assert version['feedback_items'][0]['request']=='保留结尾'
    assert 'revision_response' not in service.status(book)['run']['candidate']


def test_multiple_exact_quotes_can_be_joined_without_rejecting_manuscript():
    from story_core.revision_response import validate_response
    validate_response({'body':'“甲。”他说。\n过了一会儿。\n“乙。”', 'revision_response':[{'feedback_id':'x','status':'changed','evidence':'“甲。”/“乙。”','explanation':'两处修改'}]},[{'feedback_id':'x'}])
