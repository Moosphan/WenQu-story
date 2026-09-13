import json
import time

import pytest


def make(tmp_path, chapters=2, **run_options):
    from story_core.service import StoryService
    service = StoryService(tmp_path)
    book = service.open_book('写一部修理铺兄妹和失踪父亲的悬疑小说', title='旧收音机', chapter_count=chapters, target_words=80)
    run_options.setdefault('review_mode', 'legacy')
    service.start_run(book['book_id'], **run_options)
    return service, book['book_id']


def result_for(task, block=False):
    if task.get('input', {}).get('review_adjudication'):
        return {'decisions': [{'index': i, 'decision': 'confirmed_conflict', 'evidence_paragraph': 1, 'reason': '测试桩确认原冲突'} for i, issue in enumerate(task['input']['review_adjudication']['issues']) if issue['severity'] in ('blocker','major')]}
    stage = task['stage']
    if stage == 'brief':
        return dict(title='旧收音机', premise='兄妹修复父亲留下的收音机，找到失踪原因。',
                    audience='悬疑读者', pov='第三人称限知，跟随沈知秋', style='克制、具体、人物口语',
                    ending='兄妹查清事故，父亲回家。', characters=[dict(name='沈知秋', desire='找父亲', fear='失去妹妹', boundary='不牺牲妹妹', voice='短句，回避直说')])
    if stage == 'outline':
        n = task['input']['settings']['chapter_count']
        return dict(chapters=[dict(number=i, title=f'回音{i}', goal='修好收音机', conflict='零件不足', change='听到父亲留言', payoff='得到线索', emotion='从忍耐到期望', pov='沈知秋') for i in range(1,n+1)], promises=[])
    if stage in ('draft','revise'):
        return dict(title='回音', body='沈知秋拧下最后一颗螺丝。妹妹端着冷掉的面站在门边，手背上还沾着面粉。他没抬头，先把桌边空出一小块。收音机忽然响了。熟悉的咳嗽声传来，他手里的螺丝刀落在桌上。妹妹把碗放下，抓住了他的衣袖。')
    if stage == 'extract':
        return dict(memories=[dict(kind='fact', key='收音机', value='传来熟悉咳嗽声', evidence='熟悉的咳嗽声传来', visibility='reader')])
    if stage in ('continuity','reader','arc','ending'):
        return dict(verdict='revise' if block else 'pass', issues=[dict(severity='blocker', dimension='continuity', evidence='收音机', explanation='前文证据冲突', suggestion='改正')] if block else [], notes='测试桩评审，非文学质量证明')
    raise AssertionError(stage)


def test_brief_guidance_asks_the_model_to_avoid_generic_name_defaults():
    from story_core.prompts import instruction

    guidance = instruction('brief')
    assert '林默' in guidance
    assert '作者明确指定' in guidance


def step(service, book, **kw):
    task=service.next_task(book)
    assert task.get('task_id'), task
    response=service.submit_task(task['task_id'],task['lease_id'],result_for(task,**kw))
    return task,response


def to_stage(service, book, stage):
    for _ in range(30):
        task=service.next_task(book)
        if task.get('stage') == stage:
            return task
        assert task.get('task_id'), task
        service.submit_task(task['task_id'],task['lease_id'],result_for(task))
    raise AssertionError('stage not reached')


def finish(service, book):
    for _ in range(100):
        task=service.next_task(book)
        if not task.get('task_id'):
            return task
        service.submit_task(task['task_id'],task['lease_id'],result_for(task))
    raise AssertionError('unbounded loop')


def test_full_book_resume_and_export(tmp_path):
    from story_core.service import StoryService
    service,book=make(tmp_path)
    for _ in range(4): step(service,book)
    service=StoryService(tmp_path)
    assert finish(service,book)['status']=='complete'
    assert len(service.get_book(book)['chapters'])==2
    manifest=service.export(book)
    assert manifest['complete'] is True
    assert any(x['name']=='manuscript.txt' for x in manifest['files'])


def test_idempotent_submit_and_reject_changed_replay(tmp_path):
    from story_core.errors import StoryError
    service,book=make(tmp_path)
    task,response=step(service,book)
    assert service.submit_task(task['task_id'],task['lease_id'],result_for(task))==response
    with pytest.raises(StoryError) as error:
        service.submit_task(task['task_id'],task['lease_id'],{'title':'篡改'})
    assert error.value.code=='IDEMPOTENCY_CONFLICT'


def test_lease_owner_and_expiry_fence(tmp_path):
    from story_core.errors import StoryError
    service,book=make(tmp_path)
    task=service.next_task(book,worker_id='one')
    assert service.next_task(book,worker_id='two')['status']=='leased'
    with service.store.write(book) as conn:
        conn.execute('UPDATE tasks SET lease_until=? WHERE id=?',(time.time()-1,task['task_id']))
    newer=service.next_task(book,worker_id='two')
    assert newer['lease_id']!=task['lease_id']
    with pytest.raises(StoryError) as error:
        service.submit_task(task['task_id'],task['lease_id'],result_for(task),worker_id='one')
    assert error.value.code=='INVALID_LEASE'


def test_draft_not_canon_until_both_reviews_pass(tmp_path):
    service,book=make(tmp_path)
    task=to_stage(service,book,'reader')
    assert service.get_book(book)['chapters']==[]
    assert not service.query(book,'收音机')['hits']
    service.submit_task(task['task_id'],task['lease_id'],result_for(task))
    assert service.get_book(book)['chapters']==[]
    task=service.next_task(book)
    assert task['stage']=='reader' and task['input']['reader_profile']['id']=='logic_reader'
    service.submit_task(task['task_id'],task['lease_id'],result_for(task))
    assert len(service.get_book(book)['chapters'])==1


def test_memory_review_repairs_extraction_without_rewriting_prose(tmp_path):
    service, book = make(tmp_path)
    task = to_stage(service, book, 'continuity')
    candidate = service.status(book)['run']['candidate']
    result = result_for(task, block=True)
    result['repair_target'] = 'memory'
    result['issues'][0].update(dimension='memory', explanation='引文不能支撑抽取断言', suggestion='保留不确定性')
    service.submit_task(task['task_id'], task['lease_id'], result)
    following = service.next_task(book)
    assert following['stage'] == 'extract'
    assert following['input']['candidate'] == candidate
    assert following['input']['extraction_feedback']['repair_target'] == 'memory'
    assert not service.get_book(book)['chapters']


def test_extraction_contract_is_bounded_and_requires_verbatim_evidence():
    from story_core.prompts import instruction
    from story_core.schemas import SCHEMAS
    assert SCHEMAS['extract']['properties']['memories']['maxItems'] == 30
    assert '逐字复制候选正文中的连续片段' in instruction('extract')


def test_reader_cannot_request_memory_only_repair(tmp_path):
    from story_core.errors import StoryError
    service, book = make(tmp_path)
    task = to_stage(service, book, 'reader')
    result = result_for(task, block=True)
    result['repair_target'] = 'memory'
    with pytest.raises(StoryError):
        service.submit_task(task['task_id'], task['lease_id'], result)


def test_reader_payload_isolated_from_author_and_query_bound(tmp_path):
    from story_core.errors import StoryError
    service,book=make(tmp_path)
    task=to_stage(service,book,'reader')
    data=json.dumps(task['input'],ensure_ascii=False)
    assert '父亲回家' not in data
    assert 'plan' not in task['input'] and 'brief' not in task['input'] and 'project' not in task['input']
    with pytest.raises(StoryError) as error:
        service.query(book,'父亲',role='author',task_id=task['task_id'],lease_id=task['lease_id'])
    assert error.value.code=='INVALID_SCOPE'


def test_leased_tasks_include_bounded_context_manifest(tmp_path):
    service, book = make(tmp_path)
    author = service.next_task(book)
    manifest = author['input']['context_manifest']
    assert manifest == {
        'role': 'author', 'through_chapter': 0, 'book_revision': 0,
        'canonical_sources': [], 'recent_chapter_sources': [],
    }
    reader = to_stage(service, book, 'reader')
    reader_manifest = reader['input']['context_manifest']
    assert reader_manifest['role'] == 'reader'
    assert reader_manifest['through_chapter'] == 1
    assert reader_manifest['book_revision'] >= 2
    assert all(source['chapter_number'] < 1 for source in reader_manifest['canonical_sources'])
    assert 'plan' not in reader['input'] and 'canonical_memory' not in reader['input']


def test_author_task_uses_required_and_supplementary_context_layers(tmp_path):
    service, book = make(tmp_path)
    reader = to_stage(service, book, 'reader')
    service.submit_task(reader['task_id'], reader['lease_id'], result_for(reader))
    reader = service.next_task(book)
    service.submit_task(reader['task_id'], reader['lease_id'], result_for(reader))
    task = service.next_task(book)
    assert task['stage'] == 'draft' and task['chapter_number'] == 2
    data = task['input']
    assert [memory['key'] for memory in data['required_memory']] == ['收音机']
    assert data['canonical_memory'] == data['required_memory']
    assert data['supplementary_memory'] == []
    source = next(item for item in data['context_manifest']['canonical_sources'] if item['key'] == '收音机')
    assert source['tier'] == 'required'


def test_author_task_pov_filters_foreign_knowledge_and_manifest(tmp_path):
    from story_core.memory import index_chapter
    service, book = make(tmp_path)
    reader = to_stage(service, book, 'reader')
    service.submit_task(reader['task_id'], reader['lease_id'], result_for(reader))
    reader = service.next_task(book)
    service.submit_task(reader['task_id'], reader['lease_id'], result_for(reader))
    chapter = service.get_book(book)['chapters'][0]
    with service.store.write(book) as conn:
        index_chapter(conn, book, 1, chapter['version_id'], chapter['body'], [
            dict(kind='fact', key='收音机', value='传来熟悉咳嗽声', evidence='熟悉的咳嗽声传来', visibility='reader'),
            dict(kind='knowledge', key='沈知秋听见父亲咳嗽', value='沈知秋确认声音熟悉', evidence='熟悉的咳嗽声传来', visibility='reader', owner='沈知秋'),
            dict(kind='knowledge', key='妹妹知道父亲藏身处', value='妹妹早已知道父亲位置', evidence='妹妹把碗放下', visibility='reader', owner='妹妹'),
        ])
    task = service.next_task(book)
    data = task['input']
    assert data['pov_context'] == {'pov': '沈知秋', 'withheld_knowledge_count': 1}
    payload = json.dumps(data, ensure_ascii=False)
    assert '妹妹知道父亲藏身处' not in payload
    assert '妹妹早已知道父亲位置' not in payload
    assert '沈知秋听见父亲咳嗽' in payload
    assert all(item['key'] != '妹妹知道父亲藏身处' for item in data['context_manifest']['canonical_sources'])


def test_chapter_commits_only_after_two_isolated_reader_profiles(tmp_path):
    service, book = make(tmp_path, chapters=1)
    target = to_stage(service, book, 'reader')
    assert target['input']['reader_profile']['id'] == 'target_reader'
    assert target['input']['reader_profile']['label'] == '目标题材读者'
    assert 'logic_reader' not in json.dumps(target['input'], ensure_ascii=False)
    service.submit_task(target['task_id'], target['lease_id'], result_for(target))
    assert service.get_book(book)['chapters'] == []
    logic = service.next_task(book)
    assert logic['stage'] == 'reader'
    assert logic['input']['reader_profile']['id'] == 'logic_reader'
    assert logic['input']['reader_profile']['label'] == '逻辑敏感读者'
    assert 'target_reader' not in json.dumps(logic['input'], ensure_ascii=False)
    service.submit_task(logic['task_id'], logic['lease_id'], result_for(logic))
    assert len(service.get_book(book)['chapters']) == 1
    reader_reviews = [review for review in service.review_history(book, chapter_number=1) if review['stage'] == 'reader']
    assert [(review['reader_profile'], review['profile_attempt']) for review in reader_reviews] == [
        ('target_reader', 1), ('logic_reader', 1),
    ]


def test_due_mandatory_promise_blocks_incomplete_extraction(tmp_path):
    from story_core.errors import StoryError
    service, book = make(tmp_path, chapters=1)
    task = service.next_task(book)
    service.submit_task(task['task_id'], task['lease_id'], result_for(task))
    task = service.next_task(book)
    outline = result_for(task)
    outline['promises'] = [dict(key='父亲留言', setup_chapter=1, due_chapter=1,
                                resolution='收音机里听清父亲留言', mandatory=True)]
    service.submit_task(task['task_id'], task['lease_id'], outline)
    task = service.next_task(book)
    service.submit_task(task['task_id'], task['lease_id'], result_for(task))
    task = service.next_task(book)
    assert task['stage'] == 'extract'
    assert task['input']['promise_obligations'] == [
        {'key': '父亲留言', 'due_chapter': 1, 'resolution': '收音机里听清父亲留言', 'urgency': 'due'},
    ]
    with pytest.raises(StoryError) as caught:
        service.submit_task(task['task_id'], task['lease_id'], result_for(task))
    assert caught.value.code == 'PROMISE_DUE'
    result = result_for(task)
    result['memories'].append(dict(kind='promise', key='父亲留言', value='收音机里传来父亲留言',
                                   evidence='收音机忽然响了', visibility='reader', status='paid', mandatory=True))
    response = service.submit_task(task['task_id'], task['lease_id'], result)
    assert response['next_stage'] == 'continuity'


def test_five_chapter_arc_checkpoint_is_bounded_and_advances_on_pass(tmp_path):
    service, book = make(tmp_path, chapters=6)
    arc = None
    for _ in range(60):
        task = service.next_task(book)
        assert task.get('task_id'), task
        if task['stage'] == 'arc':
            arc = task
            break
        service.submit_task(task['task_id'], task['lease_id'], result_for(task))
    assert arc is not None
    data = arc['input']
    assert [chapter['chapter_number'] for chapter in data['arc_chapters']] == [1, 2, 3, 4, 5]
    assert [chapter['number'] for chapter in data['arc_plan']] == [1, 2, 3, 4, 5]
    assert 'candidate' not in data
    response = service.submit_task(arc['task_id'], arc['lease_id'], result_for(arc))
    assert response['next_stage'] == 'draft'
    next_task = service.next_task(book)
    assert next_task['stage'] == 'draft' and next_task['chapter_number'] == 6


def test_failed_arc_checkpoint_needs_author_attention(tmp_path):
    service, book = make(tmp_path, chapters=6)
    for _ in range(60):
        task = service.next_task(book)
        assert task.get('task_id'), task
        if task['stage'] == 'arc':
            break
        service.submit_task(task['task_id'], task['lease_id'], result_for(task))
    response = service.submit_task(task['task_id'], task['lease_id'], result_for(task, block=True))
    assert response['status'] == 'needs_attention'
    assert '故事弧审校未通过' in service.status(book)['run']['reason']


def test_revision_limit_needs_attention_not_infinite_rewrite(tmp_path):
    service,book=make(tmp_path,max_revisions=1)
    for _ in range(30):
        task=service.next_task(book)
        if not task.get('task_id'): break
        service.submit_task(task['task_id'],task['lease_id'],result_for(task,block=task['stage']=='continuity'))
    assert task['status']=='needs_attention'
    assert service.get_book(book)['chapters']==[]


def test_budget_stops_before_task(tmp_path):
    service,book=make(tmp_path,budget_tokens=1)
    assert service.next_task(book)['status']=='needs_attention'
    assert service.status(book)['run']['steps']==0


def test_pause_invalidates_inflight_and_resume_continues(tmp_path):
    from story_core.errors import StoryError
    service,book=make(tmp_path)
    task=service.next_task(book)
    service.control(book,'pause')
    with pytest.raises(StoryError):
        service.submit_task(task['task_id'],task['lease_id'],result_for(task))
    assert service.next_task(book)['status']=='paused'
    service.control(book,'resume')
    new=service.next_task(book)
    assert new['lease_id']!=task['lease_id']


def test_early_edit_invalidates_downstream_and_export(tmp_path):
    from story_core.errors import StoryError
    service,book=make(tmp_path)
    finish(service,book)
    body=result_for({'stage':'draft'})['body'].replace('冷掉','温热')
    service.control(book,'revise',chapter_number=1,body=body)
    task=to_stage(service,book,'reader')
    service.submit_task(task['task_id'],task['lease_id'],result_for(task))
    task=service.next_task(book)
    service.submit_task(task['task_id'],task['lease_id'],result_for(task))
    assert service.get_book(book)['chapters'][1]['status']=='needs_review'
    with pytest.raises(StoryError): service.export(book)
    finish(service,book)
    assert service.export(book)['complete']


def test_atomic_commit_rollback_on_index_failure(tmp_path,monkeypatch):
    import story_core.service as module
    service,book=make(tmp_path)
    task=to_stage(service,book,'reader')
    revision=service.get_book(book)['revision']
    service.submit_task(task['task_id'],task['lease_id'],result_for(task))
    task=service.next_task(book)
    assert task['input']['reader_profile']['id']=='logic_reader'
    def fail(*a,**kw): raise RuntimeError('crash during memory write')
    original=module.index_chapter
    monkeypatch.setattr(module,'index_chapter',fail)
    with pytest.raises(RuntimeError): service.submit_task(task['task_id'],task['lease_id'],result_for(task))
    assert service.get_book(book)['revision']==revision
    assert service.get_book(book)['chapters']==[]
    monkeypatch.setattr(module,'index_chapter',original)
    service.submit_task(task['task_id'],task['lease_id'],result_for(task))
    assert len(service.get_book(book)['chapters'])==1


def test_minor_review_note_is_recorded_without_blocking_candidate_progress(tmp_path):
    service, book = make(tmp_path)
    task = to_stage(service, book, 'continuity')
    result = result_for(task)
    result.update(verdict='revise', issues=[dict(
        severity='minor', dimension='记忆措辞', evidence='收音机',
        explanation='术语可保留不确定性', suggestion='后续记忆抽取避免下定论。'
    )])
    service.submit_task(task['task_id'], task['lease_id'], result)
    following = service.next_task(book)
    assert following['stage'] == 'reader'
    assert service.status(book)['run']['attempts'] == 0


def test_reader_major_feedback_is_advisory_until_it_is_a_blocker(tmp_path):
    service, book = make(tmp_path, chapters=1)
    task = to_stage(service, book, 'reader')
    result = result_for(task)
    result.update(verdict='revise', issues=[dict(
        severity='major', dimension='节奏建议', evidence='收音机',
        explanation='中段重复解释拖慢阅读', suggestion='压缩重复段落。'
    )])
    service.submit_task(task['task_id'], task['lease_id'], result)
    following = service.next_task(book)
    assert following['stage'] == 'reader'
    assert following['input']['reader_profile']['id'] == 'logic_reader'
