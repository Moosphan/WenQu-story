from html.parser import HTMLParser
from pathlib import Path
import subprocess

WEB = Path(__file__).parents[1] / 'story_core/web'


def test_workspace_has_unique_actions_and_separate_review_views():
    class Document(HTMLParser):
        def __init__(self): super().__init__(); self.ids=[]; self.views=[]
        def handle_starttag(self, tag, attrs):
            attrs=dict(attrs)
            if 'id' in attrs: self.ids.append(attrs['id'])
            if 'data-review-panel' in attrs: self.views.append(attrs['data-review-panel'])
    page=Document(); page.feed((WEB/'index.html').read_text())
    assert len(page.ids)==len(set(page.ids))
    assert 'overview-continue' not in page.ids
    assert 'open-market' not in page.ids
    assert set(page.views)=={'reviews','versions','operations'}
    assert 'open-export' in page.ids and 'focus-writing' in page.ids


def test_review_navigation_only_shows_selected_panel():
    code=r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const make=(value)=>({dataset:{reviewPanel:value,reviewView:value},hidden:false,attrs:{},classList:{toggle(){}},setAttribute(k,v){this.attrs[k]=v;}});
const panels=['reviews','versions','operations'].map(make),buttons=['reviews','versions','operations'].map(make);
const ctx=vm.createContext({document:{querySelectorAll(q){return q==='[data-review-panel]'?panels:buttons;}},Set});
vm.runInContext(fs.readFileSync(process.argv[1],'utf8').replace(/initialize\(\);\s*$/,''),ctx);
vm.runInContext("switchReviewView('versions')",ctx);
assert.deepEqual(panels.map(x=>x.hidden),[true,false,true]);
assert.equal(buttons[1].attrs['aria-selected'],'true');
vm.runInContext("switchReviewView('operations')",ctx);
assert.deepEqual(panels.map(x=>x.hidden),[true,true,false]);
'''
    result=subprocess.run(['node','-e',code,str(WEB/'app.js')],capture_output=True,text=True)
    assert result.returncode==0,result.stderr


def test_primary_navigation_preserves_workspace_and_focus_state():
    code=r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
function item(id){return {id,dataset:{tab:id},hidden:false,attrs:{},classes:{},classList:{toggle(k,v){this[k]=v;}},setAttribute(k,v){this.attrs[k]=v;}};}
const ids=['overview','manuscript','planning','memory','activity'];
const panels=ids.map(item),buttons=ids.map(item),fields=new Map();
for(const id of ['workspace','focus-writing','app-shell'])fields.set(id,item(id));
const saved={};
const ctx=vm.createContext({requestAnimationFrame(callback){callback();},document:{querySelectorAll(q){return q==='[data-tab]'?buttons:panels;},getElementById(id){return fields.get(id);}},localStorage:{setItem(k,v){saved[k]=v;}},Set});
vm.runInContext(fs.readFileSync(process.argv[1],'utf8').replace(/initialize\(\);\s*$/,''),ctx);
vm.runInContext("state.book={book_id:'book-a'};state.focused=true;switchTab('manuscript');",ctx);
assert.equal(fields.get('app-shell').classList['writing-focused'],true);
assert.equal(fields.get('focus-writing').hidden,false);
assert.equal(saved['hulk-view-book-a'],'manuscript');
vm.runInContext("switchTab('planning');",ctx);
assert.equal(fields.get('app-shell').classList['writing-focused'],false);
assert.equal(fields.get('focus-writing').hidden,true);
assert.equal(fields.get('workspace').dataset.view,'planning');
assert.deepEqual(panels.map(p=>p.hidden),[true,true,false,true,true]);
'''
    result=subprocess.run(['node','-e',code,str(WEB/'app.js')],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
