import pytest

from story_core.errors import StoryError
from story_core.service import StoryService
from story_core import long_memory as lm
from test_long_memory import world, chapter, fact


def test_book_fork_has_independent_manuscript_and_remapped_fact_dependencies(world):
    store, book = world
    service = StoryService(store.root)
    with store.write(book) as conn:
        entity = lm.register_entity(conn, book, '主角')
        version = chapter(conn, book, 1)
        old_fact = fact(conn, book, entity, version, 1)
        from story_core.storage import dumps
        conn.execute('UPDATE books SET plan=? WHERE id=?',
                     (dumps({'chapters': [{'number': 2, 'entity_ids': [entity], 'required_fact_ids': [old_fact]}], 'promises': []}), book))
    fork = service.fork_book(book, name='另一条故事线', actor='author', expected_revision=0, request_id='fork')
    child = fork['book_id']
    assert child != book
    assert service.fork_book(book, name='另一条故事线', actor='author', expected_revision=0, request_id='fork') == fork
    with store.write(child) as conn:
        child_entity = lm.resolve_alias(conn, child, '主角')
        state = lm.current_state(conn, child, [child_entity], story_time=1)
        assert state[0]['value'] == 10
        assert state[0]['fact_id'] != old_fact
        assert state[0]['source_version'] != version
        assert service.store.decode_book(conn.execute('SELECT * FROM books WHERE id=?', (child,)).fetchone())['plan']['chapters'][0]['required_fact_ids'] == [state[0]['fact_id']]
        chapter(conn, child, 1, '子分支的独立正文。')
    with store.read() as conn:
        assert lm.current_state(conn, book, [entity], story_time=1)[0]['value'] == 10
        assert conn.execute('SELECT version_id FROM chapters WHERE book_id=?', (book,)).fetchone()[0] == version
        assert conn.execute('SELECT count(*) FROM tasks WHERE book_id=?', (child,)).fetchone()[0] == 0
    assert len(service.book_branches(child)['items']) == 2


def test_fork_revision_and_request_conflicts_are_atomic(world):
    store, book = world
    service = StoryService(store.root)
    with pytest.raises(StoryError):
        service.fork_book(book, name='分支', actor='author', expected_revision=1, request_id='fork')
    assert len(service.list_books()) == 1
    service.fork_book(book, name='分支', actor='author', expected_revision=0, request_id='fork')
    with pytest.raises(StoryError) as error:
        service.fork_book(book, name='另一个分支', actor='author', expected_revision=0, request_id='fork')
    assert error.value.code == 'IDEMPOTENCY_CONFLICT'


def test_fork_copies_repair_retirement_and_survives_parent_deletion(world):
    import json
    store, book = world
    service = StoryService(store.root)
    with store.write(book) as conn:
        entity = lm.register_entity(conn, book, '主角')
        version = chapter(conn, book, 1)
        fact(conn, book, entity, version, 1)
        event = conn.execute('SELECT id FROM lm_fact_events WHERE book_id=?', (book,)).fetchone()[0]
        conn.execute('INSERT INTO lm_repair_actions VALUES (?,?,?,?,?,?,?,?)', ('repair1',book,'main',version,'author',0,
            json.dumps({'old_dependencies': [version], 'new_dependencies': [version]}),0))
        conn.execute('INSERT INTO lm_retired_events VALUES (?,?,?,?)', (event,'fact','repair1',None))
    child = service.fork_book(book, name='分叉', actor='author', expected_revision=0, request_id='f')['book_id']
    with store.read() as conn:
        retired = conn.execute('''SELECT r.* FROM lm_retired_events r JOIN lm_repair_actions a ON a.id=r.repair_id
            WHERE a.book_id=?''', (child,)).fetchone()
        assert retired is not None and retired['event_id'] != event and retired['repair_id'] != 'repair1'
        audit = conn.execute('SELECT manifest FROM lm_repair_actions WHERE book_id=?', (child,)).fetchone()[0]
        assert version not in json.loads(audit)['new_dependencies']
    service.trash_book(book)
    service.purge_book(book)
    assert service.get_book(child)['chapters'][0]['version_id'] != version


def test_fork_http_and_cli(world):
    from fastapi.testclient import TestClient
    from story_core.http import create_app
    from test_cli import cli, output
    store, book = world
    with TestClient(create_app(store.root)) as client:
        response = client.post(f'/api/books/{book}/branches', json={
            'name': '独立分支', 'actor': 'author', 'expected_revision': 0, 'request_id': 'fork'})
        assert response.status_code == 200, response.text
        child = response.json()['book_id']
        assert len(client.get(f'/api/books/{child}/branches').json()['items']) == 2
    result = cli(store.root, 'branches', child)
    assert result.returncode == 0, result.stderr
    assert len(output(result)['items']) == 2


def test_fork_preserves_plain_legacy_memory_text(world):
    from test_memory import seed
    from story_core.retrieval import SQLiteRetriever, RetrievalScope
    store, book = world
    seed(store, book, 1, '铜牌交给商人。', [dict(kind='fact', key='铜牌', value='商人持有', evidence='铜牌')])
    child = StoryService(store.root).fork_book(book, name='分叉', actor='author', expected_revision=0, request_id='f')['book_id']
    hits = SQLiteRetriever(store).search('铜牌', RetrievalScope(child, 1))['hits']
    assert hits[0]['value'] == '商人持有'
