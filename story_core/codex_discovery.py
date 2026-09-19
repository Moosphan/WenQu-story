"""Credential-free local Codex discovery. No model requests or auth-file reads."""
import os
import json
from pathlib import Path
import re
import shutil
import subprocess
import time

try:
    import tomllib
except ImportError:
    import tomli as tomllib

_cache = {}
REQUIRED_FLAGS = ('--ignore-user-config', '--ephemeral', '--output-schema', '--json')


def login_environment():
    environment = os.environ.copy()
    for name in ('CODEX_API_KEY', 'OPENAI_API_KEY', 'OPENAI_BASE_URL'):
        environment.pop(name, None)
    return environment


def model_capacity(model, home=None):
    """Use the exact CLI catalog model's default window, never its opt-in maximum."""
    root = Path(home) if home else Path.home()
    directory = Path(os.environ.get('CODEX_HOME', root / '.codex')) if root == Path.home() else root / '.codex'
    try:
        path = directory / 'models_cache.json'
        if path.stat().st_size > 8000000:
            return None
        data = json.loads(path.read_text(encoding='utf-8'))
        for entry in data.get('models', []):
            if entry.get('slug') != model:
                continue
            window = entry.get('context_window')
            percent = entry.get('effective_context_window_percent', 100)
            if type(window) is int and window > 0 and type(percent) is int and 0 < percent <= 100:
                return window * percent // 100 or None
            return None
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return None


def executable_candidates():
    override = os.environ.get('HULK_CODEX_PATH')
    if override:
        return [str(Path(override).expanduser())]
    candidates = [Path('/Applications/Codex.app/Contents/Resources/codex'),
                  Path('/Applications/ChatGPT.app/Contents/Resources/codex'),
                  Path.home() / 'Applications/Codex.app/Contents/Resources/codex']
    result = [str(path) for path in candidates if path.is_file() and os.access(path, os.X_OK)]
    on_path = shutil.which('codex')
    if on_path and on_path not in result:
        result.append(on_path)
    return result[:4]


def local_selection(home=None):
    root = Path(home) if home else Path.home()
    directory = Path(os.environ.get('CODEX_HOME', root / '.codex')) if root == Path.home() else root / '.codex'
    result = {'model': '', 'models': [], 'config_state': 'absent', 'auth_store': None,
              'provider_supported': True}
    try:
        path = directory / 'config.toml'
        if not path.exists():
            return result
        if path.stat().st_size > 262144:
            raise ValueError('oversized')
        data = tomllib.loads(path.read_text(encoding='utf-8'))
        # Profiles require the user to choose a model explicitly instead of guessing
        # which profile a separate desktop session has activated.
        model = data.get('model', '')
        if isinstance(model, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}', model):
            result.update(model=model, models=[model])
        provider = data.get('model_provider', 'openai')
        definition = (data.get('model_providers') or {}).get(provider, {})
        result['provider_supported'] = (
            (provider == 'openai' or definition.get('requires_openai_auth') is True) and not definition.get('base_url')
            and not definition.get('env_key') and not definition.get('http_headers')
            and not definition.get('env_http_headers')
            and definition.get('wire_api', 'responses') == 'responses')
        auth_store = data.get('cli_auth_credentials_store')
        if auth_store in ('file', 'keyring', 'auto', 'ephemeral'):
            result['auth_store'] = auth_store
        result['config_state'] = 'profile_requires_selection' if data.get('profile') else 'read'
        if data.get('profile'):
            result.update(model='', models=[])
    except (OSError, ValueError, TypeError, AttributeError):
        result['config_state'] = 'invalid'
    return result


def discover_codex(home=None, *, refresh=False):
    selection = local_selection(home)
    candidates = executable_candidates()
    key = (str(home), tuple(candidates), repr(selection), os.environ.get('CODEX_HOME'))
    previous = _cache.get(key)
    if not refresh and previous and time.monotonic() - previous[0] < 15:
        return dict(previous[1])
    result = {**selection, 'installed': bool(candidates), 'compatible': False, 'ready': False,
              'login_state': 'error' if candidates else 'not_installed', 'auth_method': None,
              'executable': None}
    for executable in candidates:
        try:
            help_result = subprocess.run([executable, 'exec', '--help'], capture_output=True,
                                         text=True, timeout=3, check=False)
            if help_result.returncode != 0:
                continue
            compatible = all(flag in help_result.stdout for flag in REQUIRED_FLAGS)
            result.update(executable=executable, compatible=compatible)
            if not compatible:
                result['login_state'] = 'incompatible'
                continue
            login = subprocess.run([executable, 'login', 'status'], capture_output=True,
                                   text=True, timeout=3, check=False, env=login_environment())
            text = (login.stdout + '\n' + login.stderr).lower()
            logged_in = login.returncode == 0 and 'logged in' in text and 'not logged in' not in text
            state = 'logged_in' if logged_in else 'not_logged_in' if 'not logged in' in text else 'error'
            method = 'chatgpt' if logged_in and 'chatgpt' in text else 'api_key' if logged_in and 'api key' in text else None
            result.update(login_state=state, auth_method=method,
                          ready=logged_in and selection['provider_supported'] and selection['config_state'] != 'invalid')
            break
        except (OSError, subprocess.TimeoutExpired, UnicodeError):
            continue
    if len(_cache) > 32:
        _cache.clear()
    _cache[key] = (time.monotonic(), dict(result))
    return result
