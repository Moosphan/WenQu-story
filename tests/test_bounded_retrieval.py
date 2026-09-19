from test_memory import seed


def setup(tmp_path):
    from story_core.storage import Store
    store = Store(tmp_path)
    book = store.create_book('测试', '甲', {})['book_id']
    return store, book


def test_indexed_chinese_alias_search_bounds_candidates_and_preserves_source(tmp_path):
    from story_core.retrieval import SQLiteRetriever, RetrievalScope, rebuild_index
    store, book = setup(tmp_path)
    for i in range(1, 50):
        seed(store, book, i, '主角回到城里。', [{'kind': 'fact', 'key': str(i), 'value': '主角回城', 'evidence': '主角'}])
    v = seed(store, book, 50, '沈知秋取出铜牌。', [{'kind': 'entity', 'key': '沈知秋', 'value': '铜牌持有人', 'aliases': ['秋哥'], 'evidence': '沈知秋'}])
    with store.write(book) as conn:
        rebuild_index(conn, book)
    result = SQLiteRetriever(store).search('秋哥', RetrievalScope(book, through_chapter=50), limit=3)
    assert result['hits'][0]['source']['version_id'] == v
    assert result['candidates_examined'] <= 64
    assert result['index_complete']


def test_external_semantic_candidates_are_resolved_through_scope_not_trusted(tmp_path):
    from story_core.retrieval import SQLiteRetriever, RetrievalScope, rebuild_index
    store, book = setup(tmp_path)
    other = store.create_book('测试', '乙', {})['book_id']
    old = seed(store, book, 1, '父亲死了。', [{'kind': 'fact', 'key': '旧版', 'value': '已死', 'evidence': '死了'}])
    seed(store, book, 1, '父亲失踪。', [{'kind': 'fact', 'key': '正史', 'value': '失踪', 'evidence': '失踪'}])
    seed(store, book, 2, '苏晚见到父亲。', [{'kind': 'knowledge', 'key': '秘密', 'value': '活着', 'owner': '苏晚', 'visibility': 'author', 'evidence': '见到父亲'}])
    seed(store, other, 1, '黄金满屋。', [{'kind': 'fact', 'key': '串书', 'value': '黄金', 'evidence': '黄金'}])
    with store.write(book) as conn:
        rebuild_index(conn, book)
        ids = [row[0] for row in conn.execute('SELECT id FROM memories')]
    class Adapter:
        def candidates(self, query, scope, limit):
            return ids
    result = SQLiteRetriever(store, semantic=Adapter()).search('借物的旧约', RetrievalScope(book, role='reader', through_chapter=1, pov='主角'))
    assert [hit['key'] for hit in result['hits']] == ['正史']
    assert all(hit['source']['version_id'] != old for hit in result['hits'])


def test_limited_lookup_budget_survives_new_helper_instance(tmp_path):
    import pytest
    from story_core.retrieval import bounded_lookup, RetrievalScope, rebuild_index
    from story_core.context_compiler import ContextPolicy
    from story_core.errors import StoryError
    store, book = setup(tmp_path)
    seed(store, book, 1, '铜牌借给商人。', [{'kind': 'fact', 'key': '铜牌', 'value': '借给商人', 'evidence': '铜牌'}])
    with store.write(book) as conn:
        rebuild_index(conn, book)
    task_input = {'instruction': '续写', 'candidate': {'body': '本章正文'}, 'historical_evidence': [{'key': '旧补查'}]}
    for _ in range(2):
        result = bounded_lookup(store, book, 'run-fixture', 2, task_input, {}, '铜牌', scope=RetrievalScope(book, through_chapter=1), policy=ContextPolicy(mode='adaptive', context_window=64000))
        assert result.input['candidate'] == task_input['candidate']
        assert all(item['key'] != '旧补查' for item in result.input.get('historical_evidence', []))
    with pytest.raises(StoryError) as caught:
        bounded_lookup(store, book, 'run-fixture', 2, task_input, {}, '铜牌', scope=RetrievalScope(book, through_chapter=1))
    assert caught.value.code == 'LOOKUP_LIMIT'


def test_new_chapter_commit_indexes_without_global_backfill(tmp_path):
    from story_core.retrieval import SQLiteRetriever, RetrievalScope
    store, book = setup(tmp_path)
    seed(store, book, 1, '铜牌交给商人。', [{'kind': 'fact', 'key': '铜牌', 'value': '商人持有', 'evidence': '铜牌'}])
    result = SQLiteRetriever(store).search('铜牌', RetrievalScope(book, through_chapter=1))
    assert result['index_complete']
    assert result['hits'][0]['key'] == '铜牌'


def test_optional_null_legacy_aliases_do_not_prevent_commit_or_index(tmp_path):
    from story_core.retrieval import SQLiteRetriever, RetrievalScope
    store, book = setup(tmp_path)
    seed(store, book, 1, '他取出铜牌。', [{'kind': 'entity', 'key': '铜牌', 'value': '信物', 'evidence': '铜牌', 'aliases': None}])
    result = SQLiteRetriever(store).search('铜牌', RetrievalScope(book, through_chapter=1))
    assert result['hits'][0]['key'] == '铜牌'
