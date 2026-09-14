from story_core.storage import dumps


def test_offline_long_history_replay_is_bounded_and_keeps_labeled_dependencies():
    from story_core.context_replay import synthetic_samples, replay
    samples = list(synthetic_samples())
    result = replay(samples, context_window=64000)
    assert {row['chapters'] for row in result['samples']} == {100, 300, 500}
    assert result['summary']['sample_count'] == 21
    assert result['summary']['adaptive_missing_hard_count'] == 0
    assert result['summary']['adaptive_unexecutable_count'] == 0
    assert max(row['adaptive']['final_tokens'] for row in result['samples']) <= 24000
    by_size = {n: max(row['full_history_tokens'] for row in result['samples'] if row['chapters'] == n) for n in (100, 300, 500)}
    assert by_size[500] > by_size[300] > by_size[100]
    assert result['summary']['provider_reported_tokens'] is None
    assert result['summary']['real_model_calls'] == 0


def test_replay_report_contains_no_manuscript_and_rates_use_observations():
    from story_core.context_replay import replay
    result = replay([{'stage': 'revise', 'input': {'candidate': {'body': 'PRIVATE PROSE' * 1000}}, 'output_schema': {},
                      'reported_tokens': 57, 'truncated_output': True}], context_window=2000)
    assert 'PRIVATE PROSE' not in dumps(result)
    assert result['summary']['provider_reported_tokens'] == 57
    assert result['summary']['observed_truncated_outputs'] == 1
    assert result['summary']['adaptive_unexecutable_count'] == 1
    assert result['summary']['key_fact_omission_rate'] is None


def test_replay_extract_uses_stage_soft_target_without_live_environment(monkeypatch):
    from story_core.context_replay import replay, synthetic_samples
    monkeypatch.setenv('HULK_CONTEXT_SOFT_TOKENS', '1')
    monkeypatch.setenv('HULK_CONTEXT_MODE', 'off')
    monkeypatch.setenv('HULK_CONTEXT_WINDOW_TOKENS', '1')
    result = replay(sample for sample in synthetic_samples((100,)) if sample['stage'] == 'extract')
    row = result['samples'][0]
    assert row['shadow']['soft_target'] == 16000
    assert row['adaptive']['soft_target'] == 16000
    assert row['adaptive']['final_tokens'] <= 16000
    assert row['adaptive']['context_window'] == 64000


def test_replay_explicit_recall_labels_do_not_match_freeform_memory_keys():
    from story_core.context_replay import replay
    result = replay([{'input': {'required_memory': [{'key': 'missing-id', 'kind': 'fact', 'value': '旧事'}]},
                      'expected_hard_ids': ['missing-id']}])
    assert result['summary']['key_fact_omission_rate'] == 1
    assert result['samples'][0]['missing_expected_hard_ids'] == ['missing-id']
