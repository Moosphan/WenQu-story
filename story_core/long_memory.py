"""Opt-in, source-preserving normalized memory on the existing SQLite store.

Calls accept the service's transaction connection. A verified flag is an explicit
caller trust decision, never inferred from a quote or a model confidence score.
Branch namespaces share the manuscript's existing active version pointers; full
manuscript branching is deliberately outside this foundation.
"""
import hashlib
import json
import math

from .errors import StoryError
from .memory_identity import resolve_entity_id, resolve_fact_id, canonical_value, preview_entity_merge, merge_entities
from .storage import dumps, uid

SCHEMA = """
CREATE TABLE IF NOT EXISTS lm_entities (
 id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
 branch_id TEXT NOT NULL, display_name TEXT NOT NULL, entity_type TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS lm_entity_scope ON lm_entities(book_id,branch_id);
CREATE TABLE IF NOT EXISTS lm_aliases (
 book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE, branch_id TEXT NOT NULL,
 alias TEXT NOT NULL, entity_id TEXT NOT NULL REFERENCES lm_entities(id) ON DELETE CASCADE,
 PRIMARY KEY(book_id,branch_id,alias,entity_id));
CREATE TABLE IF NOT EXISTS lm_facts (
 id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
 branch_id TEXT NOT NULL, subject_entity_id TEXT NOT NULL REFERENCES lm_entities(id) ON DELETE CASCADE,
 predicate TEXT NOT NULL, scope TEXT NOT NULL, owner_entity_id TEXT NOT NULL,
 UNIQUE(book_id,branch_id,subject_entity_id,predicate,scope,owner_entity_id));
CREATE TABLE IF NOT EXISTS lm_fact_events (
 id TEXT PRIMARY KEY, fact_id TEXT NOT NULL REFERENCES lm_facts(id) ON DELETE CASCADE,
 book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE, branch_id TEXT NOT NULL,
 value TEXT NOT NULL, source_chapter INTEGER NOT NULL, source_version TEXT NOT NULL,
 evidence TEXT NOT NULL, story_valid_from REAL NOT NULL, story_valid_to REAL,
 known_from_chapter INTEGER NOT NULL, revealed_from_chapter INTEGER,
 visibility TEXT NOT NULL, verified INTEGER NOT NULL, invalidated INTEGER NOT NULL DEFAULT 0,
 recorded_revision INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS lm_fact_source ON lm_fact_events(book_id,branch_id,source_version);
CREATE INDEX IF NOT EXISTS lm_fact_projection ON lm_fact_events(fact_id,story_valid_from);
CREATE TABLE IF NOT EXISTS lm_dependencies (
 book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE, branch_id TEXT NOT NULL,
 source_version TEXT NOT NULL, depends_on_version TEXT NOT NULL,
 PRIMARY KEY(book_id,branch_id,source_version,depends_on_version));
CREATE INDEX IF NOT EXISTS lm_dependency_parent ON lm_dependencies(book_id,branch_id,depends_on_version);
CREATE TABLE IF NOT EXISTS lm_promises (
 id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
 branch_id TEXT NOT NULL, label TEXT NOT NULL, status TEXT NOT NULL,
 due_chapter INTEGER, triggers TEXT NOT NULL, mandatory INTEGER NOT NULL,
 visibility TEXT NOT NULL, owner_entity_id TEXT NOT NULL, source_chapter INTEGER,
 source_version TEXT, data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS lm_promise_schedule ON lm_promises(book_id,branch_id,status,due_chapter);
CREATE TABLE IF NOT EXISTS lm_promise_status_events (
 id TEXT PRIMARY KEY, promise_id TEXT NOT NULL REFERENCES lm_promises(id) ON DELETE CASCADE,
 book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE, branch_id TEXT NOT NULL,
 status TEXT NOT NULL, source_chapter INTEGER, source_version TEXT,
 invalidated INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS lm_promise_status_history ON lm_promise_status_events(promise_id);
CREATE TABLE IF NOT EXISTS lm_migrations (
 book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE, branch_id TEXT NOT NULL,
 legacy_id TEXT NOT NULL, entity_id TEXT, status TEXT NOT NULL, reason TEXT NOT NULL,
 PRIMARY KEY(book_id,branch_id,legacy_id));
"""


def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise StoryError('INVALID_REQUEST', f'{field} 必须为非空文本。')
    return value.strip()


def _book(conn, book_id, branch_id):
    _text(branch_id, 'branch_id')
    if not conn.execute('SELECT 1 FROM books WHERE id=?', (book_id,)).fetchone():
        raise StoryError('NOT_FOUND', '找不到这本书。')
    if conn.execute('SELECT 1 FROM book_trash WHERE book_id=?', (book_id,)).fetchone():
        raise StoryError('BOOK_TRASHED', '作品已在回收站。')


def _entity(conn, book_id, entity_id, branch_id):
    row = conn.execute('SELECT * FROM lm_entities WHERE id=? AND book_id=? AND branch_id=?',
                       (entity_id, book_id, branch_id)).fetchone()
    if row is None:
        raise StoryError('INVALID_SCOPE', '实体不属于当前作品与分支。')
    return row


def register_entity(conn, book_id, display_name, *, entity_type='other', branch_id='main', entity_id=None):
    _book(conn, book_id, branch_id)
    display_name = _text(display_name, 'display_name')
    if entity_type not in {'person', 'item', 'place', 'organization', 'creature', 'other'}:
        raise StoryError('INVALID_REQUEST', '实体类型无效。')
    entity_id = entity_id or uid('entity')
    existing = conn.execute('SELECT * FROM lm_entities WHERE id=?', (entity_id,)).fetchone()
    if existing:
        _entity(conn, book_id, entity_id, branch_id)
        return resolve_entity_id(conn, book_id, entity_id, branch_id=branch_id)
    conn.execute('INSERT INTO lm_entities VALUES (?,?,?,?,?)',
                 (entity_id, book_id, branch_id, display_name, entity_type))
    entity_id = resolve_entity_id(conn, book_id, entity_id, branch_id=branch_id)
    register_alias(conn, book_id, entity_id, display_name, branch_id=branch_id)
    return entity_id


def register_alias(conn, book_id, entity_id, alias, *, branch_id='main'):
    _book(conn, book_id, branch_id)
    entity_id = resolve_entity_id(conn, book_id, entity_id, branch_id=branch_id)
    conn.execute('INSERT OR IGNORE INTO lm_aliases VALUES (?,?,?,?)',
                 (book_id, branch_id, _text(alias, 'alias'), entity_id))


def rename_entity(conn, book_id, entity_id, display_name, *, branch_id='main'):
    entity_id = resolve_entity_id(conn, book_id, entity_id, branch_id=branch_id)
    register_alias(conn, book_id, entity_id, display_name, branch_id=branch_id)
    conn.execute('UPDATE lm_entities SET display_name=? WHERE id=?', (display_name.strip(), entity_id))
    return entity_id


def resolve_alias(conn, book_id, alias, *, branch_id='main'):
    _book(conn, book_id, branch_id)
    rows = conn.execute('SELECT entity_id FROM lm_aliases WHERE book_id=? AND branch_id=? AND alias=?',
                        (book_id, branch_id, _text(alias, 'alias'))).fetchall()
    rows = sorted({resolve_entity_id(conn, book_id, row[0], branch_id=branch_id) for row in rows})
    if len(rows) > 1:
        raise StoryError('AMBIGUOUS_ENTITY', '该别名对应多个实体，请明确指定实体 ID。')
    return rows[0] if rows else None


def _chapter_number(number, field, *, allow_zero=False):
    if type(number) is not int or number < (0 if allow_zero else 1):
        raise StoryError('INVALID_SCOPE', f'{field} 无效。')
    return number


def _source(conn, book_id, source_chapter, source_version):
    _chapter_number(source_chapter, 'source_chapter')
    row = conn.execute('SELECT body FROM chapter_versions WHERE id=? AND book_id=? AND number=?',
                       (source_version, book_id, source_chapter)).fetchone()
    if row is None:
        raise StoryError('INVALID_SCOPE', '事实来源不属于指定作品与章节。')
    return row['body']


def record_fact(conn, book_id, subject_entity_id, predicate, value, *, source_chapter, source_version,
                evidence, story_valid_from, story_valid_to=None, known_from_chapter=None,
                revealed_from_chapter=None, scope='objective', owner_entity_id=None,
                visibility='reader', verified=False, dependency_versions=(), branch_id='main',
                recorded_revision=0):
    """Append an immutable assertion; return its stable subject/predicate/scope ID."""
    _book(conn, book_id, branch_id)
    subject_entity_id = resolve_entity_id(conn, book_id, subject_entity_id, branch_id=branch_id)
    value = canonical_value(conn, book_id, value, branch_id)
    predicate = _text(predicate, 'predicate')
    if scope not in {'objective', 'character_belief', 'author_plan'} or visibility not in {'reader', 'author'}:
        raise StoryError('INVALID_SCOPE', '事实范围无效。')
    if scope == 'character_belief' and not owner_entity_id:
        raise StoryError('INVALID_SCOPE', '角色信念必须指定所属实体。')
    if scope != 'character_belief' and owner_entity_id:
        raise StoryError('INVALID_SCOPE', '只有角色信念可以指定所属实体。')
    if owner_entity_id:
        owner_entity_id = resolve_entity_id(conn, book_id, owner_entity_id, branch_id=branch_id)
    if type(verified) is not bool:
        raise StoryError('INVALID_REQUEST', 'verified 必须为显式布尔值。')
    for instant in (story_valid_from, story_valid_to):
        if instant is not None and (type(instant) not in (int, float) or not math.isfinite(instant)):
            raise StoryError('INVALID_REQUEST', '故事时间必须为有限数值。')
    if story_valid_from is None or (story_valid_to is not None and story_valid_to <= story_valid_from):
        raise StoryError('INVALID_REQUEST', '故事时间区间无效。')
    body = _source(conn, book_id, source_chapter, source_version)
    from .memory import exact_evidence
    quote = exact_evidence(body, _text(evidence, 'evidence'))
    if quote is None:
        raise StoryError('INVALID_EVIDENCE', '事实证据不在来源正文中。')
    known = source_chapter if known_from_chapter is None else _chapter_number(known_from_chapter, 'known_from_chapter')
    revealed = source_chapter if revealed_from_chapter is None and visibility == 'reader' else revealed_from_chapter
    if revealed is not None:
        _chapter_number(revealed, 'revealed_from_chapter')
    dependencies = set(dependency_versions)
    for version in dependencies:
        if not conn.execute('SELECT 1 FROM chapter_versions WHERE id=? AND book_id=?', (version, book_id)).fetchone():
            raise StoryError('INVALID_SCOPE', '依赖版本不属于当前作品。')
    owner = owner_entity_id or ''
    key = dumps([book_id, branch_id, subject_entity_id, predicate, scope, owner])
    fact_id = 'fact_' + hashlib.sha256(key.encode()).hexdigest()[:32]
    conn.execute('INSERT OR IGNORE INTO lm_facts VALUES (?,?,?,?,?,?,?)',
                 (fact_id, book_id, branch_id, subject_entity_id, predicate, scope, owner))
    conn.execute('''INSERT INTO lm_fact_events
        (id,fact_id,book_id,branch_id,value,source_chapter,source_version,evidence,story_valid_from,
         story_valid_to,known_from_chapter,revealed_from_chapter,visibility,verified,recorded_revision)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                 (uid('assertion'), fact_id, book_id, branch_id, dumps(value), source_chapter, source_version,
                  quote, story_valid_from, story_valid_to, known, revealed, visibility, int(verified), recorded_revision))
    for version in dependencies:
        conn.execute('INSERT OR IGNORE INTO lm_dependencies VALUES (?,?,?,?)',
                     (book_id, branch_id, source_version, version))
    return fact_id


# Version-granularity dependencies conservatively invalidate every projection
# from a derived chapter if any of that chapter's recorded sources is stale.
_INVALID_VERSIONS = '''WITH RECURSIVE invalid(version_id) AS (
 SELECT v.id FROM chapter_versions v LEFT JOIN chapters c
 ON c.book_id=v.book_id AND c.number=v.number AND c.version_id=v.id AND c.status='committed'
 WHERE v.book_id=:book AND c.version_id IS NULL
 UNION SELECT source_version FROM lm_fact_events
 WHERE book_id=:book AND branch_id=:branch AND invalidated=1
 AND id NOT IN (SELECT event_id FROM lm_retired_events)
 UNION SELECT source_version FROM lm_promise_status_events
 WHERE book_id=:book AND branch_id=:branch AND invalidated=1 AND source_version IS NOT NULL
 AND id NOT IN (SELECT event_id FROM lm_retired_events)
 UNION SELECT d.source_version FROM lm_dependencies d JOIN invalid i ON d.depends_on_version=i.version_id
 WHERE d.book_id=:book AND d.branch_id=:branch
) '''


def invalidate_version(conn, book_id, version_id, *, branch_id='main'):
    """Mark source and transitive derived assertions stale without deleting history."""
    _book(conn, book_id, branch_id)
    rows = conn.execute('''WITH RECURSIVE affected(version_id) AS (
       SELECT id FROM chapter_versions WHERE id=? AND book_id=?
       UNION SELECT d.source_version FROM lm_dependencies d JOIN affected a ON d.depends_on_version=a.version_id
       WHERE d.book_id=? AND d.branch_id=?) SELECT version_id FROM affected''',
                        (version_id, book_id, book_id, branch_id)).fetchall()
    from .memory_repair import open_case
    total = 0
    for row in rows:
        open_case(conn, book_id, branch_id, row[0])
        total += conn.execute('UPDATE lm_fact_events SET invalidated=1 WHERE book_id=? AND branch_id=? AND source_version=? AND invalidated=0',
                              (book_id, branch_id, row[0])).rowcount
        total += conn.execute('UPDATE lm_promise_status_events SET invalidated=1 WHERE book_id=? AND branch_id=? AND source_version=? AND invalidated=0',
                              (book_id, branch_id, row[0])).rowcount
    return total


def current_state(conn, book_id, entity_ids, *, through_chapter=None, story_time,
                  role='author', pov_entity_id=None, branch_id='main', include_author_plan=False,
                  fact_ids=None):
    """Project verified assertions after book, version, time, and knowledge guards.

    No top-k is applied to state: the compiler must budget selected facts and
    explicitly surface any hard obligation that cannot fit.
    """
    _book(conn, book_id, branch_id)
    if role not in {'reader', 'author'} or (role == 'reader' and through_chapter is None):
        raise StoryError('INVALID_SCOPE', '读者必须指定已读章节边界。')
    boundary = 2**31 if through_chapter is None else _chapter_number(through_chapter, 'through_chapter', allow_zero=True)
    if type(story_time) not in (int, float) or not math.isfinite(story_time):
        raise StoryError('INVALID_SCOPE', '必须指定有限故事时间。')
    if pov_entity_id:
        pov_entity_id = resolve_entity_id(conn, book_id, pov_entity_id, branch_id=branch_id)
    ids = list({resolve_entity_id(conn, book_id, eid, branch_id=branch_id) for eid in entity_ids if conn.execute('SELECT 1 FROM lm_entities WHERE id=? AND book_id=? AND branch_id=?', (eid, book_id, branch_id)).fetchone()})
    if fact_ids is not None:
        fact_ids = list({resolve_fact_id(conn, book_id, fid, branch_id=branch_id) for fid in fact_ids if conn.execute('SELECT 1 FROM lm_facts WHERE id=? AND book_id=? AND branch_id=?', (fid, book_id, branch_id)).fetchone()})
    if not ids and not fact_ids:
        return []
    # json_each avoids SQLite variable limits for explicit large entity lists.
    params = dict(book=book_id, branch=branch_id, boundary=boundary, instant=story_time,
                  entities=dumps(ids), role=role, pov=pov_entity_id or '', plans=int(include_author_plan),
                  facts=dumps(fact_ids) if fact_ids is not None else None)
    rows = conn.execute(_INVALID_VERSIONS + '''SELECT e.*, f.id AS canonical_fact_id, f.subject_entity_id, f.predicate, f.scope, f.owner_entity_id
      FROM lm_fact_events e LEFT JOIN lm_fact_redirects r ON r.source_id=e.fact_id AND r.book_id=e.book_id AND r.branch_id=e.branch_id
      JOIN lm_facts f ON f.id=COALESCE(r.target_id,e.fact_id)
      JOIN chapters c ON c.book_id=e.book_id AND c.number=e.source_chapter AND c.version_id=e.source_version
      WHERE e.book_id=:book AND e.branch_id=:branch AND c.status='committed'
      AND ((:facts IS NULL AND f.subject_entity_id IN (SELECT value FROM json_each(:entities)))
           OR f.id IN (SELECT value FROM json_each(:facts)))
      AND e.id NOT IN (SELECT event_id FROM lm_event_supersessions)
      AND e.verified=1 AND e.invalidated=0 AND e.source_version NOT IN (SELECT version_id FROM invalid)
      AND e.source_chapter<=:boundary AND e.known_from_chapter<=:boundary
      AND e.story_valid_from<=:instant AND (e.story_valid_to IS NULL OR :instant<e.story_valid_to)
      AND ((:role='author' AND :pov='') OR (e.visibility='reader' AND e.revealed_from_chapter IS NOT NULL AND e.revealed_from_chapter<=:boundary))
      AND (f.scope='objective' OR (f.scope='character_belief' AND f.owner_entity_id=:pov)
           OR (f.scope='author_plan' AND :role='author' AND :plans=1 AND :pov=''))
      ORDER BY e.story_valid_from,e.source_chapter,e.rowid''', params).fetchall()
    latest = {}
    for row in rows:
        item = dict(row)
        item['event_id'] = item.pop('id')
        item['fact_id'] = item.pop('canonical_fact_id')
        item['value'] = canonical_value(conn, book_id, json.loads(item['value']), branch_id)
        item['source'] = dict(chapter_number=item['source_chapter'], version_id=item['source_version'], quote=item['evidence'])
        latest[item['fact_id']] = item
    return list(latest.values())


def schedule_promise(conn, book_id, label, *, due_chapter=None, triggers=(), mandatory=False,
                     promise_id=None, status='dormant', visibility='author', owner_entity_id=None,
                     source_chapter=None, source_version=None, branch_id='main', data=None):
    _book(conn, book_id, branch_id)
    if status not in {'dormant', 'active', 'due', 'resolved', 'cancelled'} or visibility not in {'reader', 'author'}:
        raise StoryError('INVALID_REQUEST', '伏笔状态或可见性无效。')
    if due_chapter is not None:
        _chapter_number(due_chapter, 'due_chapter')
    if owner_entity_id:
        owner_entity_id = resolve_entity_id(conn, book_id, owner_entity_id, branch_id=branch_id)
    if source_version is not None or source_chapter is not None:
        _source(conn, book_id, source_chapter, source_version)
    trigger_list = [_text(trigger, 'trigger') for trigger in triggers]
    promise_id = promise_id or uid('promise')
    existing = conn.execute('SELECT book_id,branch_id FROM lm_promises WHERE id=?', (promise_id,)).fetchone()
    if existing:
        if tuple(existing) != (book_id, branch_id):
            raise StoryError('INVALID_SCOPE', '伏笔不属于当前作品与分支。')
        return promise_id
    conn.execute('INSERT INTO lm_promises VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                 (promise_id, book_id, branch_id, _text(label, 'label'), status, due_chapter,
                  dumps(trigger_list), int(mandatory), visibility, owner_entity_id or '',
                  source_chapter, source_version, dumps(data or {})))
    return promise_id


def set_promise_status(conn, book_id, promise_id, status, *, branch_id='main',
                       source_chapter=None, source_version=None, dependency_versions=()):
    """Append a lifecycle event; sourced events apply only while their version is active.

    Source-free calls establish a new administrative baseline, superseding all
    previously recorded transitions. Later sourced transitions may supersede that
    baseline and are ordered by source chapter, with insertion order only breaking
    same-chapter ties. Generated fulfillment/cancellation must supply its version.
    """
    _book(conn, book_id, branch_id)
    if status not in {'dormant', 'active', 'due', 'resolved', 'cancelled'}:
        raise StoryError('INVALID_REQUEST', '伏笔状态无效。')
    if not conn.execute('SELECT 1 FROM lm_promises WHERE id=? AND book_id=? AND branch_id=?',
                        (promise_id, book_id, branch_id)).fetchone():
        raise StoryError('NOT_FOUND', '找不到该伏笔。')
    if source_version is not None or source_chapter is not None:
        _source(conn, book_id, source_chapter, source_version)
    dependencies = set(dependency_versions)
    if dependencies and source_version is None:
        raise StoryError('INVALID_REQUEST', '章节依赖必须关联来源版本。')
    for version in dependencies:
        if not conn.execute('SELECT 1 FROM chapter_versions WHERE id=? AND book_id=?', (version, book_id)).fetchone():
            raise StoryError('INVALID_SCOPE', '依赖版本不属于当前作品。')
    event_id = uid('promise_status')
    conn.execute('INSERT INTO lm_promise_status_events VALUES (?,?,?,?,?,?,?,0)',
                 (event_id, promise_id, book_id, branch_id, status, source_chapter, source_version))
    for version in dependencies:
        conn.execute('INSERT OR IGNORE INTO lm_dependencies VALUES (?,?,?,?)',
                     (book_id, branch_id, source_version, version))
    return event_id


def select_promises(conn, book_id, chapter_number, *, trigger_keys=(), explicit_ids=(),
                    role='author', pov_entity_id=None, branch_id='main'):
    _book(conn, book_id, branch_id)
    _chapter_number(chapter_number, 'chapter_number')
    if role not in {'reader', 'author'}:
        raise StoryError('INVALID_SCOPE', '伏笔权限无效。')
    if pov_entity_id:
        pov_entity_id = resolve_entity_id(conn, book_id, pov_entity_id, branch_id=branch_id)
    rows = conn.execute(_INVALID_VERSIONS + '''SELECT p.*, COALESCE((
        SELECT s.status FROM lm_promise_status_events s
        WHERE s.promise_id=p.id AND s.book_id=:book AND s.branch_id=:branch AND s.invalidated=0
        AND s.rowid >= COALESCE((SELECT MAX(a.rowid) FROM lm_promise_status_events a
          WHERE a.promise_id=p.id AND a.book_id=:book AND a.branch_id=:branch
          AND a.source_version IS NULL AND a.invalidated=0),0)
        AND (s.source_version IS NULL OR (s.source_chapter<:chapter AND s.source_version NOT IN (SELECT version_id FROM invalid)))
        ORDER BY COALESCE(s.source_chapter,-1) DESC,s.rowid DESC LIMIT 1),p.status) AS effective_status
      FROM lm_promises p
      WHERE book_id=:book AND branch_id=:branch
      AND ((:role='author' AND :pov='') OR visibility='reader')
      AND ((:role='author' AND :pov='') OR owner_entity_id='' OR COALESCE((SELECT target_id FROM lm_entity_redirects r WHERE r.book_id=p.book_id AND r.branch_id=p.branch_id AND r.source_id=p.owner_entity_id),owner_entity_id)=:pov)
      AND (source_version IS NULL OR (source_chapter<:chapter AND source_version NOT IN (SELECT version_id FROM invalid)))
      ORDER BY due_chapter,id''', dict(book=book_id, branch=branch_id, role=role,
                                     pov=pov_entity_id or '', chapter=chapter_number)).fetchall()
    required, periodic = [], []
    triggers, explicit = set(trigger_keys), set(explicit_ids)
    for row in rows:
        item = dict(row)
        item['status'] = item.pop('effective_status')
        if item['status'] in ('resolved', 'cancelled') and item['id'] not in explicit:
            continue
        due = item['due_chapter']
        reason = ('explicit_reference' if item['id'] in explicit else
                  'due' if item['status'] == 'due' or (due is not None and due <= chapter_number) else
                  'triggered' if triggers.intersection(json.loads(item['triggers'])) else None)
        if reason:
            item['promise_id'] = item.pop('id')
            item['context_reason'] = reason
            item['status'] = 'due' if reason == 'due' else item['status']
            item['data'] = json.loads(item['data'])
            item['triggers'] = json.loads(item['triggers'])
            required.append(item)
        elif due is None:
            periodic.append(item['id'])
    return {'required': required, 'periodic_check_ids': periodic,
            'missing_explicit_ids': sorted(explicit - {item['promise_id'] for item in required})}


def migrate_legacy(conn, book_id, *, branch_id='main'):
    """Local and idempotent: one conservative entity per legacy entity record.

    Textual aliases do not prove identity; unresolved assertions remain legacy.
    No record, quote, or previous version is removed or promoted to verified.
    """
    _book(conn, book_id, branch_id)
    report = {'entities_created': 0, 'unresolved': 0, 'ambiguous_records': 0, 'already_processed': 0}
    rows = conn.execute('SELECT * FROM memories WHERE book_id=? ORDER BY chapter_number,rowid', (book_id,)).fetchall()
    for row in rows:
        if conn.execute('SELECT 1 FROM lm_migrations WHERE book_id=? AND branch_id=? AND legacy_id=?',
                        (book_id, branch_id, row['id'])).fetchone():
            report['already_processed'] += 1
            continue
        entity = None
        if row['kind'] == 'entity':
            data = json.loads(row['data'])
            aliases = [alias.strip() for alias in [row['key'], *(data.get('aliases') or [])]]
            collision = conn.execute('''SELECT 1 FROM lm_aliases WHERE book_id=? AND branch_id=?
                AND alias IN (SELECT value FROM json_each(?)) LIMIT 1''',
                (book_id, branch_id, dumps(aliases))).fetchone()
            if collision:
                # Even an exact matching name is insufficient evidence to merge
                # historical identities automatically.
                report['unresolved'] += 1
                report['ambiguous_records'] += 1
                status, reason = 'legacy_only', 'ambiguous_alias_requires_confirmation'
            else:
                entity = register_entity(conn, book_id, row['key'], entity_type=data.get('entity_type') or 'other', branch_id=branch_id)
                for alias in aliases:
                    register_alias(conn, book_id, entity, alias, branch_id=branch_id)
                report['entities_created'] += 1
                status, reason = 'entity_created', 'conservative_per_source_identity'
        else:
            report['unresolved'] += 1
            status, reason = 'legacy_only', 'requires_explicit_subject_predicate_and_verification'
        conn.execute('INSERT INTO lm_migrations VALUES (?,?,?,?,?,?)',
                     (book_id, branch_id, row['id'], entity, status, reason))
    return report
