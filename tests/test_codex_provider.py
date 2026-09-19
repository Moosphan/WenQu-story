import json
import subprocess
from pathlib import Path

import pytest

from story_core.errors import StoryError
from story_core.ai_config import AISettings
from test_ai_config import Vault


def test_codex_discovery_separates_auth_and_model_without_secrets(tmp_path, monkeypatch):
    from story_core import codex_discovery as cd
    root = tmp_path / '.codex'; root.mkdir()
    (root / 'config.toml').write_text('model = "local-model"\nmodel_provider="custom"\n[model_providers.custom]\nrequires_openai_auth=true\nwire_api="responses"\n')
    (root / 'auth.json').write_text('DO NOT READ')
    monkeypatch.setattr(cd, 'executable_candidates', lambda: ['/desktop/codex'])
    def probe(command, **kwargs):
        assert kwargs['timeout'] <= 5
        if command[-2:] == ['login', 'status']:
            return subprocess.CompletedProcess(command, 0, '', 'Logged in using ChatGPT\n')
        return subprocess.CompletedProcess(command, 0, '--ignore-user-config --ephemeral --output-schema --json', '')
    monkeypatch.setattr(cd.subprocess, 'run', probe)
    found = cd.discover_codex(tmp_path, refresh=True)
    assert found['installed'] and found['ready']
    assert found['auth_method'] == 'chatgpt' and found['model'] == 'local-model'
    assert 'DO NOT READ' not in json.dumps(found)


def test_discovery_falls_back_after_broken_cli_without_exposing_output(tmp_path, monkeypatch):
    from story_core import codex_discovery as cd
    monkeypatch.setattr(cd, 'executable_candidates', lambda: ['/broken/codex', '/working/codex'])
    def probe(command, **kwargs):
        if command[0] == '/broken/codex': return subprocess.CompletedProcess(command, -9, 'SECRET', 'SECRET')
        if command[-2:] == ['login', 'status']: return subprocess.CompletedProcess(command, 1, '', 'Not logged in')
        return subprocess.CompletedProcess(command, 0, '--ignore-user-config --ephemeral --output-schema --json', '')
    monkeypatch.setattr(cd.subprocess, 'run', probe)
    found = cd.discover_codex(tmp_path, refresh=True)
    assert found['installed'] and found['compatible'] and not found['ready']
    assert found['login_state'] == 'not_logged_in'
    assert found['executable'] == '/working/codex'
    assert 'SECRET' not in json.dumps(found)


def test_codex_settings_do_not_require_or_store_api_key(tmp_path, monkeypatch):
    from story_core import codex_discovery as cd
    monkeypatch.setattr(cd, 'discover_codex', lambda *a, **kw: {
        'installed': True, 'compatible': True, 'ready': True, 'login_state': 'logged_in',
        'auth_method': 'chatgpt', 'model': 'local-model', 'models': ['local-model'], 'executable': '/desktop/codex'})
    vault = Vault(); settings = AISettings(tmp_path/'ai.json', vault, home=tmp_path)
    saved = settings.save('codex', model='local-model')
    assert saved['mode'] == 'codex' and saved['local_codex']['ready']
    assert settings.runtime()['model'] == 'local-model'
    assert vault.values == {}
    assert next(p for p in saved['providers'] if p['id'] == 'openai')['label'] == 'OpenAI API'


def test_codex_discovery_missing_or_timed_out_is_not_authenticated(tmp_path, monkeypatch):
    from story_core import codex_discovery as cd
    monkeypatch.setattr(cd, 'executable_candidates', lambda: [])
    assert cd.discover_codex(tmp_path, refresh=True)['login_state'] == 'not_installed'
    monkeypatch.setattr(cd, 'executable_candidates', lambda: ['/broken'])
    def timeout(command, **kwargs): raise subprocess.TimeoutExpired(command, 3)
    monkeypatch.setattr(cd.subprocess, 'run', timeout)
    result = cd.discover_codex(tmp_path, refresh=True)
    assert result['installed'] and not result['ready'] and result['login_state'] == 'error'


@pytest.fixture
def fake_codex(tmp_path, monkeypatch):
    import sys
    from story_core import codex_discovery as cd
    executable = tmp_path / 'codex'
    executable.write_text('#!' + sys.executable + '\n' + '''
import json, os, pathlib, sys, time
args=sys.argv[1:]
if args == ['exec','--help']:
 print('--ignore-user-config --ephemeral --output-schema --json'); sys.exit(0)
if args == ['login','status']:
 print('Logged in using ChatGPT'); sys.exit(0)
assert '--ignore-user-config' in args and '--ephemeral' in args
assert args[args.index('--sandbox')+1] == 'read-only'
assert 'features.shell_tool=false' in args
assert 'tools.update_plan.enabled=false' in args
assert 'tools.update_plan=false' not in args
assert 'model_provider="wenqu"' in args
assert any('supports_websockets=false' in arg for arg in args)
assert 'CODEX_API_KEY' not in os.environ
schema=json.loads(pathlib.Path(args[args.index('--output-schema')+1]).read_text())
assert schema['type']=='object'
payload=json.loads(sys.stdin.read())
assert payload['task']['instruction']=='synthetic task'
mode=os.environ.get('WENQU_FAKE_CODEX_MODE','success')
if mode=='warnings':
 print(json.dumps({'type':'item.completed','item':{'type':'error','message':'Startup deprecation warning'}}))
 print(json.dumps({'type':'error','message':'Reconnecting...'}))
if mode=='sleep': time.sleep(30)
if mode=='exit': print('SECRET', file=sys.stderr); sys.exit(2)
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'not-json' if mode=='invalid' else json.dumps({'result':json.dumps({'answer':'完成'})})}}))
print(json.dumps({'type':'turn.failed','error':{'message':'SECRET'}} if mode=='failed' else {'type':'turn.completed','usage':{'input_tokens':100,'cached_input_tokens':40,'output_tokens':20}}))
''')
    executable.chmod(0o755)
    monkeypatch.setattr(cd, 'executable_candidates', lambda: [str(executable)])
    monkeypatch.setenv('CODEX_API_KEY', 'SHOULD_NOT_INHERIT')
    return executable


def task():
    return {'stage': 'draft', 'input': {'instruction': 'synthetic task'},
            'output_schema': {'type': 'object', 'properties': {'answer': {'type': 'string'}},
                              'required': ['answer'], 'additionalProperties': False}}


def test_codex_exec_schema_usage_and_configured_dispatch(tmp_path, fake_codex):
    from story_core.providers import configured_provider
    settings = AISettings(tmp_path/'ai.json', Vault(), home=tmp_path)
    settings.save('codex', model='explicit-model')
    provider = configured_provider(settings=settings)
    assert provider.name == 'codex'
    assert provider.generate(task()) == {'answer': '完成'}
    assert provider.last_usage == 120
    assert provider.last_usage_breakdown == {'input_tokens': 100, 'output_tokens': 20, 'cached_input_tokens': 40}
    assert provider.last_metadata['models'] == ['explicit-model']


@pytest.mark.parametrize('mode,code', [('failed','HOST_RESULT_ERROR'), ('invalid','HOST_RESULT_ERROR'), ('exit','HOST_EXECUTION_ERROR')])
def test_codex_failed_results_never_return_prose(fake_codex, monkeypatch, mode, code):
    from story_core.codex_host import CodexCLI
    monkeypatch.setenv('WENQU_FAKE_CODEX_MODE', mode)
    provider = CodexCLI(model='explicit-model')
    with pytest.raises(StoryError) as error:
        provider.generate(task())
    assert error.value.code == code
    assert 'SECRET' not in str(error.value.as_dict())


def test_codex_completed_turn_survives_nonfatal_warnings(fake_codex, monkeypatch):
    from story_core.codex_host import CodexCLI
    monkeypatch.setenv('WENQU_FAKE_CODEX_MODE', 'warnings')
    assert CodexCLI(model='explicit-model').generate(task()) == {'answer': '完成'}


@pytest.mark.parametrize('cancel', [True, False])
def test_codex_cancels_or_times_out_process_group(fake_codex, monkeypatch, cancel):
    from story_core.codex_host import CodexCLI
    monkeypatch.setenv('WENQU_FAKE_CODEX_MODE', 'sleep')
    provider = CodexCLI(model='explicit-model', timeout=.1)
    with pytest.raises(StoryError) as error:
        provider.generate(task(), cancelled=lambda: cancel)
    assert error.value.code == ('TASK_CANCELLED' if cancel else 'HOST_TIMEOUT')


def test_codex_capacity_gate_precedes_child_execution(fake_codex, monkeypatch):
    from story_core.codex_host import CodexCLI
    provider = CodexCLI(model='explicit-model')
    monkeypatch.setattr(provider, '_execute', lambda *a: pytest.fail('must not start model'))
    value = task()
    value['input']['context_diagnostics'] = {'policy': {'mode': 'adaptive', 'context_window': 1}}
    with pytest.raises(StoryError):
        provider.generate(value)


def test_codex_ui_login_status_and_provider_switching():
    script = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const elements={};
const make=()=>({value:'',hidden:false,textContent:'',options:[],replaceChildren(...v){this.options=v;},append(v){this.options.push(v);}});
const ctx=vm.createContext({document:{getElementById(id){return elements[id] ||= make();},createElement:make},Set});
vm.runInContext(fs.readFileSync(process.argv[1],'utf8').replace(/initialize\(\);\s*$/,''),ctx);
vm.runInContext(`state.aiConfig={providers:[{id:'codex',mode:'codex',models:['local-model']},{id:'openai',mode:'api',base_url:'https://api.openai.com/v1'},{id:'claude_code',mode:'claude'}],local_codex:{installed:true,compatible:true,ready:true,login_state:'logged_in',auth_method:'chatgpt',model:'local-model',provider_supported:true}};applyAIProvider('codex');`,ctx);
assert.equal(elements['ai-api-fields'].hidden,true);
assert.equal(elements['ai-model'].value,'local-model');
assert(elements['ai-claude-note'].textContent.includes('ChatGPT'));
vm.runInContext("state.aiConfig.local_codex.ready=false;state.aiConfig.local_codex.login_state='not_logged_in';applyAIProvider('codex');",ctx);
assert(elements['ai-claude-note'].textContent.includes('尚未确认登录'));
vm.runInContext("applyAIProvider('claude_code');",ctx);
assert(!elements['ai-claude-note'].textContent.includes('ChatGPT'));
vm.runInContext("applyAIProvider('openai');",ctx);
assert.equal(elements['ai-api-fields'].hidden,false);
assert.equal(elements['ai-claude-note'].hidden,true);
'''
    web = Path(__file__).parents[1] / 'story_core/web'
    result = subprocess.run(['node', '-e', script, str(web/'app.js')], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert '<option value="codex">Codex（本机登录）</option>' in (web/'index.html').read_text()


@pytest.mark.parametrize('stage,result', [
    ('revise', {'patches': [{'before': '原文', 'after': '新文'}]}),
    ('extract', {'memories': [{'kind': 'fact', 'key': '测试', 'value': '值', 'evidence': '原文', 'visibility': 'reader'}]}),
    ('continuity', {'verdict': 'pass', 'issues': [], 'notes': ''}),
])
def test_codex_envelope_preserves_real_pipeline_contracts(fake_codex, monkeypatch, stage, result):
    from story_core.codex_host import CodexCLI
    from story_core.schemas import SCHEMAS
    provider = CodexCLI(model='explicit-model')
    def execute(command, content, cancelled):
        transport = json.loads(Path(command[command.index('--output-schema')+1]).read_text())
        assert transport == {'type': 'object', 'properties': {'result': {'type': 'string'}}, 'required': ['result'], 'additionalProperties': False}
        assert json.loads(content)['output_schema'] == SCHEMAS[stage]
        assert all(f'features.{feature}=false' in command for feature in ('goals', 'sleep_tool', 'skill_search', 'code_mode_host'))
        return '\n'.join(json.dumps(value) for value in [
            {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': json.dumps({'result': json.dumps(result)})}},
            {'type': 'turn.completed', 'usage': {}}])
    monkeypatch.setattr(provider, '_execute', execute)
    assert provider.generate({'stage': stage, 'input': {}, 'output_schema': SCHEMAS[stage]}) == result
    if stage == 'extract':
        result['memories'][0]['entity_type'] = 'person'
        with pytest.raises(StoryError):
            provider.generate({'stage': stage, 'input': {}, 'output_schema': SCHEMAS[stage]})


def test_codex_http_configuration_reports_executable(tmp_path, fake_codex):
    from fastapi.testclient import TestClient
    from story_core.http import create_app
    settings = AISettings(tmp_path/'ai.json', Vault(), home=tmp_path)
    with TestClient(create_app(tmp_path/'books', ai_settings=settings)) as client:
        response = client.post('/api/ai-config', json={'provider': 'codex', 'model': 'explicit-model'})
        assert response.status_code == 200
        assert response.json()['local_codex']['auth_method'] == 'chatgpt'
        capabilities = client.get('/api/capabilities').json()
        assert capabilities['executor'] == 'codex' and capabilities['worker_configured']


def test_codex_model_capacity_uses_exact_local_catalog_entry(tmp_path):
    from story_core.codex_discovery import model_capacity
    root = tmp_path / '.codex'; root.mkdir()
    (root/'models_cache.json').write_text(json.dumps({'models': [
        {'slug': 'chosen', 'context_window': 272000, 'max_context_window': 872000, 'effective_context_window_percent': 95},
        {'slug': 'invalid', 'context_window': True}]}))
    assert model_capacity('chosen', home=tmp_path) == 258400
    assert model_capacity('missing', home=tmp_path) is None
    assert model_capacity('invalid', home=tmp_path) is None


def test_codex_capacity_fallback_does_not_override_explicit_limits(fake_codex, monkeypatch):
    from story_core.codex_host import CodexCLI
    monkeypatch.setattr('story_core.codex_host.model_capacity', lambda model: 100000)
    monkeypatch.setenv('HULK_CONTEXT_MODE', 'adaptive')
    monkeypatch.setenv('HULK_CONTEXT_MODEL_WINDOWS', json.dumps({'old-model': 1000000}))
    provider = CodexCLI(model='explicit-model')
    assert provider.generate(task()) == {'answer': '完成'}
    assert provider.last_context['context_window'] == 100000
    monkeypatch.setenv('HULK_CONTEXT_MODEL_WINDOWS', json.dumps({'explicit-model': 1}))
    with pytest.raises(StoryError): provider.generate(task())
    assert provider.last_context['context_window'] == 1


def test_codex_catalog_caps_generic_window_and_unknown_remains_blocked():
    from story_core.context_compiler import compile_task
    value = task()
    value['input']['context_diagnostics'] = {'policy': {'mode': 'adaptive', 'context_window': 1000000}}
    assert compile_task(value, model='chosen', model_context_window=258400).diagnostics['context_window'] == 258400
    value['input']['context_diagnostics']['policy']['context_window'] = 1
    assert not compile_task(value, model='chosen', model_context_window=258400).executable
    value['input']['context_diagnostics']['policy']['model_windows'] = {'other': 1000000}
    assert not compile_task(value, model='chosen', model_context_window=258400).executable
    value['input']['context_diagnostics']['policy']['context_window'] = None
    unknown = compile_task(value, model='missing')
    with pytest.raises(StoryError, match='尚未识别'):
        unknown.require_executable()
