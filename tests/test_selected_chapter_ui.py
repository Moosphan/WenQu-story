import subprocess
from pathlib import Path


def test_sidebar_selection_is_not_background_task():
    source=Path('story_core/web/app.js').read_text()
    code=source[source.index('function selectedQualityLabel'):source.index('function controls()')]
    script='''
const vm=require('node:vm');const nodes={};
const state={chapter:{chapter_number:1,title:'第一章',status:'committed',quality:{release_reason:'review_limit',author_status:'pending'}},status:{run:{chapter_number:3,status:'paused'}},busy:new Set()};
vm.runInNewContext(process.argv[1]+';renderSelectedChapter();',{state,element:id=>nodes[id]||(nodes[id]={})});
if(!nodes['selected-chapter-title'].textContent.includes('第 1 章'))throw Error('wrong chapter');
if(!nodes['selected-chapter-status'].textContent.includes('上限'))throw Error('missing release label');
if(!nodes['selected-action-hint'].textContent.includes('第 3 章'))throw Error('background task unidentified');
'''
    result=subprocess.run(['node','-e',script,code],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
