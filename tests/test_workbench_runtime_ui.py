"""Exercise UI state handling in a DOM stub, without spending model tokens."""
import subprocess
from pathlib import Path


def test_config_defaults_and_interruption_ui():
    script = Path(__file__).parents[1] / 'story_core/web/app.js'
    code = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const fields = new Map();
function field() { return { value:'', hidden:false, textContent:'', className:'', options:[],
  classList:{add(){},remove(){}}, append(x){ this.options.push(x); },
  replaceChildren(...x){this.options=x;} }; }
const context = vm.createContext({document:{
  getElementById(id){ if(!fields.has(id))fields.set(id,field()); return fields.get(id); },
  createElement(){return field();}
}, console, Date, Set});
vm.runInContext(fs.readFileSync(process.argv[1],'utf8').replace(/initialize\(\);\s*$/, ''), context);
(async()=>{
  await vm.runInContext(`
    state.executor = 'claude';
    api = async () => ({configured:false, local_claude:{available:true,model:'local-model'}, providers:[
      {id:'openai',mode:'api',model:'api-model',base_url:'https://example.test/v1',models:['api-model']},
      {id:'claude_code',mode:'claude',model:'',models:['local-model']} ]});
    loadAIConfig();`, context);
  assert.equal(fields.get('ai-provider').value, 'claude_code');
  assert.equal(fields.get('ai-model').value, 'local-model');
  assert.equal(fields.get('ai-api-fields').hidden, true);
  await vm.runInContext(`state.executor='manual'; loadAIConfig();`, context);
  assert.equal(fields.get('ai-base-url').value,'https://example.test/v1');
  assert.equal(fields.get('ai-model').value,'api-model');
  vm.runInContext(`state.status={run:{stage:'extract',chapter_number:3}, worker_session:{status:'interrupted',error:{code:'SERVER_INTERRUPTED',message:'工作台中断'}}}; renderExecutionSummary();`, context);
  assert.match(fields.get('execution-outcome').textContent,/中断/);
  assert.match(fields.get('execution-detail').textContent,/第 3 章/);
  assert.match(fields.get('execution-detail').textContent,/提取故事记忆/);
  vm.runInContext(`state.status={run:{stage:'draft',chapter_number:1},execution:{stage:'draft',chapter_number:1,outcome:'failed',elapsed_seconds:2,failure:{code:'PROVIDER_ERROR',message:'拒绝鉴权'},failure_details:{phase:'http_response',next_action:'检查 API Key'}}}; renderExecutionSummary();`, context);
  assert.match(fields.get('execution-detail').textContent,/检查 API Key/);
  assert.match(fields.get('execution-detail').textContent,/服务端响应/);

})().catch(error=>{console.error(error);process.exitCode=1;});
'''
    result = subprocess.run(['node', '-e', code, str(script)], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
