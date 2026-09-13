import subprocess
from pathlib import Path


def test_drafts_are_scoped_and_stale_copies_are_not_restored():
    script=Path(__file__).parents[1]/'story_core/web/app.js'
    code=r'''
const vm=require('vm'),fs=require('fs'),assert=require('assert');
const fields=new Map(), saved=new Map();
const field=()=>({value:'',textContent:'',hidden:false,disabled:false,append(){},replaceChildren(){},addEventListener(){}});
const context=vm.createContext({document:{getElementById(id){if(!fields.has(id))fields.set(id,field());return fields.get(id)},createElement:field},localStorage:{getItem:k=>saved.get(k)||null,setItem:(k,v)=>saved.set(k,v),removeItem:k=>saved.delete(k)},Date,console});
vm.runInContext(fs.readFileSync(process.argv[1],'utf8').replace(/initialize\(\);\s*$/,''),context);
vm.runInContext(`state.book={book_id:'a',revision:1};state.chapter={chapter_number:1,title:'原题',body:'原文'};state.editRevision=1;element('chapter-title').value='改题';element('chapter-body').value='改文';element('revision-feedback').value='保留结尾';saveLocalDraft();`,context);
assert.equal(vm.runInContext('readLocalDraft().body',context),'改文');
assert.equal(vm.runInContext('localDraftMatches(readLocalDraft())',context),true);
vm.runInContext("state.chapter.body='新稿'",context);
assert.equal(vm.runInContext('localDraftMatches(readLocalDraft())',context),false);
vm.runInContext("state.book.book_id='b'",context);
assert.equal(vm.runInContext('readLocalDraft()',context),null);
'''
    result=subprocess.run(['node','-e',code,str(script)],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
