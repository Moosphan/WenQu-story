from pathlib import Path
import subprocess


def test_resume_retry_and_scope_are_derived_from_actual_task():
    source = Path('story_core/web/app.js').read_text()
    start = source.index('function workflowPresentation(')
    end = source.index('\nfunction ', start + 1)
    script = source[start:end]
    subprocess.run(['node', '-e', "const assert=require('node:assert/strict');const stages={draft:'写作正文',continuity:'核对连续性',revise:'修改候选稿'};\n" + script + r'''
const run={status:'paused',stage:'draft',chapter_number:4,end_chapter:40};
const old={chapter_number:3,stage:'continuity',outcome:'succeeded',failure:null};
let v=workflowPresentation({run,execution:old});
assert.equal(v.label,'开始写第 4 章');
assert.equal(v.range,'从第 4 章继续至第 40 章');
assert.ok(!v.notice.includes('重试'));
assert.equal(v.existing,true);
v=workflowPresentation({run,execution:{...old,outcome:'failed',failure:{code:'INVALID_RESULT'}}});
assert.equal(v.label,'开始写第 4 章'); // Previous chapter's failure is not this task.
v=workflowPresentation({run:{...run,stage:'continuity'},execution:{chapter_number:4,stage:'continuity',outcome:'failed',failure:{code:'INVALID_RESULT'}}});
assert.equal(v.label,'重试第 4 章 · 核对连续性');
assert.ok(v.notice.includes('重试'));
v=workflowPresentation({run:{...run,stage:'revise'}});
assert.equal(v.label,'继续第 4 章 · 修改候选稿');
v=workflowPresentation({run,worker_running:true});assert.ok(v.label.includes('写作正文中'));
v=workflowPresentation({run,active_task:{chapter_number:4}});assert.equal(v.label,'等待宿主提交第 4 章');
v=workflowPresentation({run:{...run,status:'awaiting_author'}});assert.equal(v.label,'等待作者确认第 4 章');
v=workflowPresentation({run:{...run,status:'batch_complete'}});assert.equal(v.existing,false);
'''], check=True)


def test_persisted_range_replaces_new_batch_selector():
    source = Path('story_core/web/app.js').read_text()
    html = Path('story_core/web/index.html').read_text()
    assert 'id="resume-range"' in html
    assert "element('batch-size').hidden = presentation.existing" in source
    assert "notice(presentation.notice)" in source
