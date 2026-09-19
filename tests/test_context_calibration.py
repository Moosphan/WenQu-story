import pytest


def test_usage_separates_input_output_and_cache_without_double_counting():
    from story_core.context_calibration import usage_breakdown
    usage = usage_breakdown({'prompt_tokens': 100, 'completion_tokens': 20,
        'prompt_tokens_details': {'cached_tokens': 60}, 'total_tokens': 120}, 'compatible')
    assert usage['input_tokens'] == 100
    assert usage['cached_input_tokens'] == 60
    assert usage['output_tokens'] == 20
    native = usage_breakdown({'input_tokens': 40, 'cache_read_input_tokens': 50,
        'cache_creation_input_tokens': 10, 'output_tokens': 20}, 'native')
    assert native['input_tokens'] == 100
    assert usage_breakdown({'total_tokens': 120}, 'compatible')['input_tokens'] is None
    assert usage_breakdown({'prompt_tokens': True}, 'compatible')['input_tokens'] is None


def test_calibration_groups_exact_transport_model_counter_and_deduplicates():
    from story_core.context_calibration import calibration_report
    row = {'call_id': 'one', 'executor': 'api', 'context': {'model': 'test',
        'counter': 'counter-a', 'final_tokens': 100, 'fingerprint': 'abc'},
        'usage_breakdown': {'input_tokens': 150, 'output_tokens': 20}, 'status': 'failed'}
    report = calibration_report([row, row, {**row, 'call_id': 'two', 'context': {**row['context'], 'model': 'other'}}])
    assert report['unique_calls'] == 2
    assert report['duplicate_calls'] == 1
    assert len(report['groups']) == 2
    assert report['groups'][0]['max_actual_to_estimate'] == 1.5
    assert report['groups'][0]['underestimated_calls'] == 1
    assert report['policy_changed'] is False
    assert 'abc' not in str(report)


def test_incomplete_or_conflicting_usage_never_becomes_successful_calibration():
    from story_core.context_calibration import calibration_report
    row = {'call_id': 'one', 'executor': 'api', 'context': {'model': 'test',
        'counter': 'counter-a', 'final_tokens': 100, 'fingerprint': 'abc'},
        'usage_breakdown': {'input_tokens': 50}, 'reported_tokens': 80}
    report = calibration_report([row, {**row, 'usage_breakdown': {'input_tokens': 99}},
        {'call_id': 'two', 'reported_tokens': 100}, {**row, 'call_id': 'three', 'usage_breakdown': {'input_tokens': None}}])
    assert report['conflicting_calls'] == 1
    assert report['eligible_calls'] == 0
    assert report['groups'] == []


def test_provider_keeps_usage_breakdown_even_when_output_truncated():
    import httpx
    from story_core.providers import OpenAICompatible
    from story_core.errors import StoryError
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
        'usage': {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120},
        'choices': [{'finish_reason': 'length', 'message': {'content': '{}'}}]})))
    provider = OpenAICompatible('http://localhost/v1', 'test', 'fake', client=client)
    with pytest.raises(StoryError):
        provider.generate({'input': {}, 'output_schema': {}})
    assert provider.last_usage_breakdown['input_tokens'] == 100
    assert provider.last_usage == 120


def test_worker_persists_each_call_breakdown_and_resets_unknown_usage(tmp_path):
    import json
    from story_core.providers import run_worker
    from story_core.errors import StoryError
    from test_workflow import make, result_for
    service, book = make(tmp_path)

    class Provider:
        count = 0

        def generate(self, task):
            self.count += 1
            if self.count == 2:
                raise StoryError('PROVIDER_ERROR', 'synthetic failure')
            self.last_usage_breakdown = {'input_tokens': 100, 'output_tokens': 20, 'cached_input_tokens': 10}
            self.last_usage = 120
            return result_for(task)

    with pytest.raises(StoryError):
        run_worker(service, book, Provider())
    with service.store.read() as conn:
        rows = [json.loads(row[0]) for row in conn.execute("SELECT payload FROM events WHERE book_id=? AND kind='provider_usage' ORDER BY seq", (book,))]
    assert rows[0]['usage_breakdown']['input_tokens'] == 100
    assert rows[1]['usage_breakdown'] is None
    assert service.status(book)['token_usage']['book_total'] == 120


def test_cli_calibrates_only_supplied_metadata_and_preserves_input(tmp_path):
    import json
    from story_core.context_calibration import main
    source, destination = tmp_path / 'usage.jsonl', tmp_path / 'report.json'
    row = {'call_id': 'one', 'executor': 'api', 'context': {'model': 'test',
        'counter': 'counter-a', 'final_tokens': 100, 'fingerprint': 'abc'},
        'usage_breakdown': {'input_tokens': 120}, 'input': {'body': 'PRIVATE FIXTURE'}}
    original = json.dumps(row)
    source.write_text(original)
    main(['--input', str(source), '--output', str(destination)])
    report = json.loads(destination.read_text())
    assert report['eligible_calls'] == 1
    assert 'PRIVATE FIXTURE' not in destination.read_text()
    assert source.read_text() == original
    with pytest.raises(SystemExit):
        main(['--input', str(source), '--output', str(source)])


def test_native_partial_cache_and_inconsistent_compatible_totals_are_unknown():
    from story_core.context_calibration import usage_breakdown
    assert usage_breakdown({'input_tokens': 10, 'output_tokens': 20}, 'native')['input_tokens'] is None
    assert usage_breakdown({'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 90}, 'compatible')['input_tokens'] is None
    assert usage_breakdown({'prompt_tokens': 100, 'prompt_tokens_details': {'cached_tokens': 200}}, 'compatible')['input_tokens'] is None


def test_host_aggregate_or_unknown_model_is_not_misattributed():
    from story_core.context_calibration import calibration_report
    row = {'call_id': 'one', 'executor': 'claude', 'context': {'model': 'alias',
        'counter': 'counter-a', 'final_tokens': 100, 'fingerprint': 'abc'},
        'usage_breakdown': {'input_tokens': 120}, 'execution': {'models': ['actual-one', 'actual-two']}}
    assert calibration_report([row])['eligible_calls'] == 0
    row['execution']['models'] = ['actual-one']
    assert calibration_report([row])['eligible_calls'] == 0
    row['context']['model'] = 'actual-one'
    assert calibration_report([row])['eligible_calls'] == 1


def test_calibration_reports_observed_overhead_and_output_coverage_without_claiming_safety():
    from story_core.context_calibration import calibration_report
    rows = [{'call_id': str(i), 'executor': 'api',
             'context': {'model': 'm', 'counter': 'local-tokenizer-envelope-estimate:fixture',
                         'final_tokens': 100, 'fingerprint': str(i), 'stage': stage,
                         'output_reserve': 12000},
             'usage_breakdown': {'input_tokens': actual, 'output_tokens': output},
             'status': status, 'error': error}
            for i, (stage, actual, output, status, error) in enumerate([
                ('draft', 150, 12000, 'failed', 'TRUNCATED_OUTPUT'),
                ('reader', 110, 300, 'completed', None)])]
    group = calibration_report(rows)['groups'][0]
    assert group['observed_extra_input_tokens'] == 50
    assert group['stages'] == ['draft', 'reader']
    assert group['output_samples'] == 2
    assert group['max_output_tokens'] == 12000
    assert group['outputs_at_reserve'] == 1
    assert group['capacity_validated'] is False


def test_conflicting_output_measurements_exclude_entire_call():
    from story_core.context_calibration import calibration_report
    row = {'call_id': 'one', 'executor': 'api', 'context': {'model': 'm', 'counter': 'c',
           'final_tokens': 100, 'fingerprint': 'x'}, 'usage_breakdown': {'input_tokens': 80, 'output_tokens': 10}}
    other = {**row, 'usage_breakdown': {'input_tokens': 80, 'output_tokens': 20}}
    assert calibration_report([row, other])['eligible_calls'] == 0
