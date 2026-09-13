import json
import pytest
from story_core.errors import StoryError
from test_workflow import make, result_for
from test_bounded_workflow import run_to_review


@pytest.mark.parametrize('hard', [False, True])
def test_optional_schema_error_preserves_review_and_idempotency(tmp_path, hard):
    s, b = make(tmp_path, review_mode='bounded', chapter_limit=1)
    t = run_to_review(s, b)
    result = result_for(t, hard)
    result['revision_verification'] = [{'feedback_id': 'F-1', 'status': 'uncertain',
        'evidence': '', 'explanation': '待确认', 'notes': '额外说明'}]
    response = s.submit_task(t['task_id'], t['lease_id'], result)
    assert s.submit_task(t['task_id'], t['lease_id'], result) == response
    assert s.status(b)['run']['status'] == ('running' if hard else 'awaiting_author')
    if hard:
        assert response['next_stage'] == 'revise'
    history = s.review_history(b)[-1]
    assert 'revision_verification' not in history['result']
    assert history['result']['issues'] == result['issues']
    assert history['supplement_warning']['details']['errors'][0]['path'] == ['revision_verification', 0]
    with s.store.read() as conn:
        raw = json.loads(conn.execute('SELECT result FROM tasks WHERE id=?', (t['task_id'],)).fetchone()[0])
    assert raw['revision_verification'][0]['notes'] == '额外说明'


def test_invalid_core_is_rejected_with_durable_diagnostic(tmp_path):
    s, b = make(tmp_path, review_mode='bounded', chapter_limit=1)
    t = run_to_review(s, b)
    result = result_for(t)
    result['verdict'] = 'looks_good'
    with pytest.raises(StoryError) as raised:
        s.submit_task(t['task_id'], t['lease_id'], result)
    assert raised.value.details['phase'] == 'result_validation'
    with s.store.read() as conn:
        record = conn.execute("SELECT payload FROM events WHERE kind='task_result_rejected' AND book_id=?", (b,)).fetchone()
    artifact = json.loads(record[0])
    assert artifact['raw_result'] == result
    assert artifact['errors'][0]['path'] == ['verdict']
    assert s.status(b)['run']['stage'] == 'continuity'
    assert s.get_book(b)['chapters'] == []


def test_invalid_lease_cannot_record_result_artifact(tmp_path):
    s, b = make(tmp_path, review_mode='bounded', chapter_limit=1)
    t = run_to_review(s, b)
    with pytest.raises(StoryError):
        s.submit_task(t['task_id'], 'wrong', {'verdict': 'bad'})
    with s.store.read() as conn:
        assert conn.execute("SELECT count(*) FROM events WHERE kind='task_result_rejected'").fetchone()[0] == 0


def test_invalid_revision_annotation_keeps_manuscript(tmp_path):
    s, b = make(tmp_path, review_mode='bounded', chapter_limit=1)
    t = run_to_review(s, b)
    s.submit_task(t['task_id'], t['lease_id'], result_for(t, True))
    t = s.next_task(b)
    assert t['stage'] == 'revise'
    result = result_for(t)
    result['revision_response'] = [{'feedback_id': 'F-1', 'status': 'changed',
        'evidence': '', 'explanation': '已改', 'notes': '多余字段'}]
    response = s.submit_task(t['task_id'], t['lease_id'], result)
    assert response['next_stage'] == 'extract'
    assert s.status(b)['run']['candidate']['body'] == result['body']
    assert s.submit_task(t['task_id'], t['lease_id'], result) == response


def test_schema_diagnostics_render_field_path():
    import subprocess
    from pathlib import Path
    source = Path('story_core/web/app.js').read_text()
    function = source[source.index('function diagnosisText('):source.index('function renderExecutionSummary(')]
    subprocess.run(['node', '-e', function + "\nconst output = diagnosisText({phase:'result_validation',errors:[{path:['revision_verification',3],message:'notes unexpected'}]});if(!output.includes('revision_verification.3') || !output.includes('notes unexpected'))throw Error(output);"], check=True)
