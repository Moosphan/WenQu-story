import json

import httpx
import pytest

from story_core.errors import StoryError
from story_core.providers import OpenAICompatible, run_worker
from story_core.storage import dumps
from test_workflow import make, result_for, to_stage


def test_all_pipeline_tasks_have_shadow_measurement_and_submission_is_not_rebudgeted(tmp_path, monkeypatch):
    service, book = make(tmp_path)
    task = to_stage(service, book, 'draft')
    diag = task['input'].get('context_diagnostics')
    assert diag and diag['mode'] == 'shadow'
    assert diag['stage'] == 'draft'
    assert diag['output_reserve'] == 12000
    monkeypatch.setenv('HULK_CONTEXT_MODE', 'adaptive')
    monkeypatch.setenv('HULK_CONTEXT_WINDOW_TOKENS', '1')
    # New budget policy never refuses an already generated valid result.
    service.submit_task(task['task_id'], task['lease_id'], result_for(task))
    assert service.status(book)['run']['candidate']['body']


def test_library_context_policy_survives_restart_and_checks_actual_model(tmp_path, monkeypatch):
    from story_core.service import StoryService
    from story_core.context_compiler import compile_task
    service, book = make(tmp_path)
    (service.store.root / 'context-policy.json').write_text(json.dumps({
        'mode': 'adaptive', 'context_window': 100000,
        'model_windows': {'configured-model': 100000}}))
    restarted = StoryService(service.store.root)
    task = restarted.next_task(book)
    assert task['input']['context_diagnostics']['mode'] == 'adaptive'
    assert compile_task(task, model='configured-model').executable
    assert not compile_task(task, model='unknown-model').executable
    monkeypatch.setenv('HULK_CONTEXT_MODE', 'off')
    restarted.submit_task(task['task_id'], task['lease_id'], result_for(task))
    assert restarted.next_task(book)['input']['context_diagnostics']['mode'] == 'off'


def test_invalid_library_policy_cannot_silently_fall_back_to_shadow(tmp_path):
    service, book = make(tmp_path)
    (service.store.root / 'context-policy.json').write_text('{broken')
    with pytest.raises(StoryError, match='上下文'):
        service.next_task(book)


def test_shadow_tiny_capacity_adds_no_new_lease_block_and_off_rolls_back(tmp_path, monkeypatch):
    monkeypatch.setenv('HULK_CONTEXT_WINDOW_TOKENS', '1')
    service, book = make(tmp_path)
    task = service.next_task(book)
    assert task.get('task_id')
    assert task['input']['context_diagnostics']['would_exceed_capacity']
    service.submit_task(task['task_id'], task['lease_id'], result_for(task))
    monkeypatch.setenv('HULK_CONTEXT_MODE', 'off')
    assert service.next_task(book)['input']['context_diagnostics']['mode'] == 'off'


def test_adaptive_overflow_stops_before_lease_and_exposes_diagnostics(tmp_path, monkeypatch):
    monkeypatch.setenv('HULK_CONTEXT_MODE', 'adaptive')
    monkeypatch.setenv('HULK_CONTEXT_WINDOW_TOKENS', '1000')
    service, book = make(tmp_path)
    result = service.next_task(book)
    assert not result.get('task_id')
    assert result['reason'] == 'context_capacity'
    blocker = service.status(book)['blocker']
    assert blocker['code'] == 'CONTEXT_CAPACITY'
    assert blocker['context']['output_reserve'] == 12000


def test_provider_counts_actual_system_schema_without_sending_diagnostics():
    sent = []
    def respond(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={'usage': {'total_tokens': 20}, 'choices': [{'finish_reason': 'stop', 'message': {'content': '{}'}}]})
    provider = OpenAICompatible('http://localhost/v1', 'fixture', 'secret', client=httpx.Client(transport=httpx.MockTransport(respond)))
    provider.generate({'input': {'instruction': '写作', 'context_manifest': {'large': 'display'}}, 'output_schema': {}, 'stage': 'draft'})
    assert 'context_manifest' not in sent[0]['messages'][1]['content']
    assert 'context_diagnostics' not in sent[0]['messages'][1]['content']
    diag = provider.last_context
    from story_core.context_compiler import ConservativeCounter
    assert diag['final_tokens'] == ConservativeCounter().count(dumps({'messages': sent[0]['messages']})).tokens
    assert diag['output_reserve'] == sent[0]['max_tokens']


def test_provider_final_capacity_check_precedes_transport(monkeypatch):
    monkeypatch.setenv('HULK_CONTEXT_MODE', 'adaptive')
    monkeypatch.setenv('HULK_CONTEXT_WINDOW_TOKENS', '1000')
    def unexpected(_):
        raise AssertionError('must not send over-capacity request')
    provider = OpenAICompatible('http://localhost/v1', 'fixture', 'secret', client=httpx.Client(transport=httpx.MockTransport(unexpected)))
    with pytest.raises(StoryError) as caught:
        provider.generate({'input': {'instruction': '写作'}, 'output_schema': {}, 'stage': 'draft'})
    assert caught.value.code == 'CONTEXT_CAPACITY'


def test_telemetry_distinguishes_cleanup_truncation_and_unknown_recall(tmp_path):
    service, book = make(tmp_path)
    class Truncated:
        def generate(self, task):
            self.last_usage = 83
            self.last_context = task['input']['context_diagnostics']
            raise StoryError('TRUNCATED_OUTPUT', 'test')
    with pytest.raises(StoryError):
        run_worker(service, book, Truncated())
    status = service.status(book)
    assert status['context_usage']['measured_requests'] == 1
    assert status['context_usage']['truncated_outputs'] == 1
    assert status['context_usage']['key_fact_omission_rate'] is None
    assert status['token_usage']['by_chapter'][0]['reported_tokens'] == 83
    assert status['token_usage']['calls'][0]['context']['stage'] == 'brief'


def test_adaptive_service_projects_explicit_verified_state_and_scheduled_promises(tmp_path, monkeypatch):
    from test_memory import seed
    from story_core.long_memory import register_entity, record_fact, schedule_promise
    service, book = make(tmp_path, chapters=3)
    outline = to_stage(service, book, 'outline')
    service.submit_task(outline['task_id'], outline['lease_id'], result_for(outline))
    version = seed(service.store, book, 1, '沈知秋将铜牌借给商人。')
    with service.store.write(book) as conn:
        entity = register_entity(conn, book, '沈知秋')
        fact = record_fact(conn, book, entity, '铜牌归属', '商人', source_chapter=1, source_version=version,
                           evidence='铜牌借给商人', story_valid_from=10, verified=True)
        promise = schedule_promise(conn, book, '归还铜牌', due_chapter=2, visibility='reader', source_chapter=1, source_version=version)
        plan = json.loads(conn.execute('SELECT plan FROM books WHERE id=?', (book,)).fetchone()[0])
        plan['chapters'][1].update(entity_ids=[entity], required_fact_ids=[fact], promise_ids=[promise], story_time=20)
        conn.execute('UPDATE books SET plan=? WHERE id=?', (dumps(plan), book))
        conn.execute("UPDATE runs SET chapter_number=2,stage='draft' WHERE book_id=?", (book,))
    monkeypatch.setenv('HULK_CONTEXT_MODE', 'adaptive')
    monkeypatch.setenv('HULK_CONTEXT_WINDOW_TOKENS', '64000')
    task = service.next_task(book)
    assert task.get('task_id'), task
    assert task['input']['current_state'][0]['fact_id'] == fact
    assert task['input']['scheduled_promises'][0]['promise_id'] == promise
    assert task['input']['context_diagnostics']['missing_hard_ids'] == []


def test_service_never_under_reserves_builtin_transport_output(tmp_path, monkeypatch):
    monkeypatch.setenv('HULK_CONTEXT_MODE', 'adaptive')
    monkeypatch.setenv('HULK_CONTEXT_WINDOW_TOKENS', '64000')
    monkeypatch.setenv('HULK_CONTEXT_OUTPUT_TOKENS', '1')
    service, book = make(tmp_path)
    task = service.next_task(book)
    assert task['input']['context_diagnostics']['output_reserve'] >= 12000


def test_adaptive_outline_retains_confirmed_cast(tmp_path, monkeypatch):
    monkeypatch.setenv('HULK_CONTEXT_MODE', 'adaptive')
    monkeypatch.setenv('HULK_CONTEXT_WINDOW_TOKENS', '64000')
    service, book = make(tmp_path)
    brief_task = service.next_task(book)
    brief = result_for(brief_task)
    service.submit_task(brief_task['task_id'], brief_task['lease_id'], brief)
    outline = service.next_task(book)
    assert outline['stage'] == 'outline'
    assert outline['input']['brief']['characters'] == brief['characters']


@pytest.mark.parametrize('mode', ['adaptive', 'shadow', 'off'])
def test_claude_unspecified_model_checks_profile_before_execute(tmp_path, monkeypatch, mode):
    from story_core.host import ClaudeCode
    monkeypatch.setenv('HULK_CONTEXT_MODE', mode)
    monkeypatch.setenv('HULK_CONTEXT_WINDOW_TOKENS', '64000')
    monkeypatch.setenv('HULK_CONTEXT_MODEL_WINDOWS', '{"tiny":1000}')
    monkeypatch.setenv('HULK_HOST_MODEL', '')
    monkeypatch.setattr('story_core.host.shutil.which', lambda _: '/fixture/claude')
    provider = ClaudeCode(host_options={'transport': 'cli'})
    service, book = make(tmp_path)
    task = service.next_task(book)
    calls = []
    def execute(*args):
        calls.append(args)
        return json.dumps({'subtype': 'success', 'structured_output': {}})
    monkeypatch.setattr(provider, '_execute', execute)
    if mode == 'adaptive':
        with pytest.raises(StoryError, match='尚未识别当前模型'):
            provider.generate(task)
        assert calls == []
    else:
        assert provider.generate(task) == {}
        assert len(calls) == 1
    assert provider.last_context['context_window'] is None


def test_metrics_include_blocked_compilations_and_successful_mixture(tmp_path, monkeypatch):
    monkeypatch.setenv('HULK_CONTEXT_MODE', 'adaptive')
    monkeypatch.setenv('HULK_CONTEXT_WINDOW_TOKENS', '1000')
    service, book = make(tmp_path)
    assert service.next_task(book)['reason'] == 'context_capacity'
    usage = service.status(book)['context_usage']
    assert usage['measured_requests'] == 1
    assert usage['organized_unexecutable'] == 1
    assert usage['organized_unexecutable_rate'] == 1
    assert usage['operational_blocked_requests'] == 1
    assert usage['operational_blocked_gates'] == {'context_capacity': 1}
    monkeypatch.setenv('HULK_CONTEXT_WINDOW_TOKENS', '64000')
    service.control(book, 'resume')
    assert service.next_task(book).get('task_id')
    usage = service.status(book)['context_usage']
    assert usage['measured_requests'] == 2
    assert usage['organized_unexecutable_rate'] == 0.5


def test_metrics_separate_missing_dependencies_unknown_capacity_and_shadow(tmp_path):
    from story_core.context_compiler import ContextCompiler, ContextPolicy
    service, book = make(tmp_path)
    scenarios = [
        ('adaptive', None, {'context_required_ids': ['missing']}),
        ('adaptive', None, {}),
        ('shadow', 1000, {}),
        ('shadow', 64000, {'supplementary_memory': [{'value': '旧事' * 20000}]}),
    ]
    with service.store.write(book) as conn:
        for mode, capacity, data in scenarios:
            compiled = ContextCompiler(ContextPolicy(mode=mode, context_window=capacity)).compile(data, {}, stage='draft')
            kind = 'context_compiled' if compiled.executable else 'context_blocked'
            service.store.event(conn, book, kind, {'context': compiled.diagnostics})
    usage = service.status(book)['context_usage']
    assert usage['measured_requests'] == 4
    assert usage['organization_triggers'] == 1
    assert usage['organized_unexecutable'] == 3
    assert usage['assessed_executability_requests'] == 3
    assert usage['organized_unexecutable_rate'] == 1
    assert usage['unknown_capacity_requests'] == 2
    assert usage['missing_hard_dependency_requests'] == 1
    assert usage['capacity_overflow_requests'] == 2
    assert usage['operational_blocked_requests'] == 2
    assert usage['key_fact_omission_rate'] is None


def test_metrics_separate_final_transport_capacity_failure_from_lease(tmp_path, monkeypatch):
    monkeypatch.setenv('HULK_CONTEXT_MODE', 'adaptive')
    monkeypatch.setenv('HULK_CONTEXT_WINDOW_TOKENS', '64000')
    monkeypatch.setenv('HULK_CONTEXT_MODEL_WINDOWS', '{"tiny":1000}')
    service, book = make(tmp_path)
    provider = OpenAICompatible('http://localhost/v1', 'tiny', 'secret', client=httpx.Client(
        transport=httpx.MockTransport(lambda _: pytest.fail('capacity failure must precede network'))))
    with pytest.raises(StoryError):
        run_worker(service, book, provider)
    usage = service.status(book)['context_usage']
    assert usage['measured_requests'] == 1
    assert usage['organized_unexecutable'] == 0
    assert usage['transport_measured_calls'] == 1
    assert usage['transport_context_blocked_calls'] == 1
    assert usage['transport_context_blocked_rate'] == 1
    assert usage['transport_blocked_reasons'] == {'model_capacity': 1}


def test_network_failure_is_not_context_capacity_failure(tmp_path):
    service, book = make(tmp_path)
    class NetworkFailed:
        def generate(self, task):
            self.last_context = task['input']['context_diagnostics']
            raise StoryError('PROVIDER_ERROR', 'synthetic network failure')
    with pytest.raises(StoryError):
        run_worker(service, book, NetworkFailed())
    usage = service.status(book)['context_usage']
    assert usage['transport_measured_calls'] == 1
    assert usage['transport_context_blocked_calls'] == 0
    assert usage['transport_context_blocked_rate'] == 0
