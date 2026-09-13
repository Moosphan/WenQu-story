import json
import subprocess
from pathlib import Path

import pytest

from story_core.errors import StoryError
from test_workflow import make, result_for


def test_host_is_task_only_and_has_no_tools_or_session(monkeypatch):
    from story_core.host import ClaudeCode
    from story_core.schemas import SCHEMAS

    calls = []

    class Process:
        returncode = 0

        def __init__(self, command, **options):
            calls.append((command, options))
            assert list(Path(options['cwd']).iterdir()) == []

        def communicate(self, input=None, timeout=None):
            payload = json.loads(input)
            assert set(payload) == {'task', 'output_schema'}
            assert 'author_secret' not in input
            return json.dumps({'is_error': False, 'subtype': 'success', 'structured_output': {'verdict': 'pass', 'issues': [], 'notes': '测试'}, 'usage': {'input_tokens': 4, 'output_tokens': 5}}), ''

    monkeypatch.setattr('story_core.host.shutil.which', lambda name: '/usr/bin/claude')
    monkeypatch.setattr('story_core.host.subprocess.Popen', Process)
    provider = ClaudeCode()
    result = provider.generate({'input': {'candidate': '正文'}, 'output_schema': SCHEMAS['reader'], 'author_secret': '不可见'})
    assert result['verdict'] == 'pass'
    command, options = calls[0]
    assert command[command.index('--tools') + 1] == ''
    assert '--safe-mode' in command and '--no-session-persistence' in command
    assert '--strict-mcp-config' in command
    assert '--resume' not in command and '--continue' not in command
    assert not Path(options['cwd']).exists()
    assert provider.last_usage == 9


def test_host_effort_is_explicit_and_validated(monkeypatch):
    from story_core.host import ClaudeCode
    monkeypatch.setattr('story_core.host.shutil.which', lambda name: '/usr/bin/claude')
    commands = []
    provider = ClaudeCode(effort='low')
    def execute(command, *args):
        commands.append(command)
        return json.dumps({'subtype': 'success', 'structured_output': {'ok': True}})
    monkeypatch.setattr(provider, '_execute', execute)
    provider.generate({'input': {}, 'output_schema': {}})
    assert commands[0][commands[0].index('--effort') + 1] == 'low'
    with pytest.raises(StoryError):
        ClaudeCode(effort='invalid')


def test_host_timeout_is_configurable_for_long_chapter_revisions(monkeypatch):
    from story_core.host import ClaudeCode
    monkeypatch.setattr('story_core.host.shutil.which', lambda name: '/usr/bin/claude')
    monkeypatch.setenv('HULK_HOST_TIMEOUT', '1200')
    assert ClaudeCode().timeout == 1200
    monkeypatch.setenv('HULK_HOST_TIMEOUT', 'too-long')
    with pytest.raises(StoryError) as error:
        ClaudeCode()
    assert error.value.code == 'INVALID_PROVIDER'


def test_host_thinking_override_is_process_local(monkeypatch):
    from story_core.host import ClaudeCode
    monkeypatch.setattr('story_core.host.shutil.which', lambda name: '/usr/bin/claude')
    monkeypatch.setenv('HULK_HOST_THINKING', 'off')
    monkeypatch.setenv('MAX_THINKING_TOKENS', '8192')
    provider = ClaudeCode()
    assert provider.process_environment()['MAX_THINKING_TOKENS'] == '0'
    import os
    assert os.environ['MAX_THINKING_TOKENS'] == '8192'
    monkeypatch.setenv('HULK_HOST_THINKING', 'inherit')
    assert ClaudeCode().process_environment()['MAX_THINKING_TOKENS'] == '8192'
    monkeypatch.setenv('HULK_HOST_THINKING', 'unknown')
    with pytest.raises(StoryError):
        ClaudeCode()


@pytest.mark.parametrize('response', [
    {'is_error': True, 'result': 'secret diagnostic'},
    {'is_error': False, 'subtype': 'error_max_turns', 'structured_output': {'ok': True}},
    {'is_error': False, 'subtype': 'success', 'result': 'not JSON'},
])
def test_host_rejects_failed_or_unstructured_result(monkeypatch, response):
    from story_core.host import ClaudeCode

    monkeypatch.setattr('story_core.host.shutil.which', lambda name: '/usr/bin/claude')
    provider = ClaudeCode()
    monkeypatch.setattr(provider, '_execute', lambda *args: json.dumps(response))
    with pytest.raises(StoryError) as error:
        provider.generate({'input': {}, 'output_schema': {}})
    assert 'secret' not in str(error.value)


def test_failed_old_worker_does_not_pause_replacement_run(tmp_path):
    from story_core.providers import run_worker

    service, book = make(tmp_path)

    class Replaced:
        def generate(self, task, cancelled=None):
            service.control(book, 'cancel')
            service.start_run(book)
            raise StoryError('PROVIDER_ERROR', '旧任务失败')

    with pytest.raises(StoryError):
        run_worker(service, book, Replaced())
    assert service.status(book)['run']['status'] == 'running'


def test_worker_cancellation_checks_persisted_task_lease(tmp_path):
    from story_core.providers import run_worker

    service, book = make(tmp_path)

    class Cancelled:
        cancellable = True

        def generate(self, task, cancelled):
            assert cancelled() is False
            service.control(book, 'pause')
            assert cancelled() is True
            raise StoryError('TASK_CANCELLED', '任务已暂停')

    with pytest.raises(StoryError):
        run_worker(service, book, Cancelled())
    assert service.status(book)['run']['status'] == 'paused'
    assert service.get_book(book)['brief'] is None


def test_active_host_task_can_renew_its_lease_without_losing_pause_control(tmp_path):
    service, book = make(tmp_path)
    task = service.next_task(book, worker_id='claude-host')
    with service.store.write(book) as conn:
        conn.execute('UPDATE tasks SET lease_until=? WHERE id=?', (0, task['task_id']))
    assert service.renew_task_lease(task['task_id'], task['lease_id'], 'claude-host') is False

    replacement = service.next_task(book, worker_id='claude-host')
    assert service.renew_task_lease(replacement['task_id'], replacement['lease_id'], 'claude-host') is True
    assert service.task_active(replacement['task_id'], replacement['lease_id']) is True
    service.control(book, 'pause')
    assert service.renew_task_lease(replacement['task_id'], replacement['lease_id'], 'claude-host') is False


def test_cancellable_worker_heartbeats_its_lease_before_checking_cancellation(tmp_path, monkeypatch):
    from story_core.providers import run_worker

    service, book = make(tmp_path)
    renewals = []
    original = service.renew_task_lease

    def renew(*args):
        renewals.append(args)
        return original(*args)

    monkeypatch.setattr(service, 'renew_task_lease', renew)

    class Host:
        cancellable = True

        def generate(self, task, cancelled):
            assert cancelled() is True
            service.control(book, 'pause')
            assert cancelled() is False
            raise StoryError('TASK_CANCELLED', '任务已暂停')

    with pytest.raises(StoryError):
        run_worker(service, book, Host())
    assert len(renewals) == 1
    task_id, lease_id, worker_id = renewals[0]
    assert task_id.startswith('task_') and lease_id.startswith('lease_') and worker_id.startswith('api_')


def test_worker_records_failure_and_can_resume_in_new_process(tmp_path):
    from story_core.providers import run_worker
    from story_core.service import StoryService

    service, book = make(tmp_path, chapters=1)

    class Broken:
        def generate(self, task, cancelled=None):
            raise StoryError('HOST_TIMEOUT', '宿主执行超时。')

    with pytest.raises(StoryError):
        run_worker(service, book, Broken())
    restarted = StoryService(tmp_path)
    status = restarted.status(book)
    assert status['run']['status'] == 'paused'
    assert '超时' in status['run']['reason']
    assert any(event['kind'] == 'worker_failed' for event in status['events'])
    restarted.control(book, 'resume')

    class Fixture:
        def generate(self, task):
            return result_for(task)

    assert run_worker(restarted, book, Fixture())['status'] == 'complete'


def test_executor_configuration_is_explicit(monkeypatch):
    from story_core.providers import configured_provider

    monkeypatch.setenv('HULK_EXECUTOR', 'claude')
    monkeypatch.setattr('story_core.host.shutil.which', lambda name: '/usr/bin/claude')
    assert configured_provider().name == 'claude'
    monkeypatch.setenv('HULK_EXECUTOR', 'surprise')
    with pytest.raises(StoryError):
        configured_provider()


@pytest.mark.parametrize('cancelled', [True, False])
def test_host_cancels_or_times_out_and_reaps_process(monkeypatch, cancelled):
    from story_core.host import ClaudeCode

    class Process:
        pid = 4321

        def communicate(self, **options):
            return '', ''

    killed = []
    monkeypatch.setattr('story_core.host.shutil.which', lambda name: '/usr/bin/claude')
    monkeypatch.setattr('story_core.host.subprocess.Popen', lambda *args, **options: Process())
    monkeypatch.setattr('story_core.host.os.killpg', lambda pid, sig: killed.append(pid))
    provider = ClaudeCode(timeout=0 if not cancelled else 600)
    with pytest.raises(StoryError) as error:
        provider.generate({'input': {}, 'output_schema': {}}, cancelled=lambda: cancelled)
    assert error.value.code == ('TASK_CANCELLED' if cancelled else 'HOST_TIMEOUT')
    if not cancelled:
        assert error.value.details['timeout_seconds'] == 0
        assert error.value.details['elapsed_seconds'] >= 0
    assert killed == [4321]


def test_host_http_configuration_and_cli_selection(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from story_core.http import create_app
    from story_core.cli import _parser

    monkeypatch.setenv('HULK_EXECUTOR', 'claude')
    monkeypatch.setattr('story_core.host.shutil.which', lambda name: '/usr/bin/claude')
    with TestClient(create_app(tmp_path)) as client:
        capabilities = client.get('/api/capabilities').json()
        assert capabilities['worker_configured'] is True
        assert capabilities['executor'] == 'claude'
    assert _parser().parse_args(['worker', 'book', '--executor', 'claude']).executor == 'claude'


def test_external_worker_activity_visible_until_lease_expires(tmp_path):
    from fastapi.testclient import TestClient
    from story_core.http import create_app

    service, book = make(tmp_path)
    task = service.next_task(book, 'external-host')
    with TestClient(create_app(tmp_path)) as client:
        status = client.get(f'/api/books/{book}/status').json()
        assert status['active_task']['worker_id'] == 'external-host'
        assert status['active_task']['stage'] == task['stage']
        assert status['worker_running'] is False
        service.control(book, 'pause')
        assert client.get(f'/api/books/{book}/status').json()['active_task'] is None


def test_unexpected_provider_error_pauses_without_exposing_diagnostics(tmp_path):
    from story_core.providers import run_worker

    service, book = make(tmp_path)

    class Broken:
        def generate(self, task):
            raise OSError('private access token')

    with pytest.raises(StoryError) as error:
        run_worker(service, book, Broken())
    assert 'private' not in str(error.value)
    assert service.status(book)['run']['status'] == 'paused'


def test_host_timeout_preserves_redacted_cli_diagnostics(monkeypatch):
    from story_core.host import ClaudeCode

    class Process:
        pid = 7351
        returncode = None

        def communicate(self, **options):
            return '', 'provider error: API_KEY=should-not-leak; schema route stalled'

    monkeypatch.setattr('story_core.host.shutil.which', lambda name: '/usr/bin/claude')
    monkeypatch.setattr('story_core.host.subprocess.Popen', lambda *args, **kwargs: Process())
    monkeypatch.setattr('story_core.host.os.killpg', lambda *args: None)
    provider = ClaudeCode(timeout=0)

    with pytest.raises(StoryError) as caught:
        provider.generate({'input': {}, 'output_schema': {}})

    details = caught.value.details
    assert details['phase'] == 'waiting_for_structured_result'
    assert 'schema route stalled' in details['host_diagnostics']['stderr_tail']
    assert 'should-not-leak' not in details['host_diagnostics']['stderr_tail']
    assert details['host_diagnostics']['stdout_bytes_at_stop'] == 0


def test_host_can_pin_a_compatible_model_without_mutating_claude_settings(monkeypatch):
    from story_core.host import ClaudeCode

    monkeypatch.setattr('story_core.host.shutil.which', lambda name: '/usr/bin/claude')
    monkeypatch.setenv('HULK_HOST_MODEL', 'deepseek-v4-pro')
    provider = ClaudeCode()
    commands = []

    def execute(command, *args):
        commands.append(command)
        return json.dumps({'subtype': 'success', 'structured_output': {'ok': True}})

    monkeypatch.setattr(provider, '_execute', execute)
    provider.generate({'input': {}, 'output_schema': {}})
    command = commands[0]
    assert command[command.index('--model') + 1] == 'deepseek-v4-pro'
    assert provider.last_metadata['model_override'] == 'deepseek-v4-pro'


def test_host_can_use_a_dedicated_revision_model(monkeypatch):
    from story_core.host import ClaudeCode

    monkeypatch.setattr('story_core.host.shutil.which', lambda name: '/usr/bin/claude')
    monkeypatch.setenv('HULK_HOST_MODEL', 'deepseek-v4-pro')
    monkeypatch.setenv('HULK_HOST_REVISION_MODEL', 'deepseek-v4-flash')
    provider = ClaudeCode()
    commands = []

    def execute(command, *args):
        commands.append(command)
        return json.dumps({'subtype': 'success', 'structured_output': {'title': '章名', 'body': '正文'}})

    monkeypatch.setattr(provider, '_execute', execute)
    provider.generate({'stage': 'revise', 'input': {}, 'output_schema': {}})
    provider.generate({'stage': 'draft', 'input': {}, 'output_schema': {}})

    assert commands[0][commands[0].index('--model') + 1] == 'deepseek-v4-flash'
    assert commands[1][commands[1].index('--model') + 1] == 'deepseek-v4-pro'
    assert provider.last_metadata['model_override'] == 'deepseek-v4-pro'


def test_host_can_dispatch_revisions_to_explicit_native_transport(monkeypatch):
    from story_core.host import ClaudeCode

    monkeypatch.setattr('story_core.host.shutil.which', lambda name: '/usr/bin/claude')
    monkeypatch.setenv('HULK_HOST_REVISION_TRANSPORT', 'native_deepseek')
    provider = ClaudeCode()
    called = []

    def native(task):
        called.append(task['stage'])
        return {'title': '章名', 'body': '正文'}, {'executor': 'deepseek-native', 'models': ['deepseek-v4-pro']}

    monkeypatch.setattr(provider, '_native_revision', native)
    monkeypatch.setattr(provider, '_execute', lambda *args: pytest.fail('native revision must not launch Claude CLI'))
    result = provider.generate({'stage': 'revise', 'input': {}, 'output_schema': {}})

    assert result['body'] == '正文'
    assert called == ['revise']
    assert provider.last_metadata['executor'] == 'deepseek-native'


def test_host_native_transport_routes_followup_stages_without_cli(monkeypatch):
    """A successful revision must not fall back to the unstable CLI for extraction."""
    from story_core.host import ClaudeCode

    monkeypatch.setattr('story_core.host.shutil.which', lambda name: '/usr/bin/claude')
    monkeypatch.setenv('HULK_HOST_REVISION_TRANSPORT', 'native_deepseek')
    monkeypatch.setenv('HULK_HOST_MODEL', 'deepseek-v4-pro')
    provider = ClaudeCode()
    called = []

    def native(task):
        called.append(task['stage'])
        return {'memory': []}, {'executor': 'deepseek-native', 'models': ['deepseek-v4-pro']}

    monkeypatch.setattr(provider, '_native_revision', native)
    monkeypatch.setattr(provider, '_execute', lambda *args: pytest.fail('native pipeline must not launch Claude CLI'))
    result = provider.generate({'stage': 'extract', 'input': {}, 'output_schema': {}})

    assert result == {'memory': []}
    assert called == ['extract']
    assert provider.last_metadata['models'] == ['deepseek-v4-pro']


def test_native_pipeline_uses_auxiliary_model_for_extraction(monkeypatch, tmp_path):
    from story_core.host import ClaudeCode

    monkeypatch.setattr('story_core.host.shutil.which', lambda name: '/usr/bin/claude')
    monkeypatch.setenv('HULK_HOST_NATIVE_TRANSPORT', 'native_deepseek')
    monkeypatch.setenv('HULK_HOST_MODEL', 'deepseek-v4-pro')
    monkeypatch.setenv('HULK_HOST_AUXILIARY_MODEL', 'deepseek-v4-flash')
    settings = tmp_path / '.claude' / 'settings.json'
    settings.parent.mkdir()
    settings.write_text(json.dumps({'env': {
        'ANTHROPIC_BASE_URL': 'https://api.deepseek.com/anthropic',
        'ANTHROPIC_AUTH_TOKEN': 'secret',
        'ANTHROPIC_MODEL': 'deepseek-v4-pro[1m]',
    }}))
    monkeypatch.setattr('story_core.host.Path.home', lambda: tmp_path)
    captured = {}

    class Response:
        status_code = 200
        def json(self):
            return {'choices': [{'finish_reason': 'stop', 'message': {'content': '{"memory": []}'}}], 'usage': {'total_tokens': 7}}

    def post(url, **kwargs):
        captured.update(kwargs['json'])
        return Response()

    monkeypatch.setattr('story_core.host.httpx.post', post)
    provider = ClaudeCode()
    assert provider.generate({'stage': 'extract', 'input': {}, 'output_schema': {}}) == {'memory': []}
    assert captured['model'] == 'deepseek-v4-flash'


def test_worker_pause_while_model_returns_is_not_reported_as_failure(tmp_path):
    from story_core.providers import run_worker

    service, book = make(tmp_path)

    class ReturnsAfterPause:
        def generate(self, task):
            service.control(book, 'pause')
            return result_for(task)

    result = run_worker(service, book, ReturnsAfterPause())
    assert result['status'] == 'paused'
    assert service.status(book)['run']['status'] == 'paused'


def test_native_off_is_sent_and_truncation_keeps_safe_diagnostics(monkeypatch,tmp_path):
    from story_core.host import ClaudeCode
    monkeypatch.setattr('story_core.host.shutil.which',lambda _: '/usr/bin/claude')
    monkeypatch.setenv('HULK_HOST_NATIVE_TRANSPORT','native_deepseek')
    monkeypatch.setenv('HULK_HOST_THINKING','off')
    settings=tmp_path/'.claude/settings.json';settings.parent.mkdir()
    settings.write_text(json.dumps({'env':{'ANTHROPIC_BASE_URL':'https://api.deepseek.com/anthropic','ANTHROPIC_AUTH_TOKEN':'secret','ANTHROPIC_MODEL':'deepseek-v4-flash'}}))
    monkeypatch.setattr('story_core.host.Path.home',lambda:tmp_path)
    captured={}
    class Response:
        status_code=200
        def json(self): return {'choices':[{'finish_reason':'length','message':{'content':'private prose'}}],'usage':{'total_tokens':15000,'prompt_tokens':3000,'completion_tokens':12000,'completion_tokens_details':{'reasoning_tokens':11000}}}
    def post(url,**kwargs): captured.update(kwargs['json']);return Response()
    monkeypatch.setattr('story_core.host.httpx.post',post)
    provider=ClaudeCode()
    with pytest.raises(StoryError) as caught: provider.generate({'stage':'revise','input':{},'output_schema':{}})
    assert captured['thinking']=={'type':'disabled'}
    assert caught.value.details['completion_tokens']==12000
    assert caught.value.details['reasoning_tokens']==11000
    assert provider.last_metadata['thinking']=='off'
    assert 'private prose' not in str(caught.value.as_dict())
