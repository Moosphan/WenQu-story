import pytest
from story_core.errors import StoryError
from test_workflow import make, step


def test_review_sources_are_immutable_and_idempotent(tmp_path):
    s,b=make(tmp_path)
    for _ in range(4):step(s,b)
    task=s.next_task(b)
    assert task['input']['source_paragraphs']
    result={'verdict':'pass','notes':'建议','issues':[dict(severity='minor',dimension='表达',evidence_paragraph=1,explanation='可以更紧凑',suggestion='可选精简')]}
    response=s.submit_task(task['task_id'],task['lease_id'],result)
    assert s.submit_task(task['task_id'],task['lease_id'],result)==response
    assert s.review_history(b)[-1]['result']['issues'][0]['evidence']==task['input']['source_paragraphs'][0]['text']


def test_review_rejects_nonexistent_paragraph_before_rewriting(tmp_path):
    s,b=make(tmp_path)
    for _ in range(4):step(s,b)
    task=s.next_task(b)
    result={'verdict':'revise','notes':'','issues':[dict(severity='blocker',dimension='矛盾',evidence_paragraph=999,explanation='矛盾',suggestion='改写')]}
    with pytest.raises(StoryError):s.submit_task(task['task_id'],task['lease_id'],result)
    assert s.status(b)['run']['attempts']==0
