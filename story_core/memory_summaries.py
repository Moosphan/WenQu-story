"""Author-verified semantic summaries with immutable source manifests.

Text is submitted by an author or model as a proposal. Quote matching validates
provenance only; accepting meaning always requires an explicit trust decision.
"""
import json
import time

from . import long_memory as lm, memory_workflow as wf
from .errors import StoryError
from .memory import exact_evidence
from .storage import dumps, uid

SCHEMA = '''
CREATE TABLE IF NOT EXISTS lm_semantic_summaries (
 id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
 level TEXT NOT NULL, chapter_start INTEGER NOT NULL, chapter_end INTEGER NOT NULL,
 text TEXT NOT NULL, sources TEXT NOT NULL, visibility TEXT NOT NULL,
 owner_entity_id TEXT, origin TEXT NOT NULL, status TEXT NOT NULL,
 created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS lm_summary_scope
 ON lm_semantic_summaries(book_id,status,chapter_end,level);
'''


def _owners(conn, book, owner):
    if not owner:
        return ['']
    from .memory_identity import resolve_entity_id
    canonical = resolve_entity_id(conn, book, owner)
    return [row[0] for row in conn.execute('''WITH RECURSIVE owners(id) AS (
        SELECT ? UNION SELECT r.source_id FROM lm_entity_redirects r JOIN owners o ON r.target_id=o.id
        WHERE r.book_id=? AND r.branch_id='main') SELECT id FROM owners''', (canonical, book))]


def _sources(conn, book, start, end, refs):
    if not isinstance(refs, list) or len(refs) != end - start + 1:
        raise StoryError('INVALID_SUMMARY', '摘要必须逐章提供完整来源清单。')
    numbers, result = set(), []
    invalid = {row[0] for row in conn.execute(lm._INVALID_VERSIONS + 'SELECT version_id FROM invalid',
                                             {'book': book, 'branch': 'main'})}
    for ref in refs:
        if not isinstance(ref, dict) or set(ref) != {'chapter_number', 'version_id', 'quote'}:
            raise StoryError('INVALID_SUMMARY', '来源需要章节号、版本 ID 和原文引用。')
        number = ref['chapter_number']
        if type(number) is not int or not start <= number <= end or number in numbers:
            raise StoryError('INVALID_SUMMARY', '摘要来源范围无效或重复。')
        quote = wf._text(ref['quote'], 'quote', 2048)
        version = wf._text(ref['version_id'], 'version_id', 200)
        source = conn.execute('''SELECT v.body FROM chapters c JOIN chapter_versions v ON v.id=c.version_id
            WHERE c.book_id=? AND c.number=? AND c.version_id=? AND c.status='committed' ''',
                              (book, number, version)).fetchone()
        if source is None or version in invalid:
            raise StoryError('STALE_SOURCE', '摘要来源已修改、未提交或依赖失效。')
        evidence = exact_evidence(source['body'], quote)
        if evidence is None:
            raise StoryError('INVALID_EVIDENCE', '摘要引用不在来源正文中。')
        numbers.add(number)
        result.append({'chapter_number': number, 'version_id': version, 'quote': evidence})
    return sorted(result, key=lambda ref: ref['chapter_number'])


def submit_summary(conn, book_id, *, level, chapter_start, chapter_end, text,
                   source_refs, visibility, request_id, origin='author', owner_entity_id=None):
    lm._book(conn, book_id, 'main')
    if (level not in {'chapter', 'volume'} or type(chapter_start) is not int
            or type(chapter_end) is not int or not 1 <= chapter_start <= chapter_end
            or chapter_end - chapter_start >= 50
            or (level == 'chapter' and chapter_start != chapter_end)
            or visibility not in {'reader', 'author'} or origin not in {'author', 'model'}):
        raise StoryError('INVALID_SUMMARY', '摘要类型、范围或权限无效；单项至多覆盖 50 章。')
    text = wf._text(text, 'text', 2000 if level == 'chapter' else 4000)
    fingerprint = wf._fingerprint('summary_propose', [level, chapter_start, chapter_end, text,
        source_refs, visibility, origin, owner_entity_id])
    previous = wf._replay(conn, book_id, 'main', request_id, fingerprint)
    if previous is not None:
        return previous
    if owner_entity_id:
        from .memory_identity import resolve_entity_id
        owner_entity_id = resolve_entity_id(conn, book_id, owner_entity_id)
    sources = _sources(conn, book_id, chapter_start, chapter_end, source_refs)
    with wf._atomic(conn):
        identity = uid('summary')
        conn.execute('INSERT INTO lm_semantic_summaries VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                     (identity, book_id, level, chapter_start, chapter_end, text, dumps(sources),
                      visibility, owner_entity_id, origin, 'pending', time.time()))
        result = {'summary_id': identity, 'status': 'pending'}
        wf._audit(conn, book_id, 'main', request_id, 'summary_propose', origin, fingerprint, result)
    return result


def decide_summary(conn, book_id, summary_id, *, decision, actor, expected_revision,
                   request_id, trust=False):
    lm._book(conn, book_id, 'main')
    actor = wf._text(actor, 'actor', 200)
    if decision not in {'accept', 'reject'} or type(trust) is not bool:
        raise StoryError('INVALID_REQUEST', '摘要核实决定无效。')
    fingerprint = wf._fingerprint('summary_decide', [summary_id, decision, actor, expected_revision, trust])
    previous = wf._replay(conn, book_id, 'main', request_id, fingerprint)
    if previous is not None:
        return previous
    wf._revision(conn, book_id, expected_revision)
    row = conn.execute('SELECT * FROM lm_semantic_summaries WHERE id=? AND book_id=?', (summary_id, book_id)).fetchone()
    if row is None or row['status'] != 'pending':
        raise StoryError('INVALID_SUMMARY', '摘要不存在或已经处理。')
    if decision == 'accept':
        if trust is not True:
            raise StoryError('EXPLICIT_TRUST_REQUIRED', '请明确核实摘要含义与来源。')
        _sources(conn, book_id, row['chapter_start'], row['chapter_end'], json.loads(row['sources']))
    with wf._atomic(conn):
        status = 'accepted' if decision == 'accept' else 'rejected'
        if decision == 'accept':
            # A new verified summary replaces the active view, never its text.
            conn.execute('''UPDATE lm_semantic_summaries SET status='superseded'
                WHERE book_id=? AND level=? AND chapter_start=? AND chapter_end=?
                AND visibility=? AND COALESCE(owner_entity_id,'') IN (SELECT value FROM json_each(?)) AND status='accepted' ''',
                (book_id, row['level'], row['chapter_start'], row['chapter_end'], row['visibility'], dumps(_owners(conn, book_id, row['owner_entity_id']))))
            conn.execute('UPDATE books SET revision=revision+1 WHERE id=?', (book_id,))
            conn.execute("UPDATE tasks SET status='cancelled' WHERE book_id=? AND status='leased' AND base_revision<=?", (book_id, expected_revision))
        conn.execute('UPDATE lm_semantic_summaries SET status=? WHERE id=?', (status, summary_id))
        result = {'summary_id': summary_id, 'status': status, 'revision': expected_revision + int(decision == 'accept')}
        wf._audit(conn, book_id, 'main', request_id, 'summary_decide', actor, fingerprint, result)
    return result


def select_summaries(conn, book_id, *, through_chapter, role, pov_entity_id=None,
                     recent_count=5, volume_count=3):
    lm._book(conn, book_id, 'main')
    if role not in {'author', 'reader'} or type(through_chapter) is not int or through_chapter < 0:
        raise StoryError('INVALID_SCOPE', '摘要读取范围无效。')
    for value in (recent_count, volume_count):
        if type(value) is not int or not 0 <= value <= 10:
            raise StoryError('INVALID_SCOPE', '摘要读取数量应为 0–10。')
    result = []
    try:
        owners = _owners(conn, book_id, pov_entity_id)
    except StoryError as error:
        if error.code == 'INVALID_SCOPE':
            return []
        raise
    for level, limit in (('chapter', recent_count), ('volume', volume_count)):
        if not limit:
            continue
        rows = conn.execute(lm._INVALID_VERSIONS + '''SELECT s.* FROM lm_semantic_summaries s
            WHERE s.book_id=:book AND s.level=:level AND s.status='accepted' AND s.chapter_end<=:boundary
            AND (:role='author' AND :pov IS NULL OR s.visibility='reader')
            AND (:pov IS NULL OR s.owner_entity_id IN (SELECT value FROM json_each(:owners)))
            AND NOT EXISTS (SELECT 1 FROM json_each(s.sources) ref LEFT JOIN chapters c
                ON c.book_id=s.book_id AND c.number=json_extract(ref.value,'$.chapter_number')
                AND c.version_id=json_extract(ref.value,'$.version_id')
                WHERE c.version_id IS NULL OR c.status!='committed'
                OR c.version_id IN (SELECT version_id FROM invalid))
            ORDER BY s.chapter_end DESC,s.created_at DESC LIMIT :count''',
            {'book': book_id, 'branch': 'main', 'level': level, 'boundary': through_chapter,
             'role': role, 'pov': pov_entity_id, 'owners': dumps(owners), 'count': limit})
        for row in rows:
            try:
                sources = _sources(conn, book_id, row['chapter_start'], row['chapter_end'], json.loads(row['sources']))
            except StoryError as error:
                if error.code in {'STALE_SOURCE', 'INVALID_EVIDENCE'}:
                    continue
                raise
            result.append({'id': row['id'], 'kind': 'verified_summary', 'level': level,
                'chapter_start': row['chapter_start'], 'chapter_end': row['chapter_end'],
                'text': row['text'], 'sources': sources, 'visibility': row['visibility']})
    return result


class SummaryActions:
    def memory_summary_propose(self, book_id, **options):
        with self.store.write(book_id) as conn:
            return submit_summary(conn, book_id, **options)

    def memory_summary_decide(self, book_id, summary_id, **options):
        with self.store.write(book_id) as conn:
            return decide_summary(conn, book_id, summary_id, **options)

    def memory_summaries(self, book_id, *, status=None, limit=50, offset=0):
        self.store.book(book_id)
        wf._page(limit, offset)
        if status not in {None, 'pending', 'accepted', 'rejected', 'superseded', 'stale'}:
            raise StoryError('INVALID_REQUEST', '摘要状态无效。')
        with self.store.read() as conn:
            rows = conn.execute('''SELECT * FROM lm_semantic_summaries WHERE book_id=?
                AND (? IS NULL OR status=?) ORDER BY created_at DESC LIMIT ? OFFSET ?''',
                (book_id, status, status, limit, offset))
            items = []
            for row in rows:
                item = dict(row)
                item['sources'] = json.loads(item['sources'])
                try:
                    _sources(conn, book_id, item['chapter_start'], item['chapter_end'], item['sources'])
                    item['source_status'] = 'current'
                except StoryError:
                    item['source_status'] = 'stale'
                items.append(item)
            return {'items': items}
