"""Local author workflow facade. Model tools can propose, never grant trust."""
from . import long_memory as lm, memory_workflow as wf
from .errors import StoryError


class MemoryActions:
    def memory_entities(self, book_id, limit=100, offset=0):
        self.store.book(book_id)
        wf._page(limit, offset)
        with self.store.read() as conn:
            return {'items': [dict(row) for row in conn.execute('SELECT * FROM lm_entities WHERE book_id=? AND branch_id=? ORDER BY display_name,id LIMIT ? OFFSET ?', (book_id, 'main', limit, offset))]}

    def memory_entity(self, book_id, name, actor='author', entity_type='other'):
        wf._text(name, 'name', 200); wf._text(actor, 'actor', 200)
        with self.store.write(book_id) as conn:
            identity = lm.resolve_alias(conn, book_id, name)
            if not identity:
                identity = lm.register_entity(conn, book_id, name, entity_type=entity_type)
                self.store.event(conn, book_id, 'memory_entity_registered', {'entity_id': identity, 'actor': actor})
            return {'entity_id': identity}

    def memory_propose(self, book_id, candidates, request_id, origin='model'):
        with self.store.write(book_id) as conn:
            return wf.submit_proposals(conn, book_id, candidates, request_id=request_id, origin=origin)

    def memory_proposals(self, book_id, status=None, limit=50, offset=0):
        self.store.book(book_id)
        with self.store.read() as conn:
            result = wf.list_proposals(conn, book_id, status=status, limit=limit, offset=offset)
            result['items'] = [wf.preview_proposal(conn, book_id, item['id']) for item in result['items']]
            for item in result['items']:
                candidate = item.get('candidate') or {}
                if not isinstance(candidate, dict): continue
                for source, target in (('subject_entity_id', 'subject_display_name'), ('owner_entity_id', 'owner_display_name')):
                    entity = conn.execute('SELECT display_name FROM lm_entities WHERE book_id=? AND id=?', (book_id, candidate.get(source))).fetchone()
                    item[target] = entity[0] if entity else None
            result['revision'] = conn.execute('SELECT revision FROM books WHERE id=?', (book_id,)).fetchone()[0]
            return result

    def _refresh_memory_leases(self, conn, book_id, revision):
        # Replays only cancel leases older than the accepted author's revision.
        # Candidate, stage, and finite content-review counters are preserved.
        conn.execute("UPDATE tasks SET status='cancelled' WHERE book_id=? AND status='leased' AND base_revision<?", (book_id, revision))

    def memory_decide(self, book_id, proposal_id, *, decision, actor, expected_revision, request_id, trust=False, allow_conflict=False):
        with self.store.write(book_id) as conn:
            result = wf.decide_proposal(conn, book_id, proposal_id, decision=decision, actor=actor,
                expected_revision=expected_revision, request_id=request_id, trust=trust, allow_conflict=allow_conflict)
            if result['canonical_changed']:
                self._refresh_memory_leases(conn, book_id, result['revision'])
            return result

    def memory_bind(self, book_id, proposal_id, entity_id, *, actor, expected_revision, request_id):
        with self.store.write(book_id) as conn:
            result = wf.bind_proposal(conn, book_id, proposal_id, entity_id, actor=actor,
                expected_revision=expected_revision, request_id=request_id)
            self._refresh_memory_leases(conn, book_id, result['revision'])
            return result

    def memory_maintenance(self, book_id, limit=50, offset=0):
        self.store.book(book_id)
        with self.store.read() as conn:
            return wf.maintenance_status(conn, book_id, limit=limit, offset=offset)

    def memory_maintain(self, book_id, *, limit=5, backfill=False):
        if type(backfill) is not bool or type(limit) is not int or not 1 <= limit <= 20:
            raise StoryError('INVALID_REQUEST', '每次本地整理处理 1–20 项。')
        with self.store.write(book_id) as conn:
            if backfill:
                # Discover at most this batch, never load all chapter bodies.
                versions = conn.execute('''SELECT c.version_id FROM chapters c WHERE c.book_id=?
                    AND c.status='committed' AND NOT EXISTS (SELECT 1 FROM lm_maintenance_jobs j
                    WHERE j.book_id=c.book_id AND j.branch_id='main' AND j.source_version=c.version_id)
                    ORDER BY c.number LIMIT ?''', (book_id, limit)).fetchall()
                for version in versions:
                    wf.enqueue_maintenance(conn, book_id, version[0])
            result = wf.run_maintenance_step(conn, book_id, limit=limit)
            result['backfill_remaining'] = conn.execute('''SELECT count(*) FROM chapters c WHERE c.book_id=?
                AND c.status='committed' AND NOT EXISTS (SELECT 1 FROM lm_maintenance_jobs j
                WHERE j.book_id=c.book_id AND j.branch_id='main' AND j.source_version=c.version_id)''', (book_id,)).fetchone()[0]
            return result
