import hashlib

import pytest

from story_core.errors import StoryError


def tokenizer_profile(tmp_path):
    from tokenizers import Tokenizer, models, pre_tokenizers
    tokenizer = Tokenizer(models.WordLevel({'[UNK]': 0, 'one': 1, 'two': 2}, unk_token='[UNK]'))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    tokenizer.enable_truncation(max_length=1)
    tokenizer.enable_padding(length=20)
    path = tmp_path / 'tokenizer.json'
    tokenizer.save(str(path))
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def test_local_tokenizer_never_truncates_or_pads_measurement(tmp_path):
    from story_core.token_counting import counter_for_model
    profile = tokenizer_profile(tmp_path)
    counter = counter_for_model({'model-a': profile}, 'model-a')
    count = counter.count('one two one')
    assert count.tokens == 3
    assert count.estimated is True  # Transport wrappers remain provider-specific.
    assert profile['sha256'] in count.method


def test_unconfigured_model_keeps_conservative_counter(tmp_path):
    from story_core.token_counting import counter_for_model
    count = counter_for_model({'model-a': tokenizer_profile(tmp_path)}, 'model-b').count('中文')
    assert count.tokens == len('中文'.encode())
    assert count.method == 'utf8-byte-upper-bound-uncalibrated'


def test_changed_tokenizer_artifact_is_rejected(tmp_path):
    from story_core.token_counting import counter_for_model
    profile = tokenizer_profile(tmp_path)
    profile['sha256'] = '0' * 64
    with pytest.raises(StoryError) as error:
        counter_for_model({'model-a': profile}, 'model-a')
    assert error.value.code == 'INVALID_TOKENIZER_PROFILE'


def test_actual_model_selects_saved_profile_and_preserves_reserves(tmp_path):
    from story_core.context_compiler import ContextCompiler, ContextPolicy, compile_task
    profile = tokenizer_profile(tmp_path)
    policy = ContextPolicy(mode='adaptive', context_window=100000,
                           tokenizer_profiles={'model-a': profile})
    data = {'instruction': 'one two one'}
    compiled = ContextCompiler(policy).compile(data, {}, stage='draft')
    task = {'stage': 'draft', 'input': {**compiled.input, 'context_diagnostics': compiled.diagnostics}, 'output_schema': {}}
    transport = compile_task(task, model='model-a', output_reserve=12000)
    assert profile['sha256'] in transport.diagnostics['counter']
    assert transport.reservation == transport.diagnostics['final_tokens'] + 12000 + 4000
    assert compile_task(task, model='model-b').diagnostics['counter'] == 'utf8-byte-upper-bound-uncalibrated'


def test_invalid_profile_configuration_is_rejected_before_leasing():
    from story_core.context_compiler import ContextPolicy
    with pytest.raises(StoryError):
        ContextPolicy(tokenizer_profiles={'model': {'path': 'relative.json', 'sha256': 'bad'}})


def test_selected_preflight_counter_applies_before_optional_evidence_is_evicted(tmp_path):
    from story_core.context_compiler import ContextCompiler, ContextPolicy
    profile = tokenizer_profile(tmp_path)
    policy = ContextPolicy(mode='adaptive', context_window=17000, soft_target=1000,
                           tokenizer_profiles={'model-a': profile}, counting_model='model-a')
    # Byte fallback exceeds the available input window; local word count fits.
    result = ContextCompiler(policy).compile({'instruction': 'one ' * 600}, {}, stage='draft')
    assert result.executable
    assert profile['sha256'] in result.diagnostics['counter']
    with pytest.raises(StoryError):
        ContextPolicy(tokenizer_profiles={'model-a': profile}, counting_model='model-b')


def test_preflight_uses_selected_models_capacity_instead_of_generic_window(tmp_path):
    from story_core.context_compiler import ContextCompiler, ContextPolicy
    profile = tokenizer_profile(tmp_path)
    policy = ContextPolicy(mode='adaptive', context_window=1,
        model_windows={'model-a': 100000}, tokenizer_profiles={'model-a': profile}, counting_model='model-a')
    compiled = ContextCompiler(policy).compile({'instruction': 'one'}, {}, stage='draft')
    assert compiled.executable
    assert compiled.diagnostics['context_window'] == 100000
