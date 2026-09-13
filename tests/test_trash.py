import pytest
from story_core.service import StoryService
from story_core.errors import StoryError


def test_trash_restore_preserves_versions_and_kind(tmp_path):
    s = StoryService(tmp_path)
    b = s.open_book('悬疑小说', title='回声', chapter_count=1, target_words=50)
    bid = b['book_id']
    s.import_chapters(bid, [{'chapter_number': 1, 'title': '门外', 'body': '门外有人。'}])
    s.set_book_kind(bid, 'sample')
    before = s.get_book(bid)
    s.trash_book(bid)
    s.trash_book(bid)
    assert s.list_books() == []
    assert s.list_books('sample') == []
    assert len(s.list_trash()) == 1
    with pytest.raises(StoryError) as e:
        s.set_book_kind(bid, 'user')
    assert e.value.code == 'BOOK_TRASHED'
    s.restore_book(bid)
    assert s.get_book(bid) == before
    assert s.list_trash() == []


def test_purge_requires_trash_and_removes_related_records(tmp_path):
    s = StoryService(tmp_path)
    bid = s.open_book('小说', chapter_count=1, target_words=50)['book_id']
    s.import_chapters(bid, [{'chapter_number': 1, 'title': '门', 'body': '风吹开门。'}])
    with pytest.raises(StoryError):
        s.purge_book(bid)
    s.trash_book(bid)
    s.purge_book(bid)
    assert s.list_trash() == []
    with s.store.read() as conn:
        assert conn.execute('SELECT count(*) FROM chapter_versions WHERE book_id=?', (bid,)).fetchone()[0] == 0


def test_trash_api_roundtrip(tmp_path):
    from fastapi.testclient import TestClient
    from story_core.http import create_app
    s = StoryService(tmp_path)
    bid = s.open_book('小说', chapter_count=1, target_words=50)['book_id']
    c = TestClient(create_app(tmp_path))
    assert c.post(f'/api/books/{bid}/trash').status_code == 200
    assert len(c.get('/api/trash').json()) == 1
    assert c.post(f'/api/books/{bid}/restore').status_code == 200
    assert c.delete(f'/api/trash/{bid}').status_code == 400
    c.post(f'/api/books/{bid}/trash')
    assert c.delete(f'/api/trash/{bid}').status_code == 200


def test_trash_rejects_running_book_and_removes_cache_only_on_purge(tmp_path):
    s = StoryService(tmp_path)
    bid = s.open_book('小说', chapter_count=1, target_words=50)['book_id']
    s.control(bid, 'start')
    with pytest.raises(StoryError) as e:
        s.trash_book(bid)
    assert e.value.code == 'RUN_ACTIVE'
    s.control(bid, 'pause')
    audio = tmp_path / 'tts-cache' / bid / 'test.mp3'
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b'audio')
    s.trash_book(bid)
    assert audio.exists()
    s.purge_book(bid)
    assert not audio.exists()
