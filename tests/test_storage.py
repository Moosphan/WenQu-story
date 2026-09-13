import pytest


def test_book_survives_new_store_instance(tmp_path):
    from story_core.storage import Store
    first = Store(tmp_path)
    book = first.create_book("给我开本都市小说", "旧收音机", {"chapter_count": 40})
    other = Store(tmp_path)
    assert other.book(book["book_id"])["title"] == "旧收音机"
    assert other.book(book["book_id"])["revision"] == 0


def test_failed_transaction_keeps_original_book_and_no_event(tmp_path):
    from story_core.storage import Store
    store = Store(tmp_path)
    book = store.create_book("开书", "旧收音机", {})
    with pytest.raises(RuntimeError):
        with store.write(book["book_id"], expected_revision=0) as conn:
            conn.execute("UPDATE books SET title='错误',revision=1 WHERE id=?", (book["book_id"],))
            raise RuntimeError("injected failure before commit")
    assert store.book(book["book_id"])["title"] == "旧收音机"
    assert store.book(book["book_id"])["revision"] == 0


def test_stale_revision_rejected_across_connections(tmp_path):
    from story_core.storage import Store
    from story_core.errors import StoryError
    store = Store(tmp_path)
    book = store.create_book("开书", "旧收音机", {})
    with store.write(book["book_id"], expected_revision=0) as conn:
        conn.execute("UPDATE books SET revision=1 WHERE id=?", (book["book_id"],))
    with pytest.raises(StoryError) as caught:
        with Store(tmp_path).write(book["book_id"], expected_revision=0):
            pytest.fail("stale write entered")
    assert caught.value.code == "STALE_REVISION"


def test_unknown_book_has_structured_error(tmp_path):
    from story_core.storage import Store
    from story_core.errors import StoryError
    with pytest.raises(StoryError) as caught:
        Store(tmp_path).book("missing")
    assert caught.value.code == "NOT_FOUND"
