from story_core.storage import dumps


def test_database_sequence_covers_each_stage_at_each_chapter_without_prose_or_paid_claims():
    from story_core.memory_benchmark import benchmark
    result = benchmark(chapters=16)
    assert len(result['points']) == 16 * 7
    assert {point['chapter'] for point in result['points']} == set(range(1, 17))
    assert result['summary']['hard_labels'] == 4 * 7
    assert result['summary']['bounded_hard_missing'] == 0
    assert result['summary']['real_model_calls'] == 0
    assert result['summary']['semantic_recall'] is None
    assert result['summary']['alias_recall']['matched'] == 4
    assert result['summary']['indexed_memory_count'] == 16
    assert '铜牌借你' not in dumps(result)
    assert all('index_complete' in point for point in result['points'])
