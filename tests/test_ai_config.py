import json

import pytest

from story_core.ai_config import AISettings
from story_core.errors import StoryError


class Vault:
    def __init__(self): self.values = {}
    def get(self, account): return self.values.get(account)
    def set(self, account, value): self.values[account] = value


def test_ai_settings_keeps_api_key_out_of_config_file(tmp_path):
    settings = AISettings(tmp_path / 'ai.json', Vault())
    public = settings.save('deepseek', model='deepseek-v4-flash', api_key='sk-local-secret')
    assert public['configured'] is True and public['key_configured'] is True
    assert 'sk-local-secret' not in (tmp_path / 'ai.json').read_text()
    assert settings.runtime()['api_key'] == 'sk-local-secret'
    assert settings.public()['model'] == 'deepseek-v4-flash'


def test_ai_settings_requires_key_for_api_but_not_claude_code(tmp_path):
    settings = AISettings(tmp_path / 'ai.json', Vault())
    with pytest.raises(StoryError) as error:
        settings.save('openai', model='gpt-5.2')
    assert error.value.code == 'API_KEY_REQUIRED'
    assert settings.save('claude_code')['mode'] == 'claude'


def test_saved_api_configuration_is_used_by_provider(tmp_path):
    from story_core.providers import configured_provider

    vault = Vault(); settings = AISettings(tmp_path / 'ai.json', vault)
    settings.save('deepseek', model='deepseek-v4-flash', api_key='sk-local-secret')
    provider = configured_provider(settings=settings)
    assert provider.url == 'https://api.deepseek.com/v1/chat/completions'
    assert provider.model == 'deepseek-v4-flash'


def test_ai_settings_exposes_catalog_and_local_claude_models_without_secrets(tmp_path, monkeypatch):
    claude = tmp_path / '.claude'
    claude.mkdir()
    (claude / 'settings.json').write_text(json.dumps({'env': {
        'ANTHROPIC_AUTH_TOKEN': 'never-expose-me',
        'ANTHROPIC_MODEL': 'deepseek-v4-pro[1m]',
    }}), encoding='utf-8')
    monkeypatch.setenv('HULK_HOST_AUXILIARY_MODEL', 'deepseek-v4-flash')
    settings = AISettings(tmp_path / 'ai.json', Vault(), home=tmp_path)

    public = settings.public()

    providers = {item['id']: item for item in public['providers']}
    assert 'gpt-5.2' in providers['openai']['models']
    assert providers['claude_code']['models'][:2] == ['deepseek-v4-pro', 'deepseek-v4-flash']
    assert public['local_claude']['available'] is True
    assert public['local_claude']['model'] == 'deepseek-v4-pro'
    assert 'never-expose-me' not in json.dumps(public)


def test_saved_claude_model_is_reused_by_host_provider(tmp_path, monkeypatch):
    from story_core.providers import configured_provider
    import story_core.host as host

    settings = AISettings(tmp_path / 'ai.json', Vault())
    settings.save('claude_code', model='deepseek-v4-pro')
    captured = {}

    class FakeClaude:
        def __init__(self, model=None): captured['model'] = model

    monkeypatch.setattr(host, 'ClaudeCode', FakeClaude)
    configured_provider(settings=settings)
    assert captured['model'] == 'deepseek-v4-pro'
