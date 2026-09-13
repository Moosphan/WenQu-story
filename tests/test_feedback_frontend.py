import subprocess
from pathlib import Path


def test_feedback_survives_event_dispatch_and_reports_saved_on_refresh_error():
    script = Path('story_core/web/app.js').read_text()
    start = script.index("  element('human-feedback-form').addEventListener('submit', async event => {")
    end = script.index("  element('query-form').addEventListener", start)
    handler = script[start:end]
    harness = r'''
const vm = require('node:vm');
async function check(refreshFails, switchBook) {
 let callback, resets=0, calls=0, notices=[];
 let resolve;
 const form={addEventListener:(_,fn)=>callback=fn,reset:()=>resets++};
 const nodes={'human-feedback-form':form,'submit-human-feedback':{id:'save'}};
 const state={book:{book_id:'a'},chapter:{chapter_number:1,version_id:'v',status:'committed'},busy:new Set()};
 vm.runInNewContext(process.argv[1],{state,element:id=>nodes[id]||{value:'意见',checked:false},controls:()=>{},api:()=>{calls++;return new Promise(r=>resolve=r)},refreshStatus:async()=>{if(refreshFails)throw Error('刷新失败')},notice:m=>notices.push(m)});
 const event={preventDefault(){},currentTarget:form};
 const pending=callback(event);event.currentTarget=null;
 if(switchBook)state.book={book_id:'b'};
 resolve({});await pending;
 if(calls!==1 || state.busy.size)throw Error('submission lifecycle');
 if(!switchBook && resets!==1)throw Error('form failed to reset after saved');
 if(switchBook && resets!==0)throw Error('reset different book form');
 if(!notices.some(m=>m.includes('已保存')))throw Error('missing saved confirmation: '+notices);
}
(async()=>{await check(false,false);await check(true,false);await check(false,true)})().catch(e=>{console.error(e);process.exit(1)});
'''
    result = subprocess.run(['node', '-e', harness, handler], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
