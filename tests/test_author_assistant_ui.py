from pathlib import Path
import subprocess


def test_assistant_keeps_discussion_separate_and_fences_book_switches():
    source = Path('story_core/web/assistant.js').read_text()
    subprocess.run(['node', '-e', r'''
const assert = require('node:assert/strict');
class Element {
  constructor(){this.children=[];this.hidden=false;this.disabled=false;this.value='';this.textContent='';this.listeners={};this.scrollTop=0;this.scrollHeight=0;this.dataset={};}
  append(...items){this.children.push(...items)}
  replaceChildren(...items){this.children=items}
  addEventListener(name,fn){this.listeners[name]=fn}
  setAttribute(){} focus(){}
}
const fields={}; const element=id=>fields[id] ||= new Element();
const node=(tag,text,cls)=>Object.assign(new Element(),{tag,textContent:text||'',className:cls||''});
let context={book:{book_id:'a',title:'甲',revision:7},chapter:{chapter_number:3},view:'manuscript',dirty:false,status:{}};
let requests=[];let resolveFetch;let delayed=false;
const api=async(path,body)=>{requests.push({path,body});if(!body){if(delayed)return new Promise(r=>resolveFetch=r);return {turns:[],protected_through:3}}return {id:'t',status:'queued'};};
const window={};
''' + source + r'''
(async()=>{
 const assistant=window.WenQuAssistant.create({element,node,api,getContext:()=>context,onApplied:async()=>{}});
 await assistant.open();
 element('assistant-message').value='世界观太小，<script>不要执行</script>';
 element('assistant-include-chapter').checked=true;
 await assistant.send();
 const post=requests.find(r=>r.body);assert.equal(post.path,'/api/books/a/assistant');assert.equal(post.body.expected_revision,7);assert.equal(post.body.chapter_number,3);
 assert.equal(requests.filter(r=>r.path.endsWith('/apply')).length,0);
 element('assistant-message').value='甲的未发送草稿';
 delayed=true; const pending=assistant.refresh();
 context={book:{book_id:'b',title:'乙',revision:2},view:'planning',status:{}};assistant.contextChanged();
 assert.equal(element('assistant-message').value,'');
 resolveFetch({turns:[{id:'old',message:'甲的回答',status:'complete',reply:'不得出现在乙'}],protected_through:3});await pending;
 assert.ok(!JSON.stringify(element('assistant-history').children).includes('不得出现在乙'));
 context={book:{book_id:'a',title:'甲',revision:7},view:'planning',status:{}};assistant.contextChanged();
 assert.equal(element('assistant-message').value,'甲的未发送草稿');
})().catch(e=>{console.error(e);process.exitCode=1});
'''], check=True)


def test_proposal_is_rendered_as_text_and_only_explicit_accept_applies():
    source = Path('story_core/web/assistant.js').read_text()
    subprocess.run(['node', '-e', r'''
const assert=require('node:assert/strict');
class Element {
 constructor(){this.children=[];this.value='';this.textContent='';this.listeners={};this.scrollTop=0;this.scrollHeight=0;this.disabled=false}
 append(...x){this.children.push(...x)} replaceChildren(...x){this.children=x}
 addEventListener(k,f){this.listeners[k]=f} setAttribute(){} focus(){}
 set innerHTML(x){throw Error('HTML injection sink used')}
}
const fields={};const element=id=>fields[id] ||= new Element();
const node=(tag,text,cls)=>Object.assign(new Element(),{tag,textContent:text||'',className:cls||''});
let requests=[], applied=0;const context={book:{book_id:'a',title:'书',revision:2},status:{},dirty:false};
const turn={id:'t',message:'拓展世界',status:'complete',reply:'<img src=x onerror=alert(1)>',proposal:{direction:'世界扩展方案',brief:{premise:'新提案'}},can_apply:true};
const api=async(path,body)=>{requests.push([path,body]);return body?{}:{turns:[turn],protected_through:20}};
const window={};
''' + source + r'''
(async()=>{
 const assistant=window.WenQuAssistant.create({element,node,api,getContext:()=>context,onApplied:async()=>{applied++}});
 await assistant.open();
 const walk=e=>[e,...e.children.flatMap(walk)];
 const items=walk(element('assistant-history'));
 assert.ok(items.some(e=>e.textContent===turn.reply));assert.equal(applied,0);
 let button=items.find(e=>e.textContent==='确认方案并重排后续章纲');
 context.status={run:{status:'running'}};assistant.contextChanged();assert.equal(button.disabled,true);await button.listeners.click();assert.equal(applied,0);
 context.status={};assistant.contextChanged();assert.equal(button.disabled,false);
 context.dirty=true;await button.listeners.click();assert.equal(applied,0);assert.ok(element('assistant-error').textContent.includes('先保存'));
 context.dirty=false;await button.listeners.click();assert.equal(applied,1);
 assert.equal(requests.filter(r=>r[0].endsWith('/apply')).length,1);
})().catch(e=>{console.error(e);process.exitCode=1});
'''],check=True)
