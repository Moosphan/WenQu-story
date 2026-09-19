"""Author scheduling facade for explicit temporal and relational obligations."""
from . import long_memory as lm, memory_workflow as wf
from .errors import StoryError


class PromiseActions:
    def memory_schedule_promise(self, book_id, label, *, actor, expected_revision, request_id, **options):
        allowed = {'due_chapter', 'due_from_chapter', 'due_to_chapter', 'triggers', 'relation_triggers',
                   'mandatory', 'status', 'visibility', 'owner_entity_id', 'source_chapter', 'source_version', 'data'}
        if set(options) - allowed:
            raise StoryError('INVALID_REQUEST', '伏笔调度参数无效。')
        wf._text(actor, 'actor', 200)
        fingerprint = wf._fingerprint('promise_schedule', [label, actor, expected_revision, options])
        with self.store.write(book_id) as conn:
            lm._book(conn, book_id, 'main')
            replay = wf._replay(conn, book_id, 'main', request_id, fingerprint)
            if replay is not None:
                return replay
            wf._revision(conn, book_id, expected_revision)
            identity = lm.schedule_promise(conn, book_id, label, **options)
            revision = expected_revision + 1
            conn.execute('UPDATE books SET revision=? WHERE id=?', (revision, book_id))
            conn.execute("UPDATE tasks SET status='cancelled' WHERE book_id=? AND status='leased' AND base_revision<?", (book_id, revision))
            result = {'promise_id': identity, 'revision': revision}
            wf._audit(conn, book_id, 'main', request_id, 'promise_schedule', actor, fingerprint, result)
            return result

    def memory_promises(self, book_id, chapter_number, *, story_time=None, role='author', pov_entity_id=None):
        with self.store.read() as conn:
            return lm.select_promises(conn, book_id, chapter_number, story_time=story_time,
                                      role=role, pov_entity_id=pov_entity_id)
