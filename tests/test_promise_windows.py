import pytest
from story_core import long_memory as lm
from story_core.errors import StoryError
from test_long_memory import world, chapter, fact


def test_promise_window_keeps_overdue_obligation(world):
    store, book = world
    with store.write(book) as conn:
        identity = lm.schedule_promise(conn, book, '归还', due_from_chapter=3, due_to_chapter=5, mandatory=True)
        assert lm.select_promises(conn, book, 2)['required'] == []
        for number, urgency in [(3, 'window_open'), (5, 'window_open'), (6, 'overdue')]:
            result = lm.select_promises(conn, book, number)['required']
            assert result[0]['promise_id'] == identity
            assert result[0]['urgency'] == urgency


def test_relation_trigger_uses_visible_verified_state_at_explicit_story_time(world):
    store, book = world
    with store.write(book) as conn:
        entity = lm.register_entity(conn, book, '铜牌')
        source = chapter(conn, book, 1)
        fid = fact(conn, book, entity, source, 1, predicate='owner', value='甲', story_valid_from=10)
        identity = lm.schedule_promise(conn, book, '归还', visibility='reader',
            relation_triggers=[{'fact_id': fid, 'equals': '甲'}])
        assert lm.select_promises(conn, book, 2, story_time=5)['required'] == []
        assert lm.select_promises(conn, book, 2)['required'] == []
        assert lm.select_promises(conn, book, 2, story_time=10)['required'][0]['promise_id'] == identity
        chapter(conn, book, 1, '来源改写。')
        assert lm.select_promises(conn, book, 2, story_time=10)['required'] == []


def test_hidden_relation_does_not_trigger_reader_obligation(world):
    store, book = world
    with store.write(book) as conn:
        entity = lm.register_entity(conn, book, '铜牌')
        source = chapter(conn, book, 1)
        fid = fact(conn, book, entity, source, 1, visibility='author')
        lm.schedule_promise(conn, book, '秘密回收', visibility='reader', relation_triggers=[{'fact_id': fid, 'equals': 10}])
        assert lm.select_promises(conn, book, 2, story_time=2, role='reader')['required'] == []
        assert lm.select_promises(conn, book, 2, story_time=2, role='author')['required']


def test_invalid_promise_interval_is_rejected(world):
    store, book = world
    with store.write(book) as conn:
        with pytest.raises(StoryError):
            lm.schedule_promise(conn, book, '归还', due_from_chapter=5, due_to_chapter=3)


def test_invalid_relation_entity_is_rejected_before_revision_changes(world):
    from story_core.service import StoryService
    store, book = world
    with store.write(book) as conn:
        entity = lm.register_entity(conn, book, '铜牌')
        source = chapter(conn, book, 1)
        fid = fact(conn, book, entity, source, 1)
    service = StoryService(store.root)
    with pytest.raises(StoryError):
        service.memory_schedule_promise(book, '归还', actor='author', expected_revision=0, request_id='bad',
            relation_triggers=[{'fact_id': fid, 'equals': {'type': 'entity', 'entity_id': 'missing'}}])
    with store.read() as conn:
        assert conn.execute('SELECT revision FROM books WHERE id=?', (book,)).fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM lm_promises').fetchone()[0] == 0


def test_promise_schedule_service_is_revision_fenced_and_idempotent(world):
    from story_core.service import StoryService
    store, book = world
    service = StoryService(store.root)
    options = dict(actor='author', expected_revision=0, request_id='schedule', due_from_chapter=3, due_to_chapter=5)
    result = service.memory_schedule_promise(book, '归还', **options)
    assert result['revision'] == 1
    assert service.memory_schedule_promise(book, '归还', **options) == result
    with pytest.raises(StoryError):
        service.memory_schedule_promise(book, '另一个约定', **{**options, 'request_id': 'other'})


def test_promise_http_rejects_nested_control_fields(world):
    from fastapi.testclient import TestClient
    from story_core.http import create_app
    store, book = world
    with TestClient(create_app(store.root)) as client:
        response = client.post(f'/api/books/{book}/memory/promises', json={
            'label': '归还', 'expected_revision': 0, 'request_id': 's', 'options': {'actor': 'other'}})
        assert response.status_code == 400
        assert response.json()['detail']['code'] == 'INVALID_REQUEST'
        response = client.post(f'/api/books/{book}/memory/promises', json={
            'label': '归还', 'expected_revision': 0, 'request_id': 's',
            'options': {'due_from_chapter': 3, 'due_to_chapter': 5}})
        assert response.status_code == 200, response.text
        selected = client.get(f'/api/books/{book}/memory/promises?chapter_number=4').json()
        assert selected['required'][0]['urgency'] == 'window_open'
