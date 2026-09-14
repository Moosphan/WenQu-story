from pathlib import Path
import subprocess
from test_workflow import make, step


def test_budget_block_is_for_next_stage_not_previous_result(tmp_path):
    s,b=make(tmp_path)
    step(s,b)
    with s.store.write(b) as c:
        c.execute('UPDATE runs SET budget_tokens=tokens WHERE book_id=?',(b,))
    assert s.next_task(b)['reason']=='budget'
    state=s.status(b)
    assert state['execution']['outcome']=='succeeded'
    assert state['execution']['stage']=='brief'
    assert state['blocker']['code']=='BUDGET_LIMIT'
    assert state['blocker']['stage']=='outline'
    assert state['blocker']['minimum_budget_tokens']>state['run']['budget_tokens']
    assert state['blocker']['minimum_max_steps']>state['run']['steps']


def test_blank_budget_fields_do_not_overwrite_saved_limits():
    src=Path('story_core/web/app.js').read_text()
    start=src.index('function budgetOptions(');end=src.index('\nfunction ',start+1)
    subprocess.run(['node','-e',"const assert=require('node:assert/strict');"+src[start:end]+";assert.deepEqual(budgetOptions('', ''),{});assert.deepEqual(budgetOptions('30000000', ''),{budget_tokens:30000000});"],check=True)
    html=Path('story_core/web/index.html').read_text()
    assert 'max="100000000" value="2000000"' not in html


def test_resume_handler_preserves_budget_and_can_focus_blocker_without_call():
    src=Path('story_core/web/app.js').read_text()
    helper=src[src.index('function budgetOptions('):src.index('function workflowPresentation(')]
    handler=src[src.index('async function continueWriting('):src.index('function automaticRevisionFeedback(')]
    subprocess.run(['node','-e',r'''
const assert=require('node:assert/strict');
const state={book:{book_id:'b'},worker:true};const stages={draft:'写作正文'};
const fields={'budget-tokens':{value:'',focus(){},closest(){return {}; }},'max-steps':{value:''}};
const element=id=>fields[id];let requests=[];let messages=[];
let status={run:{status:'paused',stage:'draft',chapter_number:4,budget_tokens:20000000,max_steps:500}};
const api=async(path,body)=>{if(path==='/api/capabilities') return {worker_configured:true,api_configured:true,executor:'fake'};if(body) requests.push([path,body]);return status;};
const workflowPresentation=()=>({notice:'续写'});const needsAuthorRevision=()=>false;
const notice=m=>messages.push(m);const refreshStatus=async()=>{};
''' + helper + handler + r'''
(async()=>{await continueWriting();assert.deepEqual(requests[0][1],{action:'resume',options:{}});
requests=[];status.blocker={code:'BUDGET_LIMIT',minimum_budget_tokens:21000000,minimum_max_steps:33};
await continueWriting();assert.equal(requests.length,0);assert.ok(messages.at(-1).includes('尚未启动'));})().catch(e=>{console.error(e);process.exitCode=1});
'''],check=True)
