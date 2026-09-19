import json
import pytest
from story_core.errors import StoryError
from story_core.schemas import validate
from test_workflow import make, result_for, to_stage


def test_short_chapter_is_not_accepted_at_old_65_percent_floor():
    book = {'settings': {'target_words': 2300}}
    with pytest.raises(StoryError) as error:
        validate('draft', {'title': '渡口', 'body': '字' * 1655}, book)
    assert error.value.details['min'] == 2070
    assert error.value.details['phase'] == 'manuscript_length'
    assert '1655' in error.value.message
    assert '补足' in error.value.details['next_action']
    validate('draft', {'title': '渡口', 'body': '字' * 2300}, book)


def test_two_short_attempts_relax_only_the_next_task(tmp_path):
    s, b = make(tmp_path)
    t = to_stage(s, b, 'draft')
    for attempt in range(2):
        assert t['input']['length_requirement']['min'] == 72
        with pytest.raises(StoryError) as error:
            s.submit_task(t['task_id'], t['lease_id'], {'title': '渡口', 'body': '字' * 60})
        s.fail_task(t['task_id'], t['lease_id'], error.value)
        s.control(b, 'resume')
        t = s.next_task(b)
    assert t['input']['length_requirement']['min'] == 63
    assert t['input']['length_requirement']['target'] == 80
    s.submit_task(t['task_id'], t['lease_id'], {'title': '渡口', 'body': '字' * 63})
    assert s.status(b)['run']['stage'] == 'extract'


def test_relaxed_length_floor_for_normal_chapter():
    from story_core.schemas import length_requirement
    assert length_requirement(2300, short_failures=2)['min'] == 1800
    assert length_requirement(2311, short_failures=9)['min'] == 1800
    assert length_requirement(2300, short_failures=1)['min'] == 2070


def test_long_failure_breaks_short_streak(tmp_path):
    s, b = make(tmp_path)
    t = to_stage(s, b, 'draft')
    for size in (60, 150, 60):
        with pytest.raises(StoryError) as error:
            s.submit_task(t['task_id'], t['lease_id'], {'title': '渡口', 'body': '字' * size})
        s.fail_task(t['task_id'], t['lease_id'], error.value)
        s.control(b, 'resume')
        t = s.next_task(b)
    assert t['input']['length_requirement']['min'] == 72


def test_revision_relaxes_after_two_failures_and_keeps_guidance(tmp_path):
    s, b = make(tmp_path)
    t = to_stage(s, b, 'draft')
    s.submit_task(t['task_id'], t['lease_id'], result_for(t))
    t = s.next_task(b)
    s.submit_task(t['task_id'], t['lease_id'], result_for(t))
    t = s.next_task(b)
    s.submit_task(t['task_id'], t['lease_id'], result_for(t, True))
    t = s.next_task(b)
    for size in (40, 60):
        with pytest.raises(StoryError) as error:
            s.submit_task(t['task_id'], t['lease_id'], {'title': '渡口', 'body': '字' * size})
        s.fail_task(t['task_id'], t['lease_id'], error.value)
        s.control(b, 'resume')
        t = s.next_task(b)
    assert t['input']['length_requirement']['min'] == 63
    assert '连续两次' in t['input']['instruction']
    assert 'length_feedback' in t['input']['instruction']
    s.submit_task(t['task_id'], t['lease_id'], {'title': '渡口', 'body': '字' * 63})
    assert s.status(b)['run']['stage'] == 'extract'


def test_retry_receives_length_error_and_preserves_candidate(tmp_path):
    s, b = make(tmp_path)
    t = to_stage(s, b, 'draft')
    assert t['input']['length_requirement']['target'] == 80
    s.submit_task(t['task_id'], t['lease_id'], result_for(t))
    extract = s.next_task(b)
    s.submit_task(extract['task_id'], extract['lease_id'], result_for(extract))
    review = s.next_task(b)
    s.submit_task(review['task_id'], review['lease_id'], result_for(review, True))
    t = s.next_task(b)
    assert t['stage'] == 'revise'
    original = t['input']['candidate']
    with pytest.raises(StoryError) as error:
        s.submit_task(t['task_id'], t['lease_id'], {'title': '渡口', 'body': '字' * 40})
    s.fail_task(t['task_id'], t['lease_id'], error.value)
    s.control(b, 'resume')
    retry = s.next_task(b)
    assert retry['input']['candidate'] == original
    assert retry['input']['length_feedback']['count'] == 40
    assert retry['input']['length_feedback']['min'] == 72
    assert '不能靠重复解释' in retry['input']['instruction']
    assert s.get_book(b)['chapters'] == []
    with s.store.read() as conn:
        artifact = json.loads(conn.execute("SELECT payload FROM events WHERE kind='task_result_rejected'").fetchone()[0])
    assert artifact['raw_result']['body'] == '字' * 40
    assert artifact['details']['count'] == 40


def test_draft_length_retry_uses_saved_prose_as_repair_source(tmp_path):
    service, book = make(tmp_path)
    task = to_stage(service, book, 'draft')
    for size in (40, 60):
        prose = {'title': '渡口', 'body': '字' * size}
        with pytest.raises(StoryError) as error:
            service.submit_task(task['task_id'], task['lease_id'], prose)
        service.fail_task(task['task_id'], task['lease_id'], error.value)
        service.control(book, 'resume')
        task = service.next_task(book)
        assert task['input']['length_repair_source'] == prose
        assert task['input']['expansion_requirement']['target_additional_words'] == 80 - size
        assert service.status(book)['run']['candidate'] is None
    service.submit_task(task['task_id'], task['lease_id'], {'title': '渡口', 'body': '字' * 80})
    assert service.status(book)['run']['stage'] == 'extract'


def test_old_word_count_diagnostic_no_longer_suggests_protocol_replay():
    import subprocess
    from pathlib import Path
    source = Path('story_core/web/app.js').read_text()
    function = source[source.index('function diagnosisText('):source.index('function renderExecutionSummary(')]
    subprocess.run(['node', '-e', function + "\nconst out = diagnosisText({count:1368,target:2300,min:1495,max:3680,phase:'result_validation',next_action:'修复协议后可重放'}); if(!out.includes('1368') || !out.includes('补足场景') || out.includes('修复协议后可重放'))throw Error(out);"], check=True)


def test_length_retry_requires_full_body_not_deletion_patches(tmp_path):
    s,b=make(tmp_path)
    t=to_stage(s,b,'draft')
    s.submit_task(t['task_id'],t['lease_id'],result_for(t))
    t=s.next_task(b);s.submit_task(t['task_id'],t['lease_id'],result_for(t))
    t=s.next_task(b);s.submit_task(t['task_id'],t['lease_id'],result_for(t,True))
    t=s.next_task(b)
    with pytest.raises(StoryError) as error:
        s.submit_task(t['task_id'],t['lease_id'],{'title':'章','body':'字'*40})
    s.fail_task(t['task_id'],t['lease_id'],error.value);s.control(b,'resume')
    t=s.next_task(b)
    assert t['input']['revision_mode']=='expand_full_body'
    assert 'patches' not in t['output_schema'].get('properties',{})
    assert 'body' in t['output_schema']['required']
    assert '优先输出 patches' not in t['input']['instruction']
    assert t['input']['expansion_requirement']['target_additional_words'] >= 0
    assert '只交 title/body' not in t['input']['instruction']
    with pytest.raises(StoryError) as error:
        s.submit_task(t['task_id'],t['lease_id'],{'patches':[{'before':t['input']['candidate']['body'], 'after':'字'*80}]})
    assert error.value.code == 'REVISION_MODE_MISMATCH'
    assert s.status(b)['run']['candidate'] == t['input']['candidate']
