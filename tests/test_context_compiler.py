import copy

import pytest


def compiler(**kwargs):
    from story_core.context_compiler import ContextCompiler, ContextPolicy
    return ContextCompiler(ContextPolicy(**kwargs))


def test_shadow_measures_final_envelope_without_changing_context_or_blocking():
    from story_core.model_context import model_input
    data = {'instruction': '规则', 'candidate': {'body': '原稿' * 1000},
            'required_memory': [{'kind': 'promise', 'key': str(i), 'status': 'open', 'value': '旧事' * 20} for i in range(100)],
            'context_manifest': {'private_display': 'a' * 10000}}
    before = copy.deepcopy(data)
    result = compiler(soft_target=1000, context_window=500, output_reserve=300).compile(data, {'large': 'schema' * 100}, stage='revise')
    assert result.input == model_input(data)
    assert result.executable
    assert result.diagnostics['would_exceed_capacity']
    assert result.diagnostics['before_tokens'] > result.diagnostics['organized_tokens']
    assert result.diagnostics['estimated'] is True
    assert data == before


def test_borrowing_soft_quota_and_elastic_hard_constraints_preserve_candidate():
    data = {'instruction': '写作', 'candidate': {'body': '正文' * 1200}, 'hard_constraints': ['必须归还铜牌' * 80],
            'supplementary_memory': [{'key': str(i), 'value': '旧事' * 300} for i in range(100)]}
    result = compiler(mode='adaptive', soft_target=1200, context_window=20000, output_reserve=1000, overhead_reserve=100).compile(data, {}, stage='revise')
    assert result.executable
    assert result.input['candidate'] == data['candidate']
    assert result.input['hard_constraints'] == data['hard_constraints']
    assert result.diagnostics['elastic_expansion']
    assert result.diagnostics['final_tokens'] + 1100 <= 20000
    assert result.diagnostics['omitted_optional_count'] > 0


def test_hard_overflow_is_explicit_and_never_truncated():
    data = {'hard_constraints': ['硬约束' * 1000], 'promise_obligations': [{'key': str(i)} for i in range(100)]}
    result = compiler(mode='adaptive', soft_target=100, context_window=1200, output_reserve=500).compile(data, {}, stage='draft')
    assert not result.executable
    assert result.input['hard_constraints'] == data['hard_constraints']
    assert result.input['promise_obligations'] == data['promise_obligations']
    assert result.diagnostics['blocked_reason'] == 'model_capacity'


def test_adaptive_requires_explicit_model_capacity_but_shadow_and_off_do_not():
    for mode in ('shadow', 'off'):
        assert compiler(mode=mode).compile({}, {}, stage='draft').executable
    result = compiler(mode='adaptive').compile({}, {}, stage='draft')
    assert not result.executable
    assert result.diagnostics['blocked_reason'] == 'unknown_model_capacity'


def test_schema_system_tools_and_output_reserve_are_counted():
    c = compiler(context_window=100000)
    plain = c.compile({'instruction': 'hello'}, {}, stage='draft')
    large = c.compile({'instruction': 'hello'}, {'enum': ['结构' * 200]}, stage='draft', system='指令' * 300, tools=[{'description': '工具' * 200}])
    assert large.diagnostics['final_tokens'] > plain.diagnostics['final_tokens'] + 2000
    assert large.reservation >= large.diagnostics['final_tokens'] + large.diagnostics['output_reserve']


def test_open_promise_or_name_match_alone_is_not_hard_and_due_refs_survive():
    data = {'chapter_number': 350, 'chapter_plan': {'participants': ['主角'], 'promise_ids': ['p12']},
            'required_memory': [
                {'kind': 'fact', 'key': str(i), 'value': '主角旧事' * 40, 'context_reason': 'participant'} for i in range(500)],
            'planned_promises': [{'promise_id': 'p12', 'key': '铜牌', 'due_chapter': 350}, {'key': '遥远', 'due_chapter': 499}],
            'promise_obligations': [{'key': '铜牌', 'urgency': 'due'}]}
    result = compiler(mode='adaptive', soft_target=4000, context_window=30000, output_reserve=1000).compile(data, {}, stage='draft')
    assert result.executable
    assert result.diagnostics['final_tokens'] <= 4000
    assert result.input['promise_obligations'] == data['promise_obligations']
    assert [p['key'] for p in result.input['planned_promises']] == ['铜牌']
    assert len(result.input.get('required_memory', [])) == 0


def test_same_request_has_stable_fingerprint_and_no_nested_diagnostics():
    data = {'candidate': {'body': '正文'}, 'context_diagnostics': {'huge': 'x' * 5000}}
    c = compiler()
    one = c.compile(data, {}, stage='extract')
    two = c.compile(one.input, {}, stage='extract')
    assert one.diagnostics['fingerprint'] == two.diagnostics['fingerprint']
    assert 'context_diagnostics' not in one.input


def test_pinned_explicit_dependencies_cannot_go_missing_silently():
    result = compiler(mode='adaptive', context_window=100000).compile(
        {'chapter_plan': {'required_fact_ids': ['fact_missing']}, 'required_memory': []}, {}, stage='draft')
    assert not result.executable
    assert result.diagnostics['missing_hard_ids'] == ['fact_missing']


def test_counter_can_be_replaced_with_exact_tokenizer():
    from story_core.context_compiler import ContextCompiler, ContextPolicy, TokenCount
    class Counter:
        def count(self, text):
            return TokenCount(len(text), False, 'test-character-tokenizer')
    result = ContextCompiler(ContextPolicy(), Counter()).compile({'x': '中文'}, {}, stage='draft')
    assert not result.diagnostics['estimated']
    assert result.diagnostics['counter'] == 'test-character-tokenizer'


def test_exact_model_profile_overrides_generic_capacity_on_transport(monkeypatch):
    from story_core.context_compiler import compile_task
    monkeypatch.setenv('HULK_CONTEXT_MODE', 'adaptive')
    monkeypatch.setenv('HULK_CONTEXT_WINDOW_TOKENS', '100000')
    monkeypatch.setenv('HULK_CONTEXT_MODEL_WINDOWS', '{"small-test":1000,"big-test":100000}')
    task = {'stage': 'draft', 'input': {'instruction': '写作'}, 'output_schema': {}}
    small = compile_task(task, model='small-test')
    assert not small.executable
    assert small.diagnostics['context_window'] == 1000
    assert compile_task(task, model='big-test').executable
    assert not compile_task(task, model='unconfigured-test').executable


def test_prepared_transport_cannot_expand_into_provider_capacity_using_stale_profile(monkeypatch):
    from story_core.context_compiler import compile_task
    first = compiler(mode='adaptive', context_window=100000).compile({'instruction': '规则' * 300}, {}, stage='draft')
    task = {'stage': 'draft', 'input': {**first.input, 'context_diagnostics': first.diagnostics}, 'output_schema': {}}
    monkeypatch.setenv('HULK_CONTEXT_MODEL_WINDOWS', '{"small-test":1000}')
    final = compile_task(task, model='small-test')
    assert not final.executable
    assert final.input == first.input


def test_settled_promises_do_not_accumulate_and_due_unsettled_is_hard():
    data = {'chapter_number': 500, 'required_memory': [
        {'kind': 'promise', 'key': 'old', 'status': 'paid', 'context_reason': 'plan_term'},
        {'kind': 'promise', 'key': 'due', 'status': 'open', 'due_chapter': 500, 'context_reason': 'unresolved_promise'}],
        'planned_promises': [{'key': 'old', 'due_chapter': 10}, {'key': 'due', 'due_chapter': 500}]}
    result = compiler(mode='adaptive', soft_target=1, context_window=64000).compile(data, {}, stage='draft')
    assert [item['key'] for item in result.input['planned_promises']] == ['due']
    assert [item['key'] for item in result.input['required_memory']] == ['due']


def test_unclassified_required_memory_is_preserved_conservatively():
    record = {'kind': 'fact', 'key': 'world_rule', 'value': '规则' * 300}
    result = compiler(mode='adaptive', soft_target=1, context_window=64000).compile({'required_memory': [record]}, {}, stage='draft')
    assert result.input['required_memory'] == [record]


def test_server_settled_metadata_filters_historical_outline_without_reaching_model():
    data = {'chapter_number': 500, 'context_selection': {'settled_promise_ids': ['old']},
            'planned_promises': [{'key': 'old', 'mandatory': True, 'due_chapter': 20}],
            'plan': {'chapters': [], 'promises': [{'key': 'old', 'mandatory': True, 'due_chapter': 20}]}}
    result = compiler(mode='adaptive', context_window=64000).compile(data, {}, stage='ending')
    assert not result.input.get('planned_promises')
    assert not result.input.get('ending_obligations')
    assert 'context_selection' not in result.input
    shadow = compiler().compile(data, {}, stage='ending')
    assert shadow.input['plan'] == data['plan']
    assert 'context_selection' not in shadow.input


@pytest.mark.parametrize('stage,plan', [('outline', None), ('draft', {}), ('draft', {'goal': '搜集线索'}),
                                      ('draft', {'participants': ['unknown'], 'pov': '限知'})])
def test_unscoped_cast_keeps_all_hard_character_constraints(stage, plan):
    cast = [{'name': '沈知秋', 'boundary': '不可杀人'}, {'name': '妹妹', 'boundary': '守信'}]
    data = {'brief': {'characters': cast}}
    if plan is not None:
        data['chapter_plan'] = plan
    result = compiler(mode='adaptive', context_window=64000).compile(data, {}, stage=stage)
    assert result.input['brief']['characters'] == cast


@pytest.mark.parametrize('prepared', [False, True])
@pytest.mark.parametrize('mode', ['adaptive', 'shadow', 'off'])
def test_unspecified_actual_model_cannot_borrow_generic_capacity(monkeypatch, prepared, mode):
    from story_core.context_compiler import compile_task
    monkeypatch.setenv('HULK_CONTEXT_MODE', mode)
    monkeypatch.setenv('HULK_CONTEXT_WINDOW_TOKENS', '64000')
    monkeypatch.setenv('HULK_CONTEXT_MODEL_WINDOWS', '{"tiny":1000}')
    data = {'instruction': '写作'}
    if prepared:
        first = compiler(mode=mode, context_window=64000).compile(data, {}, stage='draft')
        data = {**first.input, 'context_diagnostics': first.diagnostics}
    result = compile_task({'input': data, 'stage': 'draft', 'output_schema': {}}, model=None)
    assert result.executable is (mode != 'adaptive')
    assert result.diagnostics['context_window'] is None
    assert result.diagnostics['blocked_reason'] == 'unknown_model_capacity'


@pytest.mark.parametrize('reference', ['required_fact_ids', 'promise_ids', 'context_required_ids'])
def test_freeform_key_cannot_satisfy_explicit_stable_dependency(reference):
    data = {'required_memory': [{'kind': 'fact', 'key': 'fact_missing', 'value': '旧事'}]}
    if reference == 'context_required_ids':
        data[reference] = ['fact_missing']
    else:
        data['chapter_plan'] = {reference: ['fact_missing']}
    result = compiler(mode='adaptive', context_window=64000).compile(data, {}, stage='draft')
    assert not result.executable
    assert result.diagnostics['missing_hard_ids'] == ['fact_missing']
    assert result.input['required_memory'] == data['required_memory']


@pytest.mark.parametrize('id_field', ['fact_id', 'promise_id', 'id'])
def test_explicit_stable_dependency_survives_optional_eviction(id_field):
    record = {id_field: 'stable', 'key': 'text', 'value': '线索' * 100}
    data = {'context_required_ids': ['stable'], 'supplementary_memory': [record]}
    result = compiler(mode='adaptive', context_window=64000, soft_target=1).compile(data, {}, stage='draft')
    assert result.executable
    assert result.input['required_memory'] == [record]
