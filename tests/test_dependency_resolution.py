import json

import pytest

from story_core.storage import dumps
from test_memory import seed
from test_workflow import make, to_stage, result_for


def prepared(tmp_path, monkeypatch, stage='draft'):
    monkeypatch.setenv('HULK_CONTEXT_MODE', 'adaptive')
    monkeypatch.setenv('HULK_CONTEXT_WINDOW_TOKENS', '64000')
    service, book = make(tmp_path, chapters=3)
    outline = to_stage(service, book, 'outline')
    service.submit_task(outline['task_id'], outline['lease_id'], result_for(outline))
    version = seed(service.store, book, 1, '沈知秋将铜牌借给商人。', [
        {'kind': 'fact', 'key': '旧约', 'value': '商人持有铜牌', 'evidence': '铜牌借给商人', 'visibility': 'reader'}])
    with service.store.write(book) as conn:
        identity = conn.execute('SELECT id FROM memories WHERE version_id=?', (version,)).fetchone()[0]
        plan = json.loads(conn.execute('SELECT plan FROM books WHERE id=?', (book,)).fetchone()[0])
        plan['chapters'][1]['required_fact_ids'] = [identity]
        conn.execute('UPDATE books SET plan=? WHERE id=?', (dumps(plan), book))
        conn.execute('UPDATE runs SET chapter_number=2,stage=?,candidate=? WHERE book_id=?',
            (stage, dumps(result_for({'stage': 'draft'})) if stage != 'draft' else None, book))
    return service, book, version, identity


@pytest.mark.parametrize('stage', ['draft', 'revise', 'continuity', 'reader'])
def test_first_task_resolves_exact_source_without_lookup_or_index(tmp_path, monkeypatch, stage):
    service, book, version, identity = prepared(tmp_path, monkeypatch, stage)
    with service.store.write(book) as conn:
        conn.execute('DELETE FROM memory_terms WHERE book_id=?', (book,))
        conn.execute('DELETE FROM memory_indexed WHERE book_id=?', (book,))
    task = service.next_task(book)
    assert task.get('task_id'), task
    found = [item for item in task['input']['required_memory'] if item.get('id') == identity]
    assert found[0]['source']['version_id'] == version
    assert found[0]['evidence'] == '铜牌借给商人'
    assert found[0]['state_semantics'] == 'historical_evidence'
    assert task['input']['lookup_policy']['remaining'] == 2
    assert task['input']['context_diagnostics']['missing_hard_ids'] == []
    assert any(source.get('id') == identity and source['tier'] == 'required'
               for source in task['input']['context_manifest']['canonical_sources'])


@pytest.mark.parametrize('invalid', ['hidden', 'other_owner', 'stale', 'future'])
def test_explicit_id_never_bypasses_source_or_pov_guards(tmp_path, monkeypatch, invalid):
    service, book, version, identity = prepared(tmp_path, monkeypatch)
    with service.store.write(book) as conn:
        if invalid == 'hidden':
            conn.execute("UPDATE memories SET visibility='author' WHERE id=?", (identity,))
        elif invalid == 'other_owner':
            row = json.loads(conn.execute('SELECT data FROM memories WHERE id=?', (identity,)).fetchone()[0])
            row.update(kind='knowledge', owner='苏晚')
            conn.execute("UPDATE memories SET kind='knowledge',data=? WHERE id=?", (dumps(row), identity))
        elif invalid == 'stale':
            conn.execute("UPDATE chapters SET status='needs_review' WHERE book_id=? AND number=1", (book,))
        else:
            conn.execute('UPDATE runs SET chapter_number=1 WHERE book_id=?', (book,))
            plan = json.loads(conn.execute('SELECT plan FROM books WHERE id=?', (book,)).fetchone()[0])
            plan['chapters'][0]['required_fact_ids'] = [identity]
            conn.execute('UPDATE books SET plan=? WHERE id=?', (dumps(plan), book))
    result = service.next_task(book)
    assert not result.get('task_id')
    assert identity in service.status(book)['blocker']['context']['missing_hard_ids']


def test_normalized_fact_id_resolves_without_explicit_entity_list(tmp_path, monkeypatch):
    from story_core.long_memory import register_entity, record_fact
    service, book, version, identity = prepared(tmp_path, monkeypatch)
    with service.store.write(book) as conn:
        entity = register_entity(conn, book, '铜牌')
        fact = record_fact(conn, book, entity, 'location', '商人手中', source_chapter=1,
            source_version=version, evidence='铜牌借给商人', story_valid_from=5, verified=True)
        plan = json.loads(conn.execute('SELECT plan FROM books WHERE id=?', (book,)).fetchone()[0])
        plan['chapters'][1].update(required_fact_ids=[fact], story_time=10)
        conn.execute('UPDATE books SET plan=? WHERE id=?', (dumps(plan), book))
    task = service.next_task(book)
    assert task.get('task_id'), task
    assert task['input']['current_state'][0]['fact_id'] == fact
    assert task['input']['current_state'][0]['value'] == '商人手中'


@pytest.mark.parametrize('condition', ['unverified', 'no_time', 'before_time', 'invalidated', 'other_book'])
def test_fact_reference_does_not_bypass_verified_state_projection(tmp_path, monkeypatch, condition):
    from story_core.long_memory import register_entity, record_fact, invalidate_version
    service, book, version, identity = prepared(tmp_path, monkeypatch)
    source_book = book
    if condition == 'other_book':
        source_book = service.store.create_book('Synthetic', 'Synthetic', {})['book_id']
        version = seed(service.store, source_book, 1, '铜牌借给商人。')
    with service.store.write(book) as conn:
        entity = register_entity(conn, source_book, '铜牌')
        fact = record_fact(conn, source_book, entity, 'location', '商人手中', source_chapter=1,
            source_version=version, evidence='铜牌借给商人', story_valid_from=5, verified=condition != 'unverified')
        if condition == 'invalidated':
            invalidate_version(conn, source_book, version)
        plan = json.loads(conn.execute('SELECT plan FROM books WHERE id=?', (book,)).fetchone()[0])
        plan['chapters'][1].update(required_fact_ids=[fact])
        if condition != 'no_time':
            plan['chapters'][1]['story_time'] = 1 if condition == 'before_time' else 10
        conn.execute('UPDATE books SET plan=? WHERE id=?', (dumps(plan), book))
    assert not service.next_task(book).get('task_id')
    assert fact in service.status(book)['blocker']['context']['missing_hard_ids']


def test_exact_resolution_has_no_lexical_top_k_limit(tmp_path):
    from story_core.dependency_resolution import resolve_dependencies
    from story_core.storage import Store
    store = Store(tmp_path)
    book = store.create_book('Synthetic', 'Synthetic', {})['book_id']
    for number in range(1, 4):
        seed(store, book, number, '铜牌归还。', [{'kind': 'fact', 'key': f'旧事{number}-{index}',
            'value': '归还', 'evidence': '铜牌归还', 'visibility': 'reader'} for index in range(25)])
    with store.read() as conn:
        ids = [row[0] for row in conn.execute('SELECT id FROM memories WHERE book_id=?', (book,))]
        result = resolve_dependencies(conn, book, {'required_fact_ids': ids}, 'draft', 4, role='author', pov_entity_id=None)
    assert {item['id'] for item in result['evidence']} == set(ids)
    assert len(result['evidence']) == 75


def test_shadow_preserves_legacy_context_and_extract_does_not_gain_old_prose(tmp_path, monkeypatch):
    service, book, version, identity = prepared(tmp_path, monkeypatch, stage='extract')
    task = service.next_task(book)
    assert task.get('task_id'), task
    assert task['input']['context_diagnostics']['missing_hard_ids'] == []
    assert 'required_memory' not in task['input']
    assert 'context_required_ids' not in task['input']
    with service.store.read() as conn:
        from story_core.dependency_resolution import resolve_dependencies
        assert resolve_dependencies(conn, book, {'required_fact_ids': [identity]}, 'extract', 2,
            role='author', pov_entity_id=None) == {'evidence': [], 'state': []}
    monkeypatch.setenv('HULK_CONTEXT_MODE', 'shadow')
    task = service.next_task(book)
    assert task.get('task_id')
    assert 'required_memory' not in task['input']


def test_extract_ignores_unresolved_outline_promises_but_preserves_prose(tmp_path, monkeypatch):
    service, book, _, _ = prepared(tmp_path, monkeypatch, stage='extract')
    with service.store.write(book) as conn:
        plan = json.loads(conn.execute('SELECT plan FROM books WHERE id=?', (book,)).fetchone()[0])
        plan['chapters'][1]['promise_ids'] = ['古剑再飞', '铜牌旧案']
        conn.execute('UPDATE books SET plan=? WHERE id=?', (dumps(plan), book))
    task = service.next_task(book)
    assert task.get('task_id'), task
    assert task['input']['candidate']['body'] == result_for({'stage': 'draft'})['body']
    assert task['input']['source_paragraphs']
    assert 'scheduled_promises' not in task['input']
    service.submit_task(task['task_id'], task['lease_id'], result_for(task))
    assert service.status(book)['run']['stage'] == 'continuity'


def test_reader_resolves_promise_key_from_committed_evidence(tmp_path, monkeypatch):
    service, book, _, _ = prepared(tmp_path, monkeypatch, stage='reader')
    seed(service.store, book, 1, '铜牌旧案尚无结论。', [
        {'kind': 'promise', 'key': '铜牌旧案', 'value': '尚无结论', 'status': 'open',
         'evidence': '铜牌旧案尚无结论', 'visibility': 'reader'}])
    with service.store.write(book) as conn:
        plan = json.loads(conn.execute('SELECT plan FROM books WHERE id=?', (book,)).fetchone()[0])
        plan['chapters'][1]['required_fact_ids'] = []
        plan['chapters'][1]['promise_ids'] = ['铜牌旧案']
        conn.execute('UPDATE books SET plan=? WHERE id=?', (dumps(plan), book))
    task = service.next_task(book)
    assert task.get('task_id'), task
    assert task['input']['context_diagnostics']['missing_hard_ids'] == []
    assert any(x.get('key') == '铜牌旧案' for x in task['input']['required_memory'])
    assert 'chapter_plan' not in task['input']


def test_reader_does_not_require_unpublished_author_promise(tmp_path, monkeypatch):
    service, book, _, _ = prepared(tmp_path, monkeypatch, stage='reader')
    with service.store.write(book) as conn:
        plan = json.loads(conn.execute('SELECT plan FROM books WHERE id=?', (book,)).fetchone()[0])
        plan['chapters'][1]['promise_ids'] = ['作者尚未揭露的秘密']
        conn.execute('UPDATE books SET plan=? WHERE id=?', (dumps(plan), book))
    task = service.next_task(book)
    assert task.get('task_id'), task
    assert task['input']['context_diagnostics']['missing_hard_ids'] == []
    assert 'planned_promises' not in task['input']


def test_resolved_hard_source_obeys_capacity_and_resume_without_model_call(tmp_path, monkeypatch):
    service, book, version, identity = prepared(tmp_path, monkeypatch)
    monkeypatch.setenv('HULK_CONTEXT_WINDOW_TOKENS', '1000')
    assert not service.next_task(book).get('task_id')
    context = service.status(book)['blocker']['context']
    assert context['missing_hard_ids'] == []
    assert context['blocked_reason'] == 'model_capacity'
    with service.store.read() as conn:
        assert conn.execute("SELECT count(*) FROM events WHERE book_id=? AND kind IN ('context_lookup','provider_call_started')", (book,)).fetchone()[0] == 0
    monkeypatch.setenv('HULK_CONTEXT_WINDOW_TOKENS', '64000')
    service.control(book, 'resume')
    task = service.next_task(book)
    assert task.get('task_id')
    assert task['input']['lookup_policy']['remaining'] == 2
    matching = [item for key in ('required_memory', 'supplementary_memory') for item in task['input'].get(key, [])
                if item.get('key') == '旧约' and item['source']['version_id'] == version]
    assert len(matching) == 1
    sources = [source for source in task['input']['context_manifest']['canonical_sources']
               if source['key'] == '旧约' and source['version_id'] == version]
    assert len(sources) == 1
    assert sources[0]['id'] == identity


@pytest.mark.parametrize('stage', ['arc', 'ending'])
def test_committed_current_chapter_is_available_to_post_commit_reviews(tmp_path, monkeypatch, stage):
    service, book, version, identity = prepared(tmp_path, monkeypatch, stage)
    with service.store.write(book) as conn:
        conn.execute('UPDATE runs SET chapter_number=1 WHERE book_id=?', (book,))
        plan = json.loads(conn.execute('SELECT plan FROM books WHERE id=?', (book,)).fetchone()[0])
        plan['chapters'][0]['required_fact_ids'] = [identity]
        conn.execute('UPDATE books SET plan=? WHERE id=?', (dumps(plan), book))
    task = service.next_task(book)
    assert task.get('task_id'), task
    assert any(item.get('id') == identity for item in task['input']['required_memory'])
