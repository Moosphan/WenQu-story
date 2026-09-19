import pytest
from test_workflow import make, result_for


def test_project_metadata_is_versioned_and_kept_out_of_story_canon(tmp_path):
    from story_core.errors import StoryError
    from story_core.service import StoryService

    service = StoryService(tmp_path)
    book = service.open_book('给我开一本灵植修仙文', title='灵植仙途')
    saved = service.update_project_metadata(book['book_id'], {
        'platform': 'fanqie', 'length_band': 'long', 'narrative_mode': 'limited_third',
        'genre_tags': ['修仙', '种田', '金手指'], 'target_reader': '喜欢成长与经营感的男频读者',
        'content_boundaries': '不写后宫，不靠路人震惊推进爽点。',
        'seed_characters': [{'name': '陆沉', 'role': '主角', 'note': '谨慎、护短，欠下宗门灵石债。'}],
        'world_rules': [{'name': '灵植催熟', 'rule': '每月只能使用一次，代价是折损寿元。'}],
    }, expected_revision=0)

    assert saved['project']['platform'] == 'fanqie'
    assert saved['project']['seed_characters'][0]['name'] == '陆沉'
    assert service.get_book(book['book_id'])['brief'] is None
    assert service.get_book(book['book_id'])['project']['world_rules'][0]['name'] == '灵植催熟'
    with pytest.raises(StoryError) as error:
        service.update_project_metadata(book['book_id'], {}, expected_revision=0)
    assert error.value.code == 'STALE_REVISION'


def test_project_metadata_rejects_unknown_fields_and_invalid_seed_material(tmp_path):
    from story_core.errors import StoryError
    from story_core.service import StoryService

    service = StoryService(tmp_path)
    book = service.open_book('开书')
    with pytest.raises(StoryError) as error:
        service.update_project_metadata(book['book_id'], {'platform': 'unknown-platform'})
    assert error.value.code == 'INVALID_REQUEST'
    with pytest.raises(StoryError) as error:
        service.update_project_metadata(book['book_id'], {'seed_characters': [{'name': '', 'role': '主角', 'note': 'x'}]})
    assert error.value.code == 'INVALID_REQUEST'


def test_project_metadata_is_available_over_http(tmp_path):
    from fastapi.testclient import TestClient
    from story_core.http import create_app

    with TestClient(create_app(tmp_path)) as client:
        opened = client.post('/api/books', json={
            'request': '给我开本悬疑书', 'title': '雨夜旧宅',
            'project': {'platform': 'qidian', 'genre_tags': ['悬疑', '推理']},
        }).json()
        assert client.get(f"/api/books/{opened['book_id']}/project").json()['project']['platform'] == 'qidian'
        updated = client.post(f"/api/books/{opened['book_id']}/project", json={
            'metadata': {'platform': 'fanqie', 'length_band': 'medium'}, 'expected_revision': 0,
        })
        assert updated.status_code == 200, updated.text
        assert updated.json()['project']['length_band'] == 'medium'


def test_opening_proposals_are_editable_suggestions_and_do_not_create_a_book(tmp_path):
    from story_core.service import StoryService

    service = StoryService(tmp_path)
    proposals = service.project_proposals('写一本修仙金手指小说，灵物会许愿。', {
        'platform': 'fanqie', 'genre_tags': ['修仙', '金手指'], 'content_boundaries': '不写后宫',
    })
    assert len(proposals['proposals']) == 3
    assert all(item['is_canon'] is False for item in proposals['proposals'])
    assert all(item['project']['platform'] == 'fanqie' for item in proposals['proposals'])
    assert service.list_books() == []


def test_opening_proposals_are_available_over_http(tmp_path):
    from fastapi.testclient import TestClient
    from story_core.http import create_app

    with TestClient(create_app(tmp_path)) as client:
        response = client.post('/api/proposals', json={'request': '开本都市悬疑', 'project': {'genre_tags': ['悬疑']}})
        assert response.status_code == 200, response.text
        assert len(response.json()['proposals']) == 3


def test_author_task_marks_project_material_as_non_canon_instruction(tmp_path):
    from story_core.service import StoryService

    service = StoryService(tmp_path)
    book = service.open_book('写一本修仙金手指小说', project={
        'platform': 'fanqie', 'narrative_mode': 'first_person',
        'target_reader': '喜欢成长和反转的读者', 'content_boundaries': '不写后宫。',
        'custom_notes': '开局先让主角吃亏再翻盘。',
        'seed_characters': [{'name': '林照夜', 'role': '主角', 'note': '欠债的杂役。'}],
    })
    service.start_run(book['book_id'])

    task = service.next_task(book['book_id'])

    assert task['stage'] == 'brief'
    assert task['input']['project']['narrative_mode'] == 'first_person'
    assert '作者资料' in task['input']['instruction']
    assert '尚非正史' in task['input']['instruction']
    assert '正文证据' in task['input']['instruction']


def test_story_bible_edit_is_versioned_and_cancels_stale_run(tmp_path):
    from copy import deepcopy
    from story_core.errors import StoryError

    service, book_id = make(tmp_path)
    brief_task = service.next_task(book_id)
    service.submit_task(brief_task['task_id'], brief_task['lease_id'], result_for(brief_task))
    outline_task = service.next_task(book_id)
    service.submit_task(outline_task['task_id'], outline_task['lease_id'], result_for(outline_task))
    book = service.get_book(book_id)
    brief, plan = deepcopy(book['brief']), deepcopy(book['plan'])
    brief['characters'][0]['name'] = '谢停舟'
    plan['chapters'][0]['goal'] = '修好收音机，逼出第一条失踪线索'

    saved = service.update_story_bible(book_id, brief, plan, expected_revision=book['revision'])

    assert saved['brief']['characters'][0]['name'] == '谢停舟'
    assert saved['plan']['chapters'][0]['goal'] == '修好收音机，逼出第一条失踪线索'
    assert saved['revision'] == book['revision'] + 1
    assert service.status(book_id)['run']['status'] == 'cancelled'
    with pytest.raises(StoryError) as error:
        service.update_story_bible(book_id, brief, plan, expected_revision=book['revision'])
    assert error.value.code == 'STALE_REVISION'


def test_idea_job_persists_real_provider_result_as_noncanon_cards(tmp_path):
    from story_core.service import StoryService

    class FakeProvider:
        def generate(self, task):
            assert task['input']['genre_hint'] == '修仙逆袭'
            return {'ideas': [
                {'title': '《借火人》', 'logline': '杂役替灵火还愿。', 'story_core': '每次借火都要烧掉一段旧记忆。', 'reader_promise': '代价和反转同时推进。'},
                {'title': '《山门夜航》', 'logline': '夜里送信才能修炼。', 'story_core': '每封信都牵出一桩旧债。', 'reader_promise': '关系线推动升级。'},
                {'title': '《尘骨簿》', 'logline': '替死人记账换寿命。', 'story_core': '账目越清，活人越危险。', 'reader_promise': '规则和悬念逐章兑现。'},
            ]}

    service = StoryService(tmp_path)
    job = service.create_idea_job('写本修仙金手指小说', '修仙逆袭', {'genre_tags': ['修仙']})
    completed = service.run_idea_job(job['job_id'], FakeProvider())

    assert completed['status'] == 'complete'
    assert all(card['is_canon'] is False for card in completed['ideas'])
    assert service.idea_job(job['job_id'])['ideas'][0]['title'] == '《借火人》'


def test_character_rename_updates_current_text_and_context_not_history(tmp_path):
    from copy import deepcopy
    from story_core.storage import dumps
    service, book_id = make(tmp_path)
    for _ in range(2):
        task = service.next_task(book_id)
        service.submit_task(task['task_id'], task['lease_id'], result_for(task))
    book = service.get_book(book_id)
    old = book['brief']['characters'][0]['name']
    with service.store.write(book_id) as conn:
        conn.execute('INSERT INTO chapter_versions VALUES (?,?,?,?,?,?)', ('old', book_id, 1, old + '出门', old + '看见灵草。', 1))
        conn.execute('INSERT INTO chapters VALUES (?,?,?,?)', (book_id, 1, 'old', 'committed'))
        conn.execute('INSERT INTO chunks VALUES (?,?,?,?,?,?)', ('chunk', book_id, 1, 'old', old + '看见灵草。', 0))
        conn.execute('INSERT INTO memories VALUES (?,?,?,?,?,?,?,?,?,?)', ('mem', book_id, 1, 'old', 'entity', old, old + '采药', old + '看见灵草。', 'public', dumps({'name': old})))
    brief, plan = deepcopy(book['brief']), deepcopy(book['plan'])
    brief['characters'][0]['name'] = '谢停舟'
    plan['chapters'][0]['goal'] = old + '采药'
    saved = service.update_story_bible(book_id, brief, plan, book['revision'], apply_character_renames=True)
    assert saved['renamed_characters'] == {old: '谢停舟'}
    assert saved['plan']['chapters'][0]['goal'] == '谢停舟采药'
    with service.store.read() as conn:
        current = conn.execute('SELECT v.* FROM chapters c JOIN chapter_versions v ON v.id=c.version_id WHERE c.book_id=?', (book_id,)).fetchone()
        assert current['body'] == '谢停舟看见灵草。'
        assert current['id'] != 'old'
        assert conn.execute("SELECT body FROM chapter_versions WHERE id='old'").fetchone()[0] == old + '看见灵草。'
        memory = conn.execute('SELECT * FROM memories WHERE book_id=?', (book_id,)).fetchone()
        assert memory['key'] == '谢停舟' and memory['version_id'] == current['id']
        assert conn.execute('SELECT text FROM chunks WHERE book_id=?', (book_id,)).fetchone()[0] == '谢停舟看见灵草。'
