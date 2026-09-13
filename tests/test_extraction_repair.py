from copy import deepcopy
import pytest
from story_core.errors import StoryError
from test_workflow import make, step, result_for


def failed(service, book):
    task = service.next_task(book)
    good = result_for(task)['memories'][0]
    bad = dict(kind='fact', key='缺证据', value='听见声音', visibility='reader')
    with pytest.raises(StoryError) as error:
        service.submit_task(task['task_id'], task['lease_id'], {'memories': [good, bad]})
    service.fail_task(task['task_id'], task['lease_id'], error.value)
    service.control(book, 'resume')
    return good


def test_retry_retains_valid_records_and_repairs_only_invalid_after_restart(tmp_path):
    from story_core.service import StoryService
    service, book = make(tmp_path)
    for _ in range(3): step(service, book)
    good = failed(service, book)
    service = StoryService(tmp_path)
    task = service.next_task(book)
    repair = task['input']['extraction_repair']
    assert len(repair['invalid']) == 1
    assert repair['invalid'][0]['index'] == 1
    assert repair['retained_count'] == 1
    result = {'repairs': [{'index': 1, 'memory': dict(kind='fact', key='缺证据', value='听见声音', visibility='reader', evidence_paragraph=1)}]}
    response = service.submit_task(task['task_id'], task['lease_id'], result)
    assert service.submit_task(task['task_id'], task['lease_id'], result) == response
    memories = service.status(book)['run']['extraction']['memories']
    assert memories[0]['key'] == good['key']
    assert len(memories) == 2
    assert response['next_stage'] == 'continuity'


def test_repair_cannot_replace_valid_item_or_skip_invalid_item(tmp_path):
    service, book = make(tmp_path)
    for _ in range(3): step(service, book)
    failed(service, book)
    task = service.next_task(book)
    for result in ({'repairs': []}, {'repairs': [{'index': 0, 'memory': None, 'reason': '删掉'}]}):
        with pytest.raises(StoryError): service.submit_task(task['task_id'], task['lease_id'], result)
    assert service.status(book)['run']['stage'] == 'extract'


def test_stale_submit_does_not_record_repair(tmp_path):
    service, book = make(tmp_path)
    for _ in range(3): step(service, book)
    task = service.next_task(book)
    service.control(book, 'pause')
    with pytest.raises(StoryError): service.submit_task(task['task_id'], task['lease_id'], {'memories':[{}]})
    with service.store.read() as conn:
        assert conn.execute("SELECT count(*) FROM events WHERE kind='extraction_rejected'").fetchone()[0] == 0


def test_transport_failure_keeps_repair_checkpoint(tmp_path):
    service, book = make(tmp_path)
    for _ in range(3): step(service, book)
    failed(service, book)
    task = service.next_task(book)
    original = deepcopy(task['input']['extraction_repair'])
    service.fail_task(task['task_id'], task['lease_id'], StoryError('HOST_TIMEOUT', '超时'))
    service.control(book, 'resume')
    assert service.next_task(book)['input']['extraction_repair'] == original


def test_unsupported_evidence_stays_pending_and_discard_is_explicit(tmp_path):
    service, book = make(tmp_path)
    for _ in range(3): step(service, book)
    failed(service, book)
    task = service.next_task(book)
    invalid = {'repairs': [{'index': 1, 'memory': dict(kind='fact', key='缺证据', value='声音', visibility='reader', evidence_paragraph=999)}]}
    with pytest.raises(StoryError): service.submit_task(task['task_id'], task['lease_id'], invalid)
    assert service.status(book)['run']['extraction'] is None
    result = {'repairs': [{'index': 1, 'memory': None, 'reason': '本章无依据，不提取该条'}]}
    service.submit_task(task['task_id'], task['lease_id'], result)
    assert len(service.status(book)['run']['extraction']['memories']) == 1
