from jsonschema import Draft202012Validator
from story_core.schemas import SCHEMAS
from story_core.errors import StoryError
from test_workflow import make,step,result_for


def test_contract_forbids_entity_fields_on_non_entity():
    base={'kind':'fact','key':'k','value':'事实','evidence':'原文','visibility':'reader'}
    validator=Draft202012Validator(SCHEMAS['extract'])
    assert list(validator.iter_errors({'memories':[{**base,'entity_type':'person'}]}))
    assert list(validator.iter_errors({'memories':[{**base,'aliases':['甲']}]}))
    assert not list(validator.iter_errors({'memories':[{**base,'kind':'entity','entity_type':'person','aliases':['甲']}]}))


def test_retry_receives_only_current_extraction_validation_failure(tmp_path):
    service,book=make(tmp_path,chapters=1)
    for _ in range(3):step(service,book)
    task=service.next_task(book)
    assert task['stage']=='extract'
    service.fail_task(task['task_id'],task['lease_id'],StoryError('INVALID_RESULT','实体类型只能用于 entity',{'key':'person-x','field':'entity_type'}))
    service.control(book,'resume')
    retry=service.next_task(book)
    assert retry['input']['validation_feedback']['task_id']==task['task_id']
    assert retry['input']['validation_feedback']['details']['field']=='entity_type'
    service.submit_task(retry['task_id'],retry['lease_id'],result_for(retry))
    assert 'validation_feedback' not in service.next_task(book)['input']


def test_extraction_does_not_receive_previous_chapter_prose(tmp_path):
    service,book=make(tmp_path,chapters=1)
    for _ in range(3):step(service,book)
    data=service.next_task(book)['input']
    assert 'recent_chapters' not in data
    assert 'brief' not in data
    assert 'candidate' in data
    assert 'existing_memory_keys' in data


def test_knowledge_owner_and_promise_status_are_required_by_schema():
    validator=Draft202012Validator(SCHEMAS['extract'])
    base={'key':'k','value':'v','evidence':'e','visibility':'reader'}
    for kind,field in [('knowledge','owner'),('promise','status')]:
        assert list(validator.iter_errors({'memories':[{**base,'kind':kind}]}))
    assert list(validator.iter_errors({'memories':[{**base,'kind':'knowledge','owner':''}]}))
