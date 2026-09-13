import pytest
from story_core.errors import StoryError
from test_workflow import make, step, result_for


def pending(tmp_path):
    service, book = make(tmp_path, max_revisions=0)
    for _ in range(4): step(service, book)
    task = service.next_task(book)
    service.submit_task(task['task_id'], task['lease_id'], result_for(task, block=True))
    return service, book, service.next_task(book)


def answer(task, decision):
    return {'decisions': [{'index': 0, 'decision': decision, 'evidence_paragraph': 1, 'reason': '核对当前正文与既有规则后的裁决依据'}]}


def test_editorial_adjudication_advances_without_rewriting(tmp_path):
    s, b, task = pending(tmp_path)
    assert task.get('input', {}).get('review_adjudication')
    original = s.status(b)['run']['candidate']
    response = s.submit_task(task['task_id'],task['lease_id'],answer(task,'editorial'))
    assert response['next_stage'] == 'reader'
    assert s.status(b)['run']['candidate'] == original
    assert s.submit_task(task['task_id'],task['lease_id'],answer(task,'editorial')) == response
    assert s.review_history(b)[-1]['reviewer'] == '独立审稿裁决'


@pytest.mark.parametrize('decision', ['confirmed_conflict', 'uncertain'])
def test_conflict_or_uncertainty_stops_without_another_revision(tmp_path, decision):
    s,b,task=pending(tmp_path)
    assert task.get('task_id')
    result=s.submit_task(task['task_id'],task['lease_id'],answer(task,decision))
    assert result['status']=='needs_attention'
    assert s.status(b)['run']['attempts']==0


def test_missing_or_fabricated_adjudication_cannot_unlock(tmp_path):
    s,b,task=pending(tmp_path)
    assert task.get('task_id')
    for result in ({'decisions':[]}, {'decisions':[{'index':0,'decision':'editorial','evidence_paragraph':999,'reason':'说明'}]}):
        with pytest.raises(StoryError):s.submit_task(task['task_id'],task['lease_id'],result)
    assert s.status(b)['run']['stage']=='continuity'
