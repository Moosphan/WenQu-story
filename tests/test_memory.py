import json


def seed(store, book_id, number, body, memories=(), replace=False):
    from story_core.storage import uid
    from story_core.memory import index_chapter
    version = uid("v")
    with store.write(book_id) as conn:
        conn.execute("INSERT INTO chapter_versions(id,book_id,number,title,body,created_at) VALUES (?,?,?,?,?,?)",
                     (version, book_id, number, "章节", body, 0))
        conn.execute("INSERT OR REPLACE INTO chapters(book_id,number,version_id,status) VALUES (?,?,?,'committed')", (book_id, number, version))
        index_chapter(conn, book_id, number, version, body, list(memories))
    return version


def test_reader_excludes_future_and_private_memory(tmp_path):
    from story_core.storage import Store
    from story_core.memory import retrieve
    store = Store(tmp_path)
    book = store.create_book("开书", "书", {})["book_id"]
    seed(store, book, 1, "主角看到一把钥匙。", [dict(kind="fact", key="凶手", value="管家", evidence="一把钥匙", visibility="author")])
    seed(store, book, 5, "凶手是管家。")
    result = retrieve(store, book, "凶手", role="reader", through_chapter=1)
    assert "管家" not in json.dumps(result, ensure_ascii=False)
    assert all(x["chapter_number"] <= 1 for x in result["hits"])


def test_retrieval_never_leaks_other_book_or_replaced_text(tmp_path):
    from story_core.storage import Store
    from story_core.memory import retrieve
    store = Store(tmp_path)
    one = store.create_book("开书", "甲", {})["book_id"]
    two = store.create_book("开书", "乙", {})["book_id"]
    seed(store, one, 1, "父亲已经去世。")
    seed(store, two, 1, "父亲藏着百万黄金。")
    seed(store, one, 1, "父亲只是失踪。", replace=True)
    text = json.dumps(retrieve(store, one, "父亲"), ensure_ascii=False)
    assert "只是失踪" in text
    assert "去世" not in text
    assert "百万黄金" not in text


def test_event_search_has_source_and_chinese_aliases(tmp_path):
    from story_core.storage import Store
    from story_core.memory import retrieve
    store = Store(tmp_path)
    book = store.create_book("开书", "书", {})["book_id"]
    version = seed(store, book, 1, "沈知秋把旧收音机递给妹妹。", [dict(kind="entity", key="沈知秋", value="修理铺老板，别名沈老板", evidence="沈知秋", visibility="reader")])
    hits = retrieve(store, book, "沈老板")['hits']
    assert hits
    assert any(x['source']['version_id'] == version for x in hits)


def test_invalid_evidence_cannot_enter_index(tmp_path):
    import pytest
    from story_core.storage import Store
    from story_core.errors import StoryError
    store = Store(tmp_path)
    book = store.create_book("开书", "书", {})["book_id"]
    with pytest.raises(StoryError) as caught:
        seed(store, book, 1, "他关上门。", [dict(kind="fact", key="钥匙", value="已拿到", evidence="他拿到钥匙", visibility="reader")])
    assert caught.value.code == "INVALID_EVIDENCE"


def test_format_only_evidence_is_replaced_with_the_exact_candidate_quote(tmp_path):
    from story_core.memory import retrieve
    from story_core.storage import Store
    store = Store(tmp_path)
    book = store.create_book("开书", "书", {})["book_id"]
    body = '他低声道：“寿剪，合。”'
    version = seed(store, book, 1, body, [
        dict(kind="fact", key="寿剪使用", value="主角首次使用寿剪", evidence='他低声道: "寿剪,合."', visibility="reader"),
    ])
    result = retrieve(store, book, '寿剪')['hits']
    assert result[0]['source']['version_id'] == version
    assert result[0]['source']['quote'] == body


def test_reader_requires_explicit_read_boundary(tmp_path):
    import pytest
    from story_core.storage import Store
    from story_core.memory import retrieve
    from story_core.errors import StoryError
    store = Store(tmp_path)
    book = store.create_book("开书", "书", {})["book_id"]
    with pytest.raises(StoryError) as caught:
        retrieve(store, book, "秘密", role="reader")
    assert caught.value.code == "INVALID_SCOPE"


def test_memory_facets_only_include_committed_evidenced_memory(tmp_path):
    from story_core.service import StoryService
    service = StoryService(tmp_path)
    book = service.open_book('开书', chapter_count=2)['book_id']
    seed(service.store, book, 1, '陆沉收起青冥剑。', [
        dict(kind='entity', key='青冥剑', value='断剑', evidence='青冥剑', visibility='reader'),
        dict(kind='promise', key='再飞一次', value='等待回收', evidence='陆沉收起', visibility='reader', status='open'),
    ])
    facets = service.memory_facets(book)
    assert facets['实体'] == ['青冥剑']
    assert facets['约定'] == ['再飞一次']


def test_memory_facets_separate_typed_and_legacy_entities(tmp_path):
    from story_core.service import StoryService
    service = StoryService(tmp_path)
    book = service.open_book('开书', chapter_count=2)['book_id']
    seed(service.store, book, 1, '陆沉把青冥剑交给凌青，二人走进青岩坊。', [
        dict(kind='entity', key='陆沉', value='杂役弟子', evidence='陆沉', visibility='reader', entity_type='person'),
        dict(kind='entity', key='青冥剑', value='断剑', evidence='青冥剑', visibility='reader', entity_type='item'),
        dict(kind='entity', key='青岩坊', value='坊市', evidence='青岩坊', visibility='reader'),
    ])
    facets = service.memory_facets(book)
    assert facets['人物'] == ['陆沉']
    assert facets['物品'] == ['青冥剑']
    assert facets['实体'] == ['青岩坊']


def test_retrieval_matches_entity_alias_but_returns_canonical_key(tmp_path):
    from story_core.storage import Store
    from story_core.memory import retrieve
    store = Store(tmp_path)
    book = store.create_book('开书', '书', {})['book_id']
    seed(store, book, 1, '沈知秋抬头看了一眼。', [
        dict(kind='entity', key='沈知秋', value='修理铺老板', evidence='沈知秋', visibility='reader',
             entity_type='person', aliases=['秋哥']),
    ])
    hits = retrieve(store, book, '秋哥')['hits']
    assert hits and hits[0]['text'].startswith('沈知秋：')
    assert hits[0]['source']['chapter_number'] == 1


def test_aliases_are_rejected_for_non_entity_memory(tmp_path):
    import pytest
    from story_core.storage import Store
    from story_core.errors import StoryError
    store = Store(tmp_path)
    book = store.create_book('开书', '书', {})['book_id']
    with pytest.raises(StoryError) as caught:
        seed(store, book, 1, '他关上门。', [
            dict(kind='fact', key='门', value='关上', evidence='关上门', visibility='reader', aliases=['木门']),
        ])
    assert caught.value.code == 'INVALID_RESULT'


def test_context_layers_keep_open_promises_and_participant_memory_required():
    from story_core.memory import context_layers
    memories = [
        {'kind': 'promise', 'key': '父亲去向', 'value': '尚未揭晓', 'status': 'open', 'mandatory': True},
        {'kind': 'relationship', 'key': '沈知秋与妹妹', 'value': '二人共同守着修理铺', 'source': {'chapter_number': 1}},
        {'kind': 'fact', 'key': '旧铜钟', 'value': '放在城西库房', 'source': {'chapter_number': 1}},
    ]
    layers = context_layers(memories, {'goal': '沈知秋带妹妹修好收音机', 'participants': ['沈知秋', '妹妹']}, 2)
    assert [item['key'] for item in layers['required']] == ['父亲去向', '沈知秋与妹妹']
    assert [item['key'] for item in layers['supplementary']] == ['旧铜钟']
    assert layers['queries']['participants'] == ['沈知秋', '妹妹']


def test_context_layers_hide_other_character_knowledge_for_chapter_pov():
    from story_core.memory import context_layers
    import json
    memories = [
        {'kind': 'knowledge', 'key': '陆沉知晓密室入口', 'value': '陆沉听见机关位置', 'owner': '陆沉', 'source': {'chapter_number': 1}},
        {'kind': 'knowledge', 'key': '苏晚知晓父亲假死', 'value': '苏晚看过遗书', 'owner': '苏晚', 'source': {'chapter_number': 1}},
        {'kind': 'fact', 'key': '旧井', 'value': '院中旧井有机关', 'source': {'chapter_number': 1}},
    ]
    layers = context_layers(memories, {'goal': '陆沉查看旧井', 'pov': '陆沉', 'participants': ['陆沉']}, 2)
    visible = layers['required'] + layers['supplementary']
    assert '苏晚知晓父亲假死' not in [item['key'] for item in visible]
    assert layers['pov_guard'] == {'pov': '陆沉', 'withheld_knowledge_count': 1}
    assert '苏晚' not in json.dumps(layers['pov_guard'], ensure_ascii=False)


def test_context_layers_preserve_legacy_knowledge_when_chapter_has_no_pov():
    from story_core.memory import context_layers
    memories = [{'kind': 'knowledge', 'key': '苏晚知晓父亲假死', 'value': '苏晚看过遗书', 'owner': '苏晚'}]
    layers = context_layers(memories, {'goal': '查看旧井'}, 2)
    assert [item['key'] for item in layers['supplementary']] == ['苏晚知晓父亲假死']
    assert layers['pov_guard'] == {'pov': None, 'withheld_knowledge_count': 0}


def test_promise_obligations_mark_due_and_upcoming_but_skip_settled():
    from story_core.memory import promise_obligations
    plan = {'promises': [
        {'key': '父亲留言', 'due_chapter': 3, 'mandatory': True, 'resolution': '听清留言内容'},
        {'key': '旧债', 'due_chapter': 5, 'mandatory': True, 'resolution': '说明债主身份'},
        {'key': '普通问候', 'due_chapter': 3, 'mandatory': False, 'resolution': '回信'},
    ]}
    memories = [
        {'kind': 'promise', 'key': '旧债', 'status': 'paid'},
        {'kind': 'promise', 'key': '普通问候', 'status': 'open'},
    ]
    assert promise_obligations(memories, plan, 3) == [
        {'key': '父亲留言', 'due_chapter': 3, 'resolution': '听清留言内容', 'urgency': 'due'},
    ]
