from pathlib import Path
import subprocess


def test_paused_planning_exposes_progress_reason_and_recovery_in_rail():
    src = Path('story_core/web/app.js').read_text()
    start = src.index('function renderPlanningProgress(')
    end = src.index('\nfunction ', start + 1)
    subprocess.run(['node', '-e', '''
const assert=require('node:assert/strict');const fields={};const element=id=>fields[id] ||= {};
''' + src[start:end] + '''
renderPlanningProgress({worker_running:false,settings_planning:{status:'paused',planned_chapters:60,protected_chapters:20,target_settings:{chapter_count:200},reason:'卷纲缺失'}});
assert.equal(element('planning-rail').hidden,false);
assert.equal(element('resume-planning-rail').hidden,false);
assert.ok(element('planning-rail-detail').textContent.includes('60 / 200'));
assert.ok(element('planning-rail-detail').textContent.includes('61–80'));
assert.ok(element('planning-rail-detail').textContent.includes('卷纲缺失'));
renderPlanningProgress({worker_running:true,settings_planning:{status:'running',planned_chapters:80,target_settings:{chapter_count:200}}});
assert.equal(element('resume-planning-rail').hidden,true);
assert.ok(element('planning-rail-detail').textContent.includes('81–100'));
renderPlanningProgress({settings_planning:null});assert.equal(element('planning-rail').hidden,true);
'''],check=True)
