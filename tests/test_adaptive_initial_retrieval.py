from test_memory import seed
from test_workflow import make, result_for, to_stage


def test_adaptive_first_task_retrieves_history_without_legacy_full_scan(tmp_path, monkeypatch):
    service, book = make(tmp_path, chapters=3)
    draft = to_stage(service, book, 'draft')
    # Finish the first chapter, then inspect the next unleased task.
    while True:
        service.submit_task(draft['task_id'], draft['lease_id'], result_for(draft))
        with service.store.read() as conn:
            run = service._latest_run(conn, book)
        if run['chapter_number'] == 2:
            break
        draft = service.next_task(book)
    monkeypatch.setenv('HULK_CONTEXT_MODE', 'adaptive')
    monkeypatch.setenv('HULK_CONTEXT_WINDOW_TOKENS', '100000')
    def no_full_scan(*args, **kwargs):
        raise AssertionError('adaptive called legacy full-history materialization')
    monkeypatch.setattr('story_core.service.canonical_memory', no_full_scan)
    task = service.next_task(book)
    assert task['task_id']
    assert task['input']['historical_evidence'][0]['key'] == '收音机'
    assert task['input']['historical_evidence'][0]['source']['chapter_number'] == 1


def test_retriever_can_use_callers_uncommitted_snapshot(tmp_path):
    from story_core.retrieval import SQLiteRetriever, RetrievalScope
    service, book = make(tmp_path)
    seed(service.store, book, 1, '收音机响了。', [dict(kind='fact', key='收音机', value='响了', evidence='收音机')])
    with service.store.write(book) as conn:
        conn.execute("UPDATE memories SET visibility='author' WHERE book_id=?", (book,))
        result = SQLiteRetriever(service.store).search('收音机', RetrievalScope(book, 1, role='reader'), conn=conn)
        assert result['hits'] == []


def test_selected_legacy_promises_do_not_resurrect_settled_versions(tmp_path):
    from story_core.memory import selected_canonical_memory
    service, book = make(tmp_path)
    seed(service.store, book, 1, '答应归还铜牌。', [dict(kind='promise', key='铜牌', value='待归还', evidence='铜牌', status='open')])
    seed(service.store, book, 2, '已经归还铜牌。', [dict(kind='promise', key='铜牌', value='已归还', evidence='铜牌', status='paid')])
    with service.store.read() as conn:
        current = selected_canonical_memory(conn, book, 2, kinds=['promise'])
        past = selected_canonical_memory(conn, book, 1, kinds=['promise'])
    assert current[0]['status'] == 'paid'
    assert past[0]['status'] == 'open'


def test_initial_query_encoding_occurs_outside_write_transaction(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from story_core.retrieval import SQLiteRetriever
    from story_core.semantic_retrieval import PreparedSemanticQuery
    service, book = make(tmp_path)
    task = to_stage(service, book, 'draft')
    with service.store.write(book) as conn:
        conn.execute("UPDATE tasks SET status='cancelled' WHERE id=?", (task['task_id'],))
    active, prepared = [], []
    original_write = service.store.write
    @contextmanager
    def tracked_write(*args, **kwargs):
        with original_write(*args, **kwargs) as conn:
            active.append(True)
            try:
                yield conn
            finally:
                active.pop()
    class Retriever:
        def prepare_query(self, query):
            assert not active
            prepared.append(query)
            return PreparedSemanticQuery(query, [1.0], 'fixture')
        def search(self, query, scope, **kwargs):
            assert isinstance(query, PreparedSemanticQuery)
            return SQLiteRetriever(service.store).search(query.text, scope, **kwargs)
    monkeypatch.setattr(service.store, 'write', tracked_write)
    monkeypatch.setattr('story_core.retrieval.retriever_for', lambda store: Retriever())
    monkeypatch.setenv('HULK_CONTEXT_MODE', 'adaptive')
    monkeypatch.setenv('HULK_CONTEXT_WINDOW_TOKENS', '100000')
    result = service.next_task(book)
    assert result['task_id'] and len(prepared) == 1


def test_semantic_blocked_status_uses_saved_measurements(tmp_path, monkeypatch):
    from story_core.retrieval import SQLiteRetriever
    from story_core.semantic_retrieval import PreparedSemanticQuery
    service, book = make(tmp_path)
    task = to_stage(service, book, 'draft')
    with service.store.write(book) as conn:
        conn.execute("UPDATE tasks SET status='cancelled' WHERE id=?", (task['task_id'],))
        conn.execute('UPDATE runs SET budget_tokens=tokens WHERE book_id=?', (book,))
    class Adapter:
        def prepare_query(self, query):
            return PreparedSemanticQuery(query, [1.0], 'fixture')
        def candidates(self, *args):
            return []
    monkeypatch.setenv('HULK_CONTEXT_MODE', 'adaptive')
    monkeypatch.setenv('HULK_CONTEXT_WINDOW_TOKENS', '100000')
    monkeypatch.setattr('story_core.retrieval.retriever_for', lambda store: SQLiteRetriever(store, semantic=Adapter()))
    assert service.next_task(book)['reason'] == 'budget'
    def no_recompile(*args, **kwargs):
        raise AssertionError('status must use saved measurements')
    monkeypatch.setattr(service, '_task_spec', no_recompile)
    blocker = service.status(book)['blocker']
    assert blocker['minimum_budget_tokens'] > 0
    assert blocker['next_reservation'] > 0


def test_lease_expiry_during_preflight_requests_retry(tmp_path, monkeypatch):
    import pytest
    from contextlib import contextmanager
    from story_core.errors import StoryError
    service, book = make(tmp_path)
    task = to_stage(service, book, 'draft')
    monkeypatch.setenv('HULK_CONTEXT_MODE', 'adaptive')
    original_write = service.store.write
    @contextmanager
    def expire_before_write(*args, **kwargs):
        with original_write(book) as conn:
            conn.execute('UPDATE tasks SET lease_until=0 WHERE id=?', (task['task_id'],))
        with original_write(*args, **kwargs) as conn:
            yield conn
    monkeypatch.setattr(service.store, 'write', expire_before_write)
    with pytest.raises(StoryError) as caught:
        service.next_task(book)
    assert caught.value.code == 'STALE_REVISION'


def test_old_semantic_blocker_reports_unavailable_without_loading_model(tmp_path, monkeypatch):
    service, book = make(tmp_path)
    to_stage(service, book, 'draft')
    with service.store.write(book) as conn:
        conn.execute("UPDATE runs SET status='needs_attention', reason='步骤或 token 预留预算不足；可提高预算后继续。' WHERE book_id=?", (book,))
    monkeypatch.setattr('story_core.semantic_retrieval.load_policy', lambda store: {'strategy': 'semantic'})
    def no_recompile(*args, **kwargs):
        raise AssertionError('legacy semantic status must not load a model')
    monkeypatch.setattr(service, '_task_spec', no_recompile)
    blocker = service.status(book)['blocker']
    assert blocker['code'] == 'BUDGET_LIMIT'
    assert blocker['measurement_unavailable'] is True
