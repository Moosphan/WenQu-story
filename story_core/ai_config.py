"""Local AI connection settings. Secrets are held by the operating system keychain."""
import json
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from .errors import StoryError

KEYCHAIN_SERVICE = 'io.hulk-story-agent.api-key'
PROVIDERS = {
    'openai': {'label': 'ChatGPT / OpenAI API', 'mode': 'api', 'base_url': 'https://api.openai.com/v1', 'model': 'gpt-5.2'},
    'claude_code': {'label': 'Claude Code（本机登录）', 'mode': 'claude', 'base_url': '', 'model': ''},
    'kimi': {'label': 'Kimi / Moonshot', 'mode': 'api', 'base_url': 'https://api.moonshot.cn/v1', 'model': 'kimi-k2'},
    'deepseek': {'label': 'DeepSeek', 'mode': 'api', 'base_url': 'https://api.deepseek.com/v1', 'model': 'deepseek-v4-pro'},
    'minimax': {'label': 'MiniMax', 'mode': 'api', 'base_url': 'https://api.minimaxi.com/v1', 'model': 'MiniMax-M2.7'},
    'glm': {'label': '智谱 GLM', 'mode': 'api', 'base_url': 'https://open.bigmodel.cn/api/paas/v4', 'model': 'glm-4.7'},
    'grok': {'label': 'Grok / xAI', 'mode': 'api', 'base_url': 'https://api.x.ai/v1', 'model': 'grok-4'},
    'custom': {'label': '自定义 OpenAI 兼容接口', 'mode': 'api', 'base_url': '', 'model': ''},
}

# The catalogue is intentionally local and conservative. It is a convenient picker,
# not a claim that every account has access to every model.
MODEL_CATALOG = {
    'openai': ('gpt-5.2', 'gpt-5.1', 'gpt-4.1', 'o3'),
    'claude_code': ('claude-opus-4-6', 'claude-sonnet-4-5', 'claude-haiku-4-5'),
    'kimi': ('kimi-k2', 'kimi-k2-thinking'),
    'deepseek': ('deepseek-v4-pro', 'deepseek-v4-flash', 'deepseek-chat', 'deepseek-reasoner'),
    'minimax': ('MiniMax-M2.7', 'MiniMax-M2.5'),
    'glm': ('glm-4.7', 'glm-4.6', 'glm-4-plus'),
    'grok': ('grok-4', 'grok-4-fast-reasoning'),
    'custom': (),
}


def default_path():
    override = os.environ.get('HULK_AI_CONFIG_PATH')
    if override:
        return Path(override).expanduser()
    if os.name == 'posix' and Path('/System/Library/CoreServices').exists():
        return Path.home() / 'Library' / 'Application Support' / 'Hulk Story' / 'ai.json'
    return Path.home() / '.config' / 'hulk-story' / 'ai.json'


class MacKeychain:
    def get(self, account):
        try:
            result = subprocess.run(['security', 'find-generic-password', '-s', KEYCHAIN_SERVICE, '-a', account, '-w'],
                                    check=False, capture_output=True, text=True, timeout=8)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return result.stdout.rstrip('\n') if result.returncode == 0 and result.stdout else None

    def set(self, account, value):
        try:
            result = subprocess.run(['security', 'add-generic-password', '-U', '-s', KEYCHAIN_SERVICE, '-a', account, '-w', value],
                                    check=False, capture_output=True, text=True, timeout=8)
        except (OSError, subprocess.TimeoutExpired):
            raise StoryError('KEYCHAIN_UNAVAILABLE', '无法写入系统钥匙串；请确认 macOS“钥匙串访问”可用。') from None
        if result.returncode != 0:
            raise StoryError('KEYCHAIN_UNAVAILABLE', '无法写入系统钥匙串；API Key 未保存。')


def _valid_url(value):
    parsed = urlparse(value)
    return parsed.scheme == 'https' and bool(parsed.hostname) and not parsed.username and not parsed.password


def _valid_model(value):
    return isinstance(value, str) and 1 <= len(value.strip()) <= 160 and all(char.isprintable() for char in value)


def _clean_model(value):
    if not isinstance(value, str):
        return ''
    # Claude settings may use a local UI suffix such as "model[1m]".
    return re.sub(r'\[[^\]]*\]$', '', value.strip())


def _append_unique(values, value):
    if value and value not in values:
        values.append(value)


class AISettings:
    def __init__(self, path=None, vault=None, home=None):
        self.path = Path(path) if path else default_path()
        self.vault = vault or MacKeychain()
        self.home = Path(home) if home else Path.home()

    def _read(self):
        try:
            value = json.loads(self.path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            raise StoryError('AI_CONFIG_INVALID', '本地 AI 配置无法读取，请重新保存配置。') from None
        if not isinstance(value, dict) or value.get('provider') not in PROVIDERS or value.get('mode') not in ('api', 'claude'):
            raise StoryError('AI_CONFIG_INVALID', '本地 AI 配置无效，请重新保存配置。')
        return value

    def _local_claude(self):
        """Read only the selected model from local Claude settings; never expose credentials."""
        models = []
        try:
            value = json.loads((self.home / '.claude' / 'settings.json').read_text(encoding='utf-8'))
            environment = value.get('env', {}) if isinstance(value, dict) else {}
            if isinstance(environment, dict):
                _append_unique(models, _clean_model(environment.get('ANTHROPIC_MODEL', '')))
        except (OSError, ValueError, TypeError):
            pass
        for name in ('HULK_HOST_MODEL', 'HULK_HOST_REVISION_MODEL', 'HULK_HOST_AUXILIARY_MODEL'):
            _append_unique(models, _clean_model(os.environ.get(name, '')))
        return {'available': bool(models), 'model': models[0] if models else '', 'models': models}

    def _providers_public(self):
        local = self._local_claude()
        result = []
        for provider_id, preset in PROVIDERS.items():
            models = []
            if provider_id == 'claude_code':
                for local_model in local['models']:
                    _append_unique(models, local_model)
            for model in MODEL_CATALOG[provider_id]:
                _append_unique(models, model)
            if preset['model']:
                _append_unique(models, preset['model'])
            result.append({'id': provider_id, **preset, 'models': models})
        return result, local

    def public(self):
        settings = self._read()
        providers, local = self._providers_public()
        base = {'providers': providers, 'local_claude': local}
        if not settings:
            return {'configured': False, **base}
        provider = settings['provider']
        return {'configured': True, 'provider': provider, 'mode': settings['mode'],
                'base_url': settings.get('base_url', ''), 'model': settings.get('model', ''),
                'host_options': settings.get('host_options', {}),
                'key_configured': bool(self.vault.get(provider)) if settings['mode'] == 'api' else True,
                **base}

    def save(self, provider, base_url='', model='', api_key='', host_options=None):
        if provider not in PROVIDERS:
            raise StoryError('INVALID_PROVIDER', '请选择受支持的平台或自定义兼容接口。')
        preset = PROVIDERS[provider]
        mode = preset['mode']
        if mode == 'claude':
            model = _clean_model(model)
            if model and not _valid_model(model):
                raise StoryError('INVALID_PROVIDER', '模型名称需要为 1–160 个可见字符。')
            previous = self._read() or {}
            options = host_options if host_options is not None else previous.get('host_options', {}) if previous.get('provider') == provider else {}
            if not isinstance(options, dict) or set(options) - {'transport', 'thinking', 'revision_model', 'auxiliary_model'}:
                raise StoryError('INVALID_PROVIDER', '宿主连接选项无效。')
            if options.get('transport', 'cli') not in ('cli', 'native_deepseek') or options.get('thinking', 'inherit') not in ('inherit', 'off'):
                raise StoryError('INVALID_PROVIDER', '宿主传输或思考模式无效。')
            for field in ('revision_model', 'auxiliary_model'):
                if field in options and not _valid_model(options[field]):
                    raise StoryError('INVALID_PROVIDER', '宿主阶段模型名无效。')
            data = {'provider': provider, 'mode': mode, 'base_url': '', 'model': model, 'host_options': options}
        else:
            base_url = (base_url or preset['base_url']).strip().rstrip('/')
            model = (model or preset['model']).strip()
            if not _valid_url(base_url):
                raise StoryError('INVALID_PROVIDER', '模型接口必须是 HTTPS 地址，例如 https://api.example.com/v1。')
            if not _valid_model(model):
                raise StoryError('INVALID_PROVIDER', '模型名称需要为 1–160 个可见字符。')
            existing = self.vault.get(provider)
            if api_key:
                if not isinstance(api_key, str) or len(api_key) > 4096 or any(not char.isprintable() for char in api_key):
                    raise StoryError('INVALID_PROVIDER', 'API Key 格式无效。')
                self.vault.set(provider, api_key)
            elif not existing:
                raise StoryError('API_KEY_REQUIRED', '请填写 API Key；密钥只会写入本机系统钥匙串。')
            data = {'provider': provider, 'mode': mode, 'base_url': base_url, 'model': model}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        try:
            self.path.chmod(0o600)
        except OSError:
            pass
        return self.public()

    def runtime(self):
        settings = self._read()
        if not settings:
            return None
        if settings['mode'] == 'api':
            key = self.vault.get(settings['provider'])
            if not key:
                raise StoryError('API_KEY_REQUIRED', 'AI 配置缺少 API Key；请在“AI 配置”中重新保存。')
            return {**settings, 'api_key': key}
        return settings
