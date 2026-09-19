"""Independent manuscript snapshots using ordinary book isolation.

A fork is a complete snapshot with fresh IDs, not another namespace sharing
mutable chapter pointers. Running tasks, credentials and billing are not cloned.
"""
import hashlib
import json
import time

from . import long_memory as lm, memory_workflow as wf
from .storage import dumps, uid

SCHEMA = '''
CREATE TABLE IF NOT EXISTS book_branches (
 book_id TEXT PRIMARY KEY REFERENCES books(id) ON DELETE CASCADE,
 root_book_id TEXT NOT NULL, parent_book_id TEXT,
 fork_revision INTEGER NOT NULL, name TEXT NOT NULL, created_at REAL NOT NULL,
 source_map TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS book_branch_root ON book_branches(root_book_id);
'''

REFERENCE_KEYS = {'book_id', 'entity_id', 'subject_entity_id', 'owner_entity_id', 'entity_ids',
    'fact_id', 'required_fact_ids', 'fact_ids', 'promise_id', 'promise_ids', 'event_id', 'event_ids',
    'version_id', 'source_version', 'dependency_versions', 'depends_on_version', 'source_id', 'target_id',
    'identity_aliases', 'legacy_id', 'winner_event_id', 'summary_id', 'retire_event_ids',
    'repair_id', 'replacement_event_id', 'old_dependencies', 'new_dependencies'}
JSON_COLUMNS = {'data', 'value', 'sources', 'candidate', 'config', 'brief', 'plan', 'project', 'manifest'}
EXCLUDED_MEMORY_TABLES = {'lm_workflow_audit', 'lm_maintenance_jobs', 'lm_source_digests', 'lm_proposals'}


def _remap_json(value, mapping, key=None):
    if isinstance(value, dict):
        return {name: _remap_json(item, mapping, name) for name, item in value.items()}
    if isinstance(value, list):
        return [_remap_json(item, mapping, key) for item in value]
    if isinstance(value, str) and key in REFERENCE_KEYS:
        return mapping.get(value, value)
    return value


def _insert(conn, table, item):
    # Names originate solely from the schema inventory, never request text.
    names = ','.join('"' + column + '"' for column in item)
    conn.execute(f'INSERT INTO "{table}" ({names}) VALUES ({",".join("?" for _ in item)})', list(item.values()))


def fork_book(conn, book_id, *, name, actor, expected_revision, request_id):
    lm._book(conn, book_id, 'main')
    name = wf._text(name, 'name', 200)
    actor = wf._text(actor, 'actor', 200)
    fingerprint = wf._fingerprint('book_fork', [name, actor, expected_revision])
    previous = wf._replay(conn, book_id, 'main', request_id, fingerprint)
    if previous is not None:
        return previous
    wf._revision(conn, book_id, expected_revision)
    original = dict(conn.execute('SELECT * FROM books WHERE id=?', (book_id,)).fetchone())
    tables = ['chapter_versions', 'chapters', 'memories', 'chunks']
    for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'lm_%' ORDER BY name"):
        table = row[0]
        columns = {column['name'] for column in conn.execute(f'PRAGMA table_info("{table}")')}
        if table not in EXCLUDED_MEMORY_TABLES and 'book_id' in columns:
            tables.append(table)
    records = {table: [dict(row) for row in conn.execute(f'SELECT * FROM "{table}" WHERE book_id=?', (book_id,))]
               for table in tables}
    if 'lm_repair_actions' in records:
        records['lm_retired_events'] = [dict(row) for row in conn.execute('''SELECT r.* FROM lm_retired_events r
            JOIN lm_repair_actions a ON a.id=r.repair_id WHERE a.book_id=?''', (book_id,))]
    child = uid('book')
    mapping = {book_id: child}
    for rows in records.values():
        for row in rows:
            if 'id' in row:
                mapping[row['id']] = uid(row['id'].split('_', 1)[0])
    # Fact IDs encode the complete logical identity; recompute rather than
    # generating random IDs that record_fact could never resolve on later writes.
    for row in records.get('lm_facts', []):
        identity = [child, row['branch_id'], mapping[row['subject_entity_id']], row['predicate'],
                    row['scope'], mapping.get(row['owner_entity_id'], row['owner_entity_id'])]
        mapping[row['id']] = 'fact_' + hashlib.sha256(dumps(identity).encode()).hexdigest()[:32]
    with wf._atomic(conn):
        conn.execute('PRAGMA defer_foreign_keys=ON')
        cloned = dict(original, id=child, title=name, status='draft', ending=None, created_at=time.time())
        for column in ('config', 'brief', 'plan', 'project'):
            if cloned.get(column):
                cloned[column] = dumps(_remap_json(json.loads(cloned[column]), mapping))
        if cloned.get('brief'):
            brief = json.loads(cloned['brief']); brief['title'] = name
            cloned['brief'] = dumps(brief)
        _insert(conn, 'books', cloned)
        for table, rows in records.items():
            for row in rows:
                item = {}
                for column, value in row.items():
                    if column == 'id' or column in REFERENCE_KEYS:
                        item[column] = mapping.get(value, value) if isinstance(value, str) else value
                    elif column in JSON_COLUMNS and isinstance(value, str) and not (table == 'memories' and column == 'value'):
                        item[column] = dumps(_remap_json(json.loads(value), mapping))
                    else:
                        item[column] = value
                _insert(conn, table, item)
        lineage = conn.execute('SELECT root_book_id FROM book_branches WHERE book_id=?', (book_id,)).fetchone()
        root = lineage[0] if lineage else book_id
        conn.execute('INSERT OR IGNORE INTO book_branches VALUES (?,?,?,?,?,?,?)',
                     (book_id, root, None, expected_revision, original['title'], time.time(), '{}'))
        conn.execute('INSERT INTO book_branches VALUES (?,?,?,?,?,?,?)',
                     (child, root, book_id, expected_revision, name, time.time(), dumps(mapping)))
        from .retrieval import rebuild_index
        rebuild_index(conn, child)
        result = {'book_id': child, 'parent_book_id': book_id, 'root_book_id': root,
                  'fork_revision': expected_revision, 'name': name}
        wf._audit(conn, book_id, 'main', request_id, 'book_fork', actor, fingerprint, result)
    return result


class BranchActions:
    def fork_book(self, book_id, **options):
        with self.store.write(book_id) as conn:
            return fork_book(conn, book_id, **options)

    def book_branches(self, book_id):
        self.store.book(book_id)
        with self.store.read() as conn:
            lineage = conn.execute('SELECT root_book_id FROM book_branches WHERE book_id=?', (book_id,)).fetchone()
            root = lineage[0] if lineage else book_id
            items = [dict(row) for row in conn.execute('''SELECT b.book_id,b.parent_book_id,b.root_book_id,
                b.fork_revision,b.name,b.created_at FROM book_branches b JOIN books w ON w.id=b.book_id
                WHERE b.root_book_id=? AND NOT EXISTS (SELECT 1 FROM book_trash t WHERE t.book_id=b.book_id)
                ORDER BY b.created_at''', (root,))]
            return {'root_book_id': root, 'items': items}
