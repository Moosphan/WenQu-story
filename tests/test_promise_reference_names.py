from story_core.context_compiler import organize


def test_legacy_promise_name_keeps_existing_evidence():
    core, _, missing = organize({
        'chapter_number': 21,
        'chapter_plan': {'promise_ids': ['旧账']},
        'context_required_ids': ['旧账'],
        'required_memory': [{'kind': 'promise', 'key': '旧账', 'status': 'open',
                             'context_reason': 'participant', 'evidence': '封存旧账'}],
    })
    assert missing == []
    assert core['required_memory'][0]['evidence'] == '封存旧账'


def test_fact_names_do_not_satisfy_explicit_fact_ids():
    _, _, missing = organize({'chapter_plan': {'required_fact_ids': ['旧账']},
        'required_memory': [{'kind': 'fact', 'key': '旧账', 'value': '存在'}]})
    assert missing == ['旧账']


def test_planned_promise_name_is_kept_even_before_due_date():
    core, _, missing = organize({'chapter_number': 21,
        'chapter_plan': {'promise_ids': ['旧账']},
        'planned_promises': [{'key': '旧账', 'setup_chapter': 1, 'due_chapter': 198}]})
    assert missing == []
    assert core['planned_promises'][0]['key'] == '旧账'
