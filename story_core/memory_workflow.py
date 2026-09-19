"""Author-reviewed memory proposals and bounded, local-only maintenance.

All mutations use the caller's SQLite transaction (BEGIN IMMEDIATE recommended).
No prose inference, model calls, automatic trust promotion, or source deletion.
"""
import hashlib
import json
import math
import time
from contextlib import contextmanager

from . import long_memory as lm
from .errors import StoryError
from .storage import dumps, uid

SCHEMA = """
CREATE TABLE IF NOT EXISTS lm_predicate_aliases (
 book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE, branch_id TEXT NOT NULL,
 alias TEXT NOT NULL, predicate TEXT NOT NULL, PRIMARY KEY(book_id,branch_id,alias));
CREATE TABLE IF NOT EXISTS lm_proposals (
 id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
 branch_id TEXT NOT NULL, status TEXT NOT NULL, origin TEXT NOT NULL,
 candidate TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', fact_id TEXT,
 created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS lm_proposal_review ON lm_proposals(book_id,branch_id,status,created_at);
CREATE TABLE IF NOT EXISTS lm_workflow_audit (
 id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
 branch_id TEXT NOT NULL, request_id TEXT NOT NULL, action TEXT NOT NULL,
 actor TEXT NOT NULL, fingerprint TEXT NOT NULL, result TEXT NOT NULL, created_at REAL NOT NULL,
 UNIQUE(book_id,branch_id,request_id));
CREATE TABLE IF NOT EXISTS lm_maintenance_jobs (
 id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
 branch_id TEXT NOT NULL, source_version TEXT NOT NULL, source_chapter INTEGER NOT NULL,
 status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
 needs_verification INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '',
 created_at REAL NOT NULL, updated_at REAL NOT NULL,
 UNIQUE(book_id,branch_id,source_version));
CREATE INDEX IF NOT EXISTS lm_maintenance_pending ON lm_maintenance_jobs(book_id,branch_id,status);
CREATE TABLE IF NOT EXISTS lm_source_digests (
 book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE, branch_id TEXT NOT NULL,
 source_version TEXT NOT NULL, source_chapter INTEGER NOT NULL,
 source_hash TEXT NOT NULL, digest TEXT NOT NULL, status TEXT NOT NULL,
 updated_at REAL NOT NULL, PRIMARY KEY(book_id,branch_id,source_version));
"""

# Explicit equivalences only. A custom registry may extend, never redefine these.
PREDICATE_ALIASES = {
    'location': 'location', '所在位置': 'location', '位置': 'location',
    'status': 'status', '状态': 'status', 'injury': 'injury', '伤势': 'injury',
    'possession': 'possession', '持有物': 'possession', 'relationship': 'relationship', '关系': 'relationship',
}
MAX_BATCH = 100
MAX_CANDIDATE_BYTES = 16384
MAX_FIELD_CHARS = 4096
MAX_JOB_ATTEMPTS = 3


@contextmanager
def _atomic(conn):
    name = uid('memory_savepoint')
    conn.execute('SAVEPOINT ' + name)
    try:
        yield
        conn.execute('RELEASE ' + name)
    except BaseException:
        conn.execute('ROLLBACK TO ' + name)
        conn.execute('RELEASE ' + name)
        raise


def _text(value, name, limit=MAX_FIELD_CHARS):
    value = lm._text(value, name)
    if len(value) > limit:
        raise StoryError('INVALID_REQUEST', f'{name} 超过长度上限。')
    return value


def _json(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        raise StoryError('INVALID_REQUEST', '候选必须为有限 JSON 数据。') from None


def _fingerprint(action, data):
    return hashlib.sha256(_json([action, data]).encode()).hexdigest()


def _replay(conn, book_id, branch_id, request_id, fingerprint):
    _text(request_id, 'request_id', 200)
    row = conn.execute('SELECT * FROM lm_workflow_audit WHERE book_id=? AND branch_id=? AND request_id=?',
                       (book_id, branch_id, request_id)).fetchone()
    if row:
        if row['fingerprint'] != fingerprint:
            raise StoryError('IDEMPOTENCY_CONFLICT', '同一请求 ID 的内容已变化。')
        return json.loads(row['result'])
    return None


def _audit(conn, book, branch, request, action, actor, fingerprint, result):
    conn.execute('INSERT INTO lm_workflow_audit VALUES (?,?,?,?,?,?,?,?,?)',
                 (uid('audit'), book, branch, request, action, _text(actor, 'actor', 200), fingerprint, dumps(result), time.time()))


def _revision(conn, book, expected):
    row = conn.execute('SELECT revision FROM books WHERE id=?', (book,)).fetchone()
    if type(expected) is not int or expected < 0 or row['revision'] != expected:
        raise StoryError('STALE_REVISION', '作品已变化，请刷新后核实。')
    return expected


def _current_source(conn, book, candidate):
    body = lm._source(conn, book, candidate['source_chapter'], candidate['source_version'])
    active = conn.execute("SELECT 1 FROM chapters WHERE book_id=? AND number=? AND version_id=? AND status='committed'",
                          (book, candidate['source_chapter'], candidate['source_version'])).fetchone()
    if not active:
        raise StoryError('STALE_SOURCE', '来源章节已修改或未提交，请重新核实。')
    return body


def register_predicate(conn, book_id, predicate, *, aliases=(), actor, branch_id='main'):
    lm._book(conn, book_id, branch_id)
    predicate = _text(predicate, 'predicate', 100)
    if not isinstance(aliases, (list, tuple)) or len(aliases) > 20:
        raise StoryError('INVALID_REQUEST', '属性别名最多 20 项。')
    aliases = list(dict.fromkeys([predicate, *[_text(x, 'alias', 100) for x in aliases]]))
    actor = _text(actor, 'actor', 200)
    with _atomic(conn):
        for alias in aliases:
            existing = PREDICATE_ALIASES.get(alias)
            row = conn.execute('SELECT predicate FROM lm_predicate_aliases WHERE book_id=? AND branch_id=? AND alias=?',
                               (book_id, branch_id, alias)).fetchone()
            if (existing and existing != predicate) or (row and row[0] != predicate):
                raise StoryError('PREDICATE_CONFLICT', '属性别名已绑定其他属性。')
            conn.execute('INSERT OR IGNORE INTO lm_predicate_aliases VALUES (?,?,?,?)',
                         (book_id, branch_id, alias, predicate))
        result = {'predicate': predicate, 'aliases': aliases}
        _audit(conn, book_id, branch_id, uid('register'), 'register_predicate', actor,
               _fingerprint('register_predicate', result), result)
    return result


def _normalize(conn, book, branch, candidate):
    if not isinstance(candidate, dict):
        raise StoryError('INVALID_REQUEST', '候选必须为对象。')
    if len(_json(candidate).encode()) > MAX_CANDIDATE_BYTES:
        raise StoryError('INVALID_REQUEST', '候选超过大小上限。')
    allowed = {'subject_entity_id', 'subject_alias', 'predicate', 'value', 'source_chapter', 'source_version',
               'evidence', 'story_valid_from', 'story_valid_to', 'known_from_chapter', 'revealed_from_chapter',
               'scope', 'owner_entity_id', 'visibility', 'dependency_versions', 'legacy_id'}
    if set(candidate) - allowed:
        raise StoryError('INVALID_REQUEST', '候选包含不支持的字段。')
    data = dict(candidate)
    if 'value' in data:
        data['value'] = lm.canonical_value(conn, book, data['value'], branch)
    for field in ('subject_entity_id', 'subject_alias', 'owner_entity_id', 'scope', 'visibility', 'legacy_id'):
        if field in data and data[field] is not None:
            data[field] = _text(data[field], field, 200)
    for field in ('predicate', 'source_version', 'evidence'):
        data[field] = _text(data.get(field), field, 4096 if field == 'evidence' else 200)
    alias = data['predicate']
    predicate = PREDICATE_ALIASES.get(alias)
    if predicate is None:
        row = conn.execute('SELECT predicate FROM lm_predicate_aliases WHERE book_id=? AND branch_id=? AND alias=?',
                           (book, branch, alias)).fetchone()
        predicate = row[0] if row else None
    if predicate is None:
        raise StoryError('UNKNOWN_PREDICATE', '请先显式注册该属性及其别名。')
    data['predicate'] = predicate
    if 'value' not in data or len(_json(data['value'])) > MAX_FIELD_CHARS:
        raise StoryError('INVALID_REQUEST', '属性值缺失或过长。')
    for name in ('story_valid_from', 'story_valid_to'):
        instant = data.get(name)
        if (name == 'story_valid_from' and instant is None) or (instant is not None and
                (type(instant) not in (int, float) or not math.isfinite(instant))):
            raise StoryError('INVALID_REQUEST', '必须显式提供有限故事时间。')
    if data.get('story_valid_to') is not None and data['story_valid_to'] <= data['story_valid_from']:
        raise StoryError('INVALID_REQUEST', '故事时间区间无效。')
    if type(data.get('source_chapter')) is not int or data['source_chapter'] < 1:
        raise StoryError('INVALID_SCOPE', '来源章节无效。')
    body = _current_source(conn, book, data)
    from .memory import exact_evidence
    quote = exact_evidence(body, data['evidence'])
    if quote is None:
        raise StoryError('INVALID_EVIDENCE', '事实证据不在来源正文中。')
    data['evidence'] = quote
    subject = data.get('subject_entity_id')
    if subject:
        subject = lm.resolve_entity_id(conn, book, _text(subject, 'subject_entity_id', 200), branch_id=branch)
    else:
        data['subject_alias'] = _text(data.get('subject_alias'), 'subject_alias', 200)
        try:
            subject = lm.resolve_alias(conn, book, data['subject_alias'], branch_id=branch)
        except StoryError as error:
            if error.code != 'AMBIGUOUS_ENTITY':
                raise
    data['subject_entity_id'] = subject
    scope, owner = data.setdefault('scope', 'objective'), data.get('owner_entity_id')
    if scope not in {'objective', 'character_belief', 'author_plan'} or data.setdefault('visibility', 'reader') not in {'reader', 'author'}:
        raise StoryError('INVALID_SCOPE', '事实范围无效。')
    if (scope == 'character_belief') != bool(owner):
        raise StoryError('INVALID_SCOPE', '角色信念必须指定所属实体，其他范围不可指定。')
    if owner:
        data['owner_entity_id'] = lm.resolve_entity_id(conn, book, owner, branch_id=branch)
    for name in ('known_from_chapter', 'revealed_from_chapter'):
        if data.get(name) is not None:
            lm._chapter_number(data[name], name)
    deps = data.get('dependency_versions', [])
    if not isinstance(deps, (list, tuple)) or len(deps) > 100:
        raise StoryError('INVALID_REQUEST', '依赖版本最多 100 项。')
    for dependency in deps:
        if not isinstance(dependency, str) or not conn.execute('SELECT 1 FROM chapter_versions WHERE id=? AND book_id=?', (dependency, book)).fetchone():
            raise StoryError('INVALID_SCOPE', '依赖不属于当前作品。')
    return data


def submit_proposals(conn, book_id, candidates, *, request_id, origin='model', branch_id='main'):
    """Quarantine invalid suggestions individually; never change manuscript or trust."""
    lm._book(conn, book_id, branch_id)
    if not isinstance(candidates, list) or not 1 <= len(candidates) <= MAX_BATCH:
        raise StoryError('INVALID_REQUEST', '每批候选必须为 1 至 100 项。')
    origin = _text(origin, 'origin', 100)
    # Bound retained raw data too. Oversize batches are envelope errors, not prose rejection.
    if len(_json(candidates).encode()) > MAX_BATCH * MAX_CANDIDATE_BYTES:
        raise StoryError('INVALID_REQUEST', '候选批次超过大小上限。')
    fingerprint = _fingerprint('submit', [origin, candidates])
    previous = _replay(conn, book_id, branch_id, request_id, fingerprint)
    if previous is not None:
        return previous
    result = {'proposals': [], 'quarantined': []}
    with _atomic(conn):
        for candidate in candidates:
            proposal_id = uid('proposal')
            try:
                data = _normalize(conn, book_id, branch_id, candidate)
                status, reason = 'pending', ''
            except StoryError as error:
                status, reason = 'quarantined', error.code
                raw = _json(candidate)
                # A malformed giant candidate gets a bounded diagnostic and content hash.
                data = candidate if len(raw.encode()) <= MAX_CANDIDATE_BYTES else {
                    'truncated': True, 'sha256': hashlib.sha256(raw.encode()).hexdigest(),
                    'source_version': candidate.get('source_version') if isinstance(candidate, dict) and isinstance(candidate.get('source_version'), str) and len(candidate['source_version']) <= 200 else None}
            conn.execute('INSERT INTO lm_proposals VALUES (?,?,?,?,?,?,?,?,?)',
                         (proposal_id, book_id, branch_id, status, origin, dumps(data), reason, None, time.time()))
            item = {'id': proposal_id, 'status': status, 'reason': reason}
            result['proposals' if status == 'pending' else 'quarantined'].append(item)
        _audit(conn, book_id, branch_id, request_id, 'submit', origin, fingerprint, result)
    return result


def submit_legacy_proposals(conn, book_id, bindings, *, request_id, branch_id='main'):
    """Copy archival evidence only; callers supply explicit identity/predicate/time.

    Bindings cannot replace legacy value, evidence, source or visibility. Invalid
    mappings join ordinary quarantine; old memory and all source text remain.
    """
    lm._book(conn, book_id, branch_id)
    if not isinstance(bindings, list) or not 1 <= len(bindings) <= MAX_BATCH:
        raise StoryError('INVALID_REQUEST', '每批旧记忆映射必须为 1 至 100 项。')
    candidates = []
    mapping_keys = {'legacy_id', 'subject_entity_id', 'subject_alias', 'predicate', 'story_valid_from',
                    'story_valid_to', 'scope', 'owner_entity_id', 'known_from_chapter',
                    'revealed_from_chapter', 'dependency_versions'}
    for binding in bindings:
        if not isinstance(binding, dict) or set(binding) - mapping_keys or not isinstance(binding.get('legacy_id'), str):
            candidates.append({'invalid_legacy_mapping': binding})
            continue
        row = conn.execute('SELECT * FROM memories WHERE id=? AND book_id=?', (binding['legacy_id'], book_id)).fetchone()
        if row is None:
            candidates.append({'legacy_id': binding['legacy_id'], 'invalid_legacy_mapping': 'not_found'})
            continue
        data = dict(binding)
        data.update(value=row['value'], evidence=row['evidence'], source_chapter=row['chapter_number'],
                    source_version=row['version_id'], visibility=row['visibility'])
        candidates.append(data)
    return submit_proposals(conn, book_id, candidates, request_id=request_id, origin='legacy', branch_id=branch_id)


def _proposal(conn, book, branch, proposal_id):
    lm._book(conn, book, branch)
    row = conn.execute('SELECT * FROM lm_proposals WHERE id=? AND book_id=? AND branch_id=?',
                       (proposal_id, book, branch)).fetchone()
    if not row:
        raise StoryError('NOT_FOUND', '找不到该核实候选。')
    item = dict(row)
    item['candidate'] = json.loads(item['candidate'])
    return item


def preview_proposal(conn, book_id, proposal_id, *, branch_id='main'):
    item = _proposal(conn, book_id, branch_id, proposal_id)
    data = item['candidate']
    item.update(conflicts=[], identity_candidates=[], source_current=False)
    if not isinstance(data, dict) or item['status'] == 'quarantined':
        return item
    try:
        _current_source(conn, book_id, data)
        item['source_current'] = True
    except StoryError:
        pass
    for field in ('subject_entity_id', 'owner_entity_id'):
        if data.get(field):
            data[field] = lm.resolve_entity_id(conn, book_id, data[field], branch_id=branch_id)
    data['value'] = lm.canonical_value(conn, book_id, data['value'], branch_id)
    subject = data.get('subject_entity_id')
    if not subject:
        item['identity_candidates'] = [dict(r) for r in conn.execute('''SELECT e.id,e.display_name,e.entity_type FROM lm_entities e
          JOIN lm_aliases a ON a.entity_id=e.id WHERE a.book_id=? AND a.branch_id=? AND a.alias=? ORDER BY e.id LIMIT 100''',
          (book_id, branch_id, data.get('subject_alias', '')))]
        candidates = {}
        for candidate in item['identity_candidates']:
            eid = lm.resolve_entity_id(conn, book_id, candidate['id'], branch_id=branch_id)
            candidates[eid] = dict(lm._entity(conn, book_id, eid, branch_id))
        item['identity_candidates'] = list(candidates.values())
    else:
        scope = data.get('scope', 'objective')
        # Author review sees all scopes/visibility. POV-facing projections deliberately
        # hide author-only beliefs and therefore cannot serve as conflict detection.
        row = conn.execute(lm._INVALID_VERSIONS + """SELECT e.*,f.subject_entity_id,f.predicate,f.scope,f.owner_entity_id
          FROM lm_fact_events e LEFT JOIN lm_fact_redirects r ON r.source_id=e.fact_id AND r.book_id=e.book_id AND r.branch_id=e.branch_id
          JOIN lm_facts f ON f.id=COALESCE(r.target_id,e.fact_id)
          WHERE e.book_id=:book AND e.branch_id=:branch AND e.verified=1 AND e.invalidated=0
          AND e.id NOT IN (SELECT event_id FROM lm_event_supersessions)
          AND e.source_version NOT IN (SELECT version_id FROM invalid)
          AND f.subject_entity_id=:subject AND f.predicate=:predicate AND f.scope=:scope AND f.owner_entity_id=:owner
          AND e.story_valid_from<=:instant AND (e.story_valid_to IS NULL OR :instant<e.story_valid_to)
          ORDER BY e.story_valid_from DESC,e.source_chapter DESC,e.rowid DESC LIMIT 1""",
          {'book': book_id, 'branch': branch_id, 'subject': subject, 'predicate': data['predicate'],
           'scope': scope, 'owner': data.get('owner_entity_id') or '', 'instant': data['story_valid_from']}).fetchone()
        if row and json.loads(row['value']) != data['value']:
            conflict = dict(row)
            conflict['value'] = json.loads(conflict['value'])
            item['conflicts'] = [conflict]
    return item


def _page(limit, offset, max_limit=100):
    if type(limit) is not int or not 1 <= limit <= max_limit or type(offset) is not int or offset < 0:
        raise StoryError('INVALID_REQUEST', f'分页大小必须为 1 至 {max_limit}，偏移非负。')


def list_proposals(conn, book_id, *, status=None, limit=50, offset=0, branch_id='main'):
    lm._book(conn, book_id, branch_id)
    _page(limit, offset)
    if status is not None and status not in {'pending', 'accepted', 'rejected', 'quarantined'}:
        raise StoryError('INVALID_REQUEST', '候选状态无效。')
    counts = {row[0]: row[1] for row in conn.execute('SELECT status,count(*) FROM lm_proposals WHERE book_id=? AND branch_id=? GROUP BY status', (book_id, branch_id))}
    rows = conn.execute('''SELECT * FROM lm_proposals WHERE book_id=? AND branch_id=? AND (? IS NULL OR status=?)
      ORDER BY created_at,id LIMIT ? OFFSET ?''', (book_id, branch_id, status, status, limit, offset)).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item['candidate'] = json.loads(item['candidate'])
        items.append(item)
    return {'items': items, 'counts': counts, 'total': counts.get(status, 0) if status else sum(counts.values()), 'limit': limit, 'offset': offset}


def bind_proposal(conn, book_id, proposal_id, entity_id, *, actor, expected_revision, request_id, branch_id='main'):
    """Explicit per-proposal identity binding; never merge historical identities."""
    lm._book(conn, book_id, branch_id)
    actor = _text(actor, 'actor', 200)
    fingerprint = _fingerprint('bind', [proposal_id, entity_id, actor, expected_revision])
    previous = _replay(conn, book_id, branch_id, request_id, fingerprint)
    if previous is not None:
        return previous
    _revision(conn, book_id, expected_revision)
    item = _proposal(conn, book_id, branch_id, proposal_id)
    if item['status'] != 'pending':
        raise StoryError('PROPOSAL_DECIDED', '候选已处理或隔离。')
    _current_source(conn, book_id, item['candidate'])
    entity_id = lm.resolve_entity_id(conn, book_id, entity_id, branch_id=branch_id)
    data = item['candidate']
    data['subject_entity_id'] = entity_id
    result = {'proposal_id': proposal_id, 'entity_id': entity_id, 'revision': expected_revision + 1, 'canonical_changed': False}
    with _atomic(conn):
        conn.execute('UPDATE lm_proposals SET candidate=? WHERE id=? AND status=?', (dumps(data), proposal_id, 'pending'))
        conn.execute('UPDATE books SET revision=revision+1 WHERE id=? AND revision=?', (book_id, expected_revision))
        _audit(conn, book_id, branch_id, request_id, 'bind', actor, fingerprint, result)
    return result


def decide_proposal(conn, book_id, proposal_id, *, decision, actor, expected_revision, request_id,
                    trust=False, allow_conflict=False, branch_id='main'):
    lm._book(conn, book_id, branch_id)
    actor = _text(actor, 'actor', 200)
    if decision not in {'accept', 'reject'} or type(trust) is not bool or type(allow_conflict) is not bool:
        raise StoryError('INVALID_REQUEST', '核实决定必须显式提供。')
    fingerprint = _fingerprint('decide', [proposal_id, decision, actor, expected_revision, trust, allow_conflict])
    previous = _replay(conn, book_id, branch_id, request_id, fingerprint)
    if previous is not None:
        return previous
    _revision(conn, book_id, expected_revision)
    item = preview_proposal(conn, book_id, proposal_id, branch_id=branch_id)
    if item['status'] != 'pending':
        raise StoryError('PROPOSAL_DECIDED', '候选已处理或隔离。')
    data = item['candidate']
    fact_id = None
    if decision == 'accept':
        _current_source(conn, book_id, data)
        if trust is not True:
            raise StoryError('EXPLICIT_TRUST_REQUIRED', '接受事实必须显式核实来源与含义。')
        if not data.get('subject_entity_id'):
            raise StoryError('AMBIGUOUS_ENTITY', '请先显式绑定候选实体 ID。')
        if item['conflicts'] and not allow_conflict:
            raise StoryError('FACT_CONFLICT', '候选与当前状态冲突，请显式确认覆盖。', {'conflicts': item['conflicts']})
        invalid = {r[0] for r in conn.execute(lm._INVALID_VERSIONS + 'SELECT version_id FROM invalid', {'book': book_id, 'branch': branch_id})}
        if data['source_version'] in invalid or invalid.intersection(data.get('dependency_versions', [])):
            raise StoryError('STALE_DEPENDENCY', '来源依赖已失效，需先重新核实依赖。')
    with _atomic(conn):
        if decision == 'accept':
            options = {key: value for key, value in data.items() if key not in {'subject_alias', 'legacy_id'}}
            fact_id = lm.record_fact(conn, book_id, **options, verified=True, branch_id=branch_id, recorded_revision=expected_revision + 1)
            conn.execute('UPDATE books SET revision=revision+1 WHERE id=? AND revision=?', (book_id, expected_revision))
        status = 'accepted' if decision == 'accept' else 'rejected'
        conn.execute('UPDATE lm_proposals SET status=?,fact_id=? WHERE id=? AND status=?', (status, fact_id, proposal_id, 'pending'))
        result = {'proposal_id': proposal_id, 'status': status, 'fact_id': fact_id,
                  'revision': expected_revision + int(decision == 'accept'), 'canonical_changed': decision == 'accept'}
        _audit(conn, book_id, branch_id, request_id, 'decide', actor, fingerprint, result)
    return result


def enqueue_maintenance(conn, book_id, version_id, *, branch_id='main', invalidated=False):
    """Invalidate transitive projections and schedule only their local source views."""
    lm._book(conn, book_id, branch_id)
    if type(invalidated) is not bool:
        raise StoryError('INVALID_REQUEST', 'invalidated 必须为布尔值。')
    rows = conn.execute('''WITH RECURSIVE affected(id) AS (
      SELECT id FROM chapter_versions WHERE book_id=? AND id=?
      UNION SELECT d.source_version FROM lm_dependencies d JOIN affected a ON d.depends_on_version=a.id
      WHERE d.book_id=? AND d.branch_id=? AND ?=1)
      SELECT v.id,v.number FROM chapter_versions v JOIN affected a ON a.id=v.id WHERE v.book_id=? ORDER BY v.number,v.id''',
      (book_id, version_id, book_id, branch_id, int(invalidated), book_id)).fetchall()
    if not rows:
        raise StoryError('INVALID_SCOPE', '来源版本不属于当前作品。')
    jobs = []
    with _atomic(conn):
        if invalidated:
            lm.invalidate_version(conn, book_id, version_id, branch_id=branch_id)
        for row in rows:
            key = 'maintenance_' + hashlib.sha256(dumps([book_id, branch_id, row['id']]).encode()).hexdigest()[:32]
            now = time.time()
            conn.execute('''INSERT INTO lm_maintenance_jobs VALUES (?,?,?,?,?,'pending',0,?,'',?,?)
               ON CONFLICT(book_id,branch_id,source_version) DO UPDATE SET
               status=CASE WHEN lm_maintenance_jobs.attempts>=3 THEN 'failed' WHEN excluded.needs_verification=1 AND lm_maintenance_jobs.needs_verification=0 THEN 'pending' ELSE lm_maintenance_jobs.status END,
               needs_verification=MAX(lm_maintenance_jobs.needs_verification,excluded.needs_verification),updated_at=excluded.updated_at''',
              (key, book_id, branch_id, row['id'], row['number'], int(invalidated), now, now))
            if invalidated:
                conn.execute("UPDATE lm_source_digests SET status='needs_verification' WHERE book_id=? AND branch_id=? AND source_version=?",
                             (book_id, branch_id, row['id']))
            jobs.append(key)
    return {'job_ids': jobs}


def _build_digest(conn, book, branch, source):
    """A bounded source excerpt/index, explicitly not an inferred factual summary."""
    body = source['body']
    return dumps({'kind': 'source_index', 'source_version': source['id'], 'source_chapter': source['number'],
                  'characters': len(body), 'head': body[:128], 'tail': body[-64:] if len(body) > 128 else '',
                  'evidence_count': conn.execute('SELECT count(*) FROM lm_fact_events WHERE book_id=? AND branch_id=? AND source_version=?',
                                                (book, branch, source['id'])).fetchone()[0]})


def run_maintenance_step(conn, book_id, *, branch_id='main', limit=5):
    """At most 20 jobs, three attempts each; transaction rollback safely resumes.

    Savepoint per job keeps errors durable without partial digest writes. Process
    death rolls back this whole local step; no external side effects need replay.
    """
    lm._book(conn, book_id, branch_id)
    _page(limit, 0, 20)
    rows = conn.execute("SELECT * FROM lm_maintenance_jobs WHERE book_id=? AND branch_id=? AND status='pending' AND attempts<? ORDER BY created_at,id LIMIT ?",
                        (book_id, branch_id, MAX_JOB_ATTEMPTS, limit)).fetchall()
    result = {'processed': 0, 'ready': 0, 'needs_verification': 0, 'superseded': 0, 'failed': 0}
    for job in rows:
        attempt = job['attempts'] + 1
        conn.execute('UPDATE lm_maintenance_jobs SET attempts=?,updated_at=? WHERE id=?', (attempt, time.time(), job['id']))
        result['processed'] += 1
        try:
            with _atomic(conn):
                source = conn.execute('SELECT * FROM chapter_versions WHERE book_id=? AND id=? AND number=?',
                                      (book_id, job['source_version'], job['source_chapter'])).fetchone()
                active = conn.execute("SELECT 1 FROM chapters WHERE book_id=? AND number=? AND version_id=? AND status='committed'",
                                      (book_id, job['source_chapter'], job['source_version'])).fetchone()
                if not source or not active:
                    conn.execute("UPDATE lm_source_digests SET status='stale',updated_at=? WHERE book_id=? AND branch_id=? AND source_version=?",
                                 (time.time(), book_id, branch_id, job['source_version']))
                    status = 'superseded'
                else:
                    digest = _build_digest(conn, book_id, branch_id, source)
                    # Legacy evidence may predate the inverted index. Publish both
                    # derived views atomically; indexing failures retain retry state.
                    from .retrieval import index_version
                    index_version(conn, book_id, source['id'])
                    # Recheck the pointer after work too (source compare-and-swap).
                    current = conn.execute("SELECT version_id FROM chapters WHERE book_id=? AND number=? AND status='committed'",
                                           (book_id, job['source_chapter'])).fetchone()
                    if current is None or current[0] != source['id']:
                        raise StoryError('STALE_SOURCE', '整理期间来源版本已变化。')
                    invalid_versions = {r[0] for r in conn.execute(lm._INVALID_VERSIONS + 'SELECT version_id FROM invalid', {'book': book_id, 'branch': branch_id})}
                    status = 'needs_verification' if job['needs_verification'] or source['id'] in invalid_versions else 'ready'
                    conn.execute('''INSERT INTO lm_source_digests VALUES (?,?,?,?,?,?,?,?)
                      ON CONFLICT(book_id,branch_id,source_version) DO UPDATE SET source_hash=excluded.source_hash,
                      digest=excluded.digest,status=excluded.status,updated_at=excluded.updated_at''',
                      (book_id, branch_id, source['id'], source['number'], hashlib.sha256(source['body'].encode()).hexdigest(), digest, status, time.time()))
                conn.execute("UPDATE lm_maintenance_jobs SET status=?,error='',updated_at=? WHERE id=?", (status, time.time(), job['id']))
                result[status] += 1
        except Exception as error:
            status = 'failed' if attempt >= MAX_JOB_ATTEMPTS else 'pending'
            # Persist bounded codes, not arbitrary exception messages containing source text.
            code = error.code if isinstance(error, StoryError) else type(error).__name__
            conn.execute('UPDATE lm_maintenance_jobs SET status=?,error=?,updated_at=? WHERE id=?',
                         (status, str(code)[:100], time.time(), job['id']))
            result['failed'] += 1
    return result


def maintenance_status(conn, book_id, *, branch_id='main', limit=50, offset=0):
    lm._book(conn, book_id, branch_id)
    _page(limit, offset)
    counts = {row[0]: row[1] for row in conn.execute('SELECT status,count(*) FROM lm_maintenance_jobs WHERE book_id=? AND branch_id=? GROUP BY status', (book_id, branch_id))}
    items = [dict(row) for row in conn.execute('SELECT * FROM lm_maintenance_jobs WHERE book_id=? AND branch_id=? ORDER BY created_at,id LIMIT ? OFFSET ?',
                                            (book_id, branch_id, limit, offset))]
    return {'items': items, 'counts': counts, 'total': sum(counts.values()), 'limit': limit, 'offset': offset}


def source_digest_view(conn, book_id, *, through_chapter, branch_id='main', recent_count=5, arc_count=3, arc_span=10):
    """Bounded author-only index views from original source digests, not summaries.

    These are navigational excerpts, never trusted facts or a completeness claim.
    A later context adapter must apply its reader/POV policy before using them.
    """
    lm._book(conn, book_id, branch_id)
    lm._chapter_number(through_chapter, 'through_chapter', allow_zero=True)
    _page(recent_count, 0, 5)
    _page(arc_count, 0, 3)
    _page(arc_span, 0, 100)
    invalid_versions = {r[0] for r in conn.execute(lm._INVALID_VERSIONS + 'SELECT version_id FROM invalid',
                                                  {'book': book_id, 'branch': branch_id})}
    def read_sources(start, end, limit, descending=False):
        order = 'DESC' if descending else 'ASC'
        rows = conn.execute(f"""SELECT d.* FROM lm_source_digests d JOIN chapters c
          ON c.book_id=d.book_id AND c.number=d.source_chapter AND c.version_id=d.source_version
          WHERE d.book_id=? AND d.branch_id=? AND c.status='committed' AND d.status IN ('ready','needs_verification')
          AND d.source_chapter BETWEEN ? AND ? ORDER BY d.source_chapter {order} LIMIT ?""",
          (book_id, branch_id, start, end, limit)).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item['digest'] = json.loads(item['digest'])
            if item['source_version'] in invalid_versions:
                item['status'] = 'needs_verification'
            items.append(item)
        return items
    recent = read_sources(1, through_chapter, recent_count, True)
    arcs = []
    end = through_chapter - recent_count
    for _ in range(arc_count):
        if end < 1:
            break
        start = max(1, end - arc_span + 1)
        midpoint = (start + end) // 2
        # At most three source anchors, each read directly from its own chapter.
        sources = {item['source_version']: item for item in (
            read_sources(start, end, 1) + read_sources(midpoint, end, 1) + read_sources(start, end, 1, True))}
        arcs.append({'start_chapter': start, 'end_chapter': end, 'sources': list(sources.values())})
        end = start - 1
    return {'recent': recent, 'arcs': arcs, 'visibility': 'author', 'kind': 'source_index_view'}
