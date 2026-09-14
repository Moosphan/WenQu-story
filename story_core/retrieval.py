"""Replaceable, bounded retrieval over version-filtered SQLite memory evidence.

Adapters supply IDs only. Core resolves every candidate through authorization.
No vector service is enabled by default. Chapter prose remains in the archive;
this index covers evidenced memory, so absence is explicitly unknown.
"""
from dataclasses import dataclass
import json
from itertools import islice

from .context_compiler import ContextCompiler, ContextPolicy
from .errors import StoryError
from .memory import _terms

MAX_CANDIDATES = 64
MAX_LOOKUPS = 2


@dataclass(frozen=True)
class RetrievalScope:
    book_id: str
    through_chapter: int
    role: str = 'author'
    pov: str | None = None
    branch_id: str = 'main'

    def __post_init__(self):
        if self.role not in ('author', 'reader') or type(self.through_chapter) is not int or self.through_chapter < 0:
            raise StoryError('INVALID_SCOPE', '检索需要有效角色与已读章节边界。')


def initialize_index(conn):
    # Individual statements preserve a caller's commit transaction.
    conn.execute('''CREATE TABLE IF NOT EXISTS memory_terms (
        book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
        term TEXT NOT NULL, memory_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
        PRIMARY KEY(book_id,term,memory_id))''')
    conn.execute('''CREATE TABLE IF NOT EXISTS memory_indexed (
        memory_id TEXT PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,
        book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE)''')
    conn.execute('CREATE INDEX IF NOT EXISTS memory_indexed_book ON memory_indexed(book_id)')


def index_version(conn, book_id, version_id):
    initialize_index(conn)
    for row in conn.execute('SELECT id,key,value,data FROM memories WHERE book_id=? AND version_id=?', (book_id, version_id)):
        data = json.loads(row['data'])
        text = ' '.join([row['key'], row['value'], *(data.get('aliases') or [])])
        # Memory items have bounded source output size. Index distinct Chinese
        # bigrams/words once; never assume English FTS tokenization works here.
        terms = _terms(text)
        conn.execute('DELETE FROM memory_terms WHERE memory_id=?', (row['id'],))
        conn.executemany('INSERT OR IGNORE INTO memory_terms VALUES (?,?,?)', ((book_id, t, row['id']) for t in terms))
        conn.execute('INSERT OR IGNORE INTO memory_indexed VALUES (?,?)', (row['id'], book_id))


def rebuild_index(conn, book_id):
    """Explicit local backfill, safe to repeat; never edits source memory."""
    initialize_index(conn)
    for row in conn.execute('SELECT DISTINCT version_id FROM memories WHERE book_id=?', (book_id,)).fetchall():
        index_version(conn, book_id, row[0])


class SQLiteRetriever:
    def __init__(self, store, semantic=None):
        self.store, self.semantic = store, semantic

    def search(self, query, scope, *, limit=10, exact_ids=()):
        self.store.book(scope.book_id)
        if not isinstance(query, str) or len(query) > 10000 or type(limit) is not int or not 1 <= limit <= 50:
            raise StoryError('INVALID_REQUEST', '检索词或数量无效。')
        if scope.branch_id != 'main':
            return {'hits': [], 'candidates_examined': 0, 'index_complete': False, 'reason': 'legacy_index_has_only_main_branch'}
        terms = sorted(_terms(query), key=lambda term: (-len(term), term))[:32]
        # Stable scope parameters apply both to indexed and external channels.
        where = '''m.book_id=:book AND m.chapter_number<=:boundary AND c.status='committed'
            AND (:role='author' OR m.visibility='reader')
            AND (:pov='' OR (m.visibility='reader' AND
                 (m.kind!='knowledge' OR json_extract(m.data,'$.owner')=:pov)))'''
        join = '''FROM memories m JOIN chapters c ON c.book_id=m.book_id
            AND c.number=m.chapter_number AND c.version_id=m.version_id'''
        params = {'book': scope.book_id, 'boundary': scope.through_chapter, 'role': scope.role, 'pov': scope.pov or '', 'terms': json.dumps(terms)}
        with self.store.read() as conn:
            present = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_terms'").fetchone()
            ranked = []
            if present and terms:
                ranked = conn.execute('SELECT m.id,COUNT(*) score ' + join + ''' JOIN memory_terms t ON t.memory_id=m.id AND t.book_id=m.book_id
                    WHERE ''' + where + ''' AND t.term IN (SELECT value FROM json_each(:terms))
                    GROUP BY m.id ORDER BY score DESC,m.chapter_number DESC,m.id LIMIT 64''', params).fetchall()
            ids = [row['id'] for row in ranked]
            # Reciprocal-rank fusion, then authorization again on fetched rows.
            scores = {row['id']: 1/(60+i) for i, row in enumerate(ranked, 1)}
            channels = [list(islice(iter(exact_ids), MAX_CANDIDATES))]
            if self.semantic:
                channels.append(list(islice(iter(self.semantic.candidates(query, scope, MAX_CANDIDATES)), MAX_CANDIDATES)))
            for channel in channels:
                for i, identity in enumerate(channel, 1):
                    if not isinstance(identity, str): continue
                    scores[identity] = scores.get(identity, 0) + 1/(60+i)
                    ids.append(identity)
            ids = list(dict.fromkeys(ids))
            params['ids'] = json.dumps(ids)
            rows = conn.execute('SELECT m.* ' + join + ' WHERE ' + where + ' AND m.id IN (SELECT value FROM json_each(:ids))', params).fetchall() if ids else []
            rows = sorted(rows, key=lambda row: (-scores[row['id']], -row['chapter_number'], row['id']))[:MAX_CANDIDATES]
            unindexed = conn.execute('SELECT 1 ' + join + ' WHERE ' + where + ' AND NOT EXISTS (SELECT 1 FROM memory_indexed i WHERE i.memory_id=m.id) LIMIT 1', params).fetchone() if present else True
        hits = []
        for row in rows[:limit]:
            item = json.loads(row['data'])
            item.update(id=row['id'], source={'chapter_number': row['chapter_number'], 'version_id': row['version_id']}, score=scores[row['id']])
            hits.append(item)
        return {'hits': hits, 'candidates_examined': len(rows), 'index_complete': not bool(unindexed),
                'strategy': 'sqlite-chinese-rrf-v1', 'semantic_enabled': self.semantic is not None}


def bounded_lookup(store, book_id, run_id, chapter_number, data, schema, query, *, scope, policy=None, stage='draft'):
    """Durable helper for host integrations; replaces evidence, never appends chat.

    Caller owns task/lease authorization. Current providers do not automatically
    call this helper. Charges an attempted lookup even if it cannot fit, limiting
    repeated expensive searches across helper restarts within the same run/chapter.
    """
    if scope.book_id != book_id or scope.through_chapter >= chapter_number:
        raise StoryError('INVALID_SCOPE', '补查只能读取本作品当前章之前的资料。')
    with store.write(book_id) as conn:
        used = 0
        for row in conn.execute("SELECT payload FROM events WHERE book_id=? AND run_id=? AND kind='context_lookup'", (book_id, run_id)):
            used += json.loads(row[0]).get('chapter_number') == chapter_number
        if used >= MAX_LOOKUPS:
            raise StoryError('LOOKUP_LIMIT', '本章已用完 2 次补查；请明确待处理资料。')
        store.event(conn, book_id, 'context_lookup', {'chapter_number': chapter_number, 'lookup_number': used + 1}, run_id)
    result = SQLiteRetriever(store).search(query, scope)
    replaced = {**data, 'historical_evidence': result['hits']}
    return ContextCompiler(policy or ContextPolicy.from_env(stage)).compile(replaced, schema, stage=stage)
