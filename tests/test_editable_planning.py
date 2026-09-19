from copy import deepcopy

import pytest

from story_core.errors import StoryError
from test_workflow import make, to_stage, result_for, finish


def test_extend_finished_book_preserves_prose_and_applies_future_length(tmp_path):
    service, bid = make(tmp_path, chapters=1)
    finish(service, bid)
    book = service.get_book(bid)
    original = deepcopy(book['chapters'])
    brief, plan = deepcopy(book['brief']), deepcopy(book['plan'])
    brief['title'] = '新书名'
    brief['characters'][0]['name'] = '新角色名'
    plan['chapters'].append({**plan['chapters'][0], 'number': 2})
    plan.update(volumes=[dict(title='第一卷', start_chapter=1, end_chapter=2,
                             goal='寻找父亲', conflict='旧账', climax='找到线索')],
                payoff_design=['解谜兑现'], climax='重逢', conflicts=['追查与隐瞒'], reversals=['证人反转'])
    saved = service.update_story_bible(bid, brief, plan, book['revision'],
                                     settings={'chapter_count': 2, 'target_words': 200})
    assert saved['settings']['target_words'] == 200
    assert service.get_book(bid)['chapters'] == original
    assert saved['renamed_chapters'] == 0
    service.start_run(bid)
    task = service.next_task(bid)
    assert task['chapter_number'] == 2 and task['stage'] == 'draft'
    assert task['input']['length_requirement']['target'] == 200
    assert task['input']['story_structure']['climax'] == '重逢'
    assert task['input']['story_structure']['volumes'][0]['title'] == '第一卷'


def test_edit_invalidates_leased_task_and_worker(tmp_path):
    from story_core.runtime import WorkbenchRuntime
    service, bid = make(tmp_path)
    to_stage(service, bid, 'draft')
    service.control(bid, 'pause')
    service.control(bid, 'resume')
    runtime = WorkbenchRuntime(service)
    worker = runtime.reserve(bid, 'test')
    task = service.next_task(bid, worker['id'])
    book = service.get_book(bid)
    service.update_story_bible(bid, book['brief'], book['plan'], book['revision'], settings={'target_words': 100})
    assert runtime.latest(bid)['status'] == 'cancelled'
    assert not service.task_active(task['task_id'], task['lease_id'])
    with pytest.raises(StoryError):
        service.submit_task(task['task_id'], task['lease_id'], result_for(task), worker['id'])


@pytest.mark.parametrize('settings', [{'chapter_count': 1}, {'target_words': True}, {'target_words': 49}, {'unknown': 1}])
def test_invalid_settings_edit_is_atomic(tmp_path, settings):
    service, bid = make(tmp_path)
    to_stage(service, bid, 'draft')
    book = service.get_book(bid)
    with pytest.raises(StoryError):
        service.update_story_bible(bid, book['brief'], book['plan'], book['revision'], settings=settings)
    assert service.get_book(bid) == book


def test_edit_releases_awaiting_author_run_without_changing_committed_version(tmp_path):
    service, bid = make(tmp_path, chapters=1, review_mode='bounded')
    finish(service, bid)
    assert service.status(bid)['run']['status'] == 'awaiting_author'
    book = service.get_book(bid)
    saved = service.update_story_bible(bid, book['brief'], book['plan'], book['revision'])
    assert saved['run_cancelled']
    assert service.status(bid)['run']['status'] == 'cancelled'
    assert service.get_book(bid)['chapters'] == book['chapters']


def test_http_settings_and_volume_validation(tmp_path):
    from fastapi.testclient import TestClient
    from story_core.http import create_app
    service, bid = make(tmp_path)
    to_stage(service, bid, 'draft')
    book = service.get_book(bid)
    with TestClient(create_app(tmp_path)) as client:
        payload = {'brief': book['brief'], 'plan': book['plan'], 'expected_revision': book['revision'],
                   'settings': {'target_words': 120}}
        response = client.post(f'/api/books/{bid}/story-bible', json=payload)
        assert response.status_code == 200
        assert response.json()['settings']['target_words'] == 120
        assert client.post(f'/api/books/{bid}/story-bible', json=payload).status_code != 200
        payload['expected_revision'] = response.json()['revision']
        payload['plan']['volumes'] = [dict(title='卷一', start_chapter=1, end_chapter=3,
                                         goal='目标', conflict='冲突', climax='高潮')]
        assert client.post(f'/api/books/{bid}/story-bible', json=payload).status_code != 200


def test_legacy_volume_remains_editable_and_reaches_draft(tmp_path):
    service, bid = make(tmp_path)
    to_stage(service, bid, 'draft')
    book = service.get_book(bid)
    plan = deepcopy(book['plan'])
    plan['volumes'] = [dict(number=1, title='旧卷', range=[1, 2], goal='寻找', world_expansion='旧世界', climax='重逢')]
    service.update_story_bible(bid, book['brief'], plan, book['revision'])
    service.start_run(bid)
    assert service.next_task(bid)['input']['story_structure']['volumes'][0]['range'] == [1, 2]


def test_planning_editor_round_trip_preserves_dependencies_and_revision(tmp_path):
    import subprocess
    from pathlib import Path
    script = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const fields=new Map();
const makeNode=()=>({value:'',children:[],append(...items){this.children.push(...items)},replaceChildren(...items){this.children=items}});
const ctx=vm.createContext({Set,document:{createElement:makeNode,getElementById(id){if(!fields.has(id))fields.set(id,makeNode());return fields.get(id);}}});
vm.runInContext(fs.readFileSync(process.argv[2],'utf8').replace(/initialize\(\);\s*$/,''),ctx);
vm.runInContext(`state.book={book_id:'book',revision:4,settings:{chapter_count:2,target_words:100},
brief:{title:'书',premise:'核心',audience:'读者',pov:'视角',style:'文风',ending:'结局',characters:[{name:'人',desire:'欲',fear:'惧',boundary:'界',voice:'声'}]},
plan:{chapters:[{number:1,title:'章',goal:'目标',conflict:'冲突',change:'改变',payoff:'回报',emotion:'情绪',participants:['人'],required_fact_ids:['f1'],story_time:0}],promises:[],volumes:[],payoff_design:['兑现'],climax:'高潮',conflicts:['冲突'],reversals:['反转']}};
fillStoryBibleEditor(); state.book.revision=5;`,ctx);
fields.get('bible-target-words').value='250';
const result=JSON.parse(vm.runInContext('JSON.stringify(storyBiblePayload())',ctx));
assert.equal(result.settings.target_words,250);
assert.equal(result.expected_revision,4);
assert.deepEqual(result.plan.chapters[0].required_fact_ids,['f1']);
assert.equal(result.plan.chapters[0].story_time,0);
assert.deepEqual(result.plan.payoff_design,['兑现']);
assert.equal(result.apply_character_renames,false);
vm.runInContext('renderPlanning()',ctx);
const rendered=JSON.stringify(fields.get('plan'));
for(const label of ['全书高潮','爽点设计','卷纲','核心冲突','关键反转','兑现']) assert(rendered.includes(label));
vm.runInContext("state.book.plan.volumes=[{number:1,title:'卷',range:[1,2],goal:'目标',world_expansion:'',climax:''}];fillStoryBibleEditor()",ctx);
const legacy=JSON.parse(vm.runInContext('JSON.stringify(storyBiblePayload())',ctx));
assert.equal(legacy.plan.volumes[0].world_expansion,'');
assert.deepEqual(legacy.plan.volumes[0].range,[1,2]);
'''
    js = tmp_path / 'editor.cjs'
    js.write_text(script)
    subprocess.run(['node', str(js), str(Path(__file__).parents[1] / 'story_core/web/app.js')], check=True)
