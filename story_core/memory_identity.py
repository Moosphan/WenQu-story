"""Source-preserving entity identity and explicit, audited merge decisions."""
import hashlib
import json
from itertools import combinations
from .errors import StoryError
from .storage import dumps

SCHEMA = """
CREATE TABLE IF NOT EXISTS lm_entity_redirects (
 book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE, branch_id TEXT NOT NULL, source_id TEXT NOT NULL REFERENCES lm_entities(id) ON DELETE CASCADE,
 target_id TEXT NOT NULL REFERENCES lm_entities(id) ON DELETE CASCADE, created_revision INTEGER NOT NULL,
 PRIMARY KEY(book_id,branch_id,source_id));
CREATE TABLE IF NOT EXISTS lm_fact_redirects (
 book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE, branch_id TEXT NOT NULL, source_id TEXT NOT NULL REFERENCES lm_facts(id) ON DELETE CASCADE,
 target_id TEXT NOT NULL REFERENCES lm_facts(id) ON DELETE CASCADE, created_revision INTEGER NOT NULL,
 PRIMARY KEY(book_id,branch_id,source_id));
CREATE INDEX IF NOT EXISTS lm_fact_redirect_target ON lm_fact_redirects(book_id,branch_id,target_id);
CREATE TABLE IF NOT EXISTS lm_event_supersessions (
 event_id TEXT PRIMARY KEY REFERENCES lm_fact_events(id) ON DELETE CASCADE, winner_event_id TEXT NOT NULL REFERENCES lm_fact_events(id) ON DELETE CASCADE,
 book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE, branch_id TEXT NOT NULL, created_revision INTEGER NOT NULL);
"""


def _resolve(conn, book, branch, identity, kind):
    seen = set()
    while True:
        if identity in seen:
            raise StoryError('IDENTITY_CYCLE', '实体或事实重定向存在环。')
        seen.add(identity)
        table = 'lm_entities' if kind == 'entity' else 'lm_facts'
        if not conn.execute(f'SELECT 1 FROM {table} WHERE id=? AND book_id=? AND branch_id=?', (identity,book,branch)).fetchone():
            raise StoryError('INVALID_SCOPE', 'ID 不属于当前作品与分支。')
        row = conn.execute(f'SELECT target_id FROM lm_{kind}_redirects WHERE book_id=? AND branch_id=? AND source_id=?', (book,branch,identity)).fetchone()
        if row is None: return identity
        identity = row[0]


def resolve_entity_id(conn, book_id, entity_id, *, branch_id='main'):
    return _resolve(conn,book_id,branch_id,entity_id,'entity')


def resolve_fact_id(conn, book_id, fact_id, *, branch_id='main'):
    return _resolve(conn,book_id,branch_id,fact_id,'fact')


def canonical_value(conn, book, value, branch='main', *, proposed_source=None, proposed_target=None):
    """Only explicitly typed entity references are identity-bearing values."""
    options = {'proposed_source': proposed_source, 'proposed_target': proposed_target}
    if isinstance(value, list): return [canonical_value(conn,book,x,branch, **options) for x in value]
    if not isinstance(value, dict): return value
    result = {k: canonical_value(conn,book,v,branch, **options) for k,v in value.items()}
    if value.get('type') == 'entity' and isinstance(value.get('entity_id'), str):
        result['entity_id'] = resolve_entity_id(conn,book,value['entity_id'],branch_id=branch)
        if result['entity_id'] == proposed_source:
            result['entity_id'] = proposed_target
    return result


def _key(conn, book, branch, row, source=None, target=None):
    subject = resolve_entity_id(conn,book,row['subject_entity_id'],branch_id=branch)
    owner = resolve_entity_id(conn,book,row['owner_entity_id'],branch_id=branch) if row['owner_entity_id'] else ''
    return (target if subject == source else subject,row['predicate'],row['scope'],target if owner == source else owner)


def preview_entity_merge(conn, book_id, source_id, target_id, *, branch_id='main'):
    from . import long_memory as lm
    lm._book(conn,book_id,branch_id)
    source = resolve_entity_id(conn,book_id,source_id,branch_id=branch_id)
    target = resolve_entity_id(conn,book_id,target_id,branch_id=branch_id)
    if source == target: raise StoryError('INVALID_MERGE', '不能合并自身或形成重定向环。')
    if lm._entity(conn,book_id,source,branch_id)['entity_type'] != lm._entity(conn,book_id,target,branch_id)['entity_type']:
        raise StoryError('INVALID_MERGE', '实体类型不同，不能合并。')
    groups, affected = {}, []
    for row in conn.execute('SELECT * FROM lm_facts WHERE book_id=? AND branch_id=?',(book_id,branch_id)):
        old = _key(conn,book_id,branch_id,row)
        if source in (old[0],old[3]) or target in (old[0],old[3]):
            affected.append(row['id'])
            groups.setdefault(_key(conn,book_id,branch_id,row,source,target),[]).append(row['id'])
    conflicts = []
    invalid_versions = {row[0] for row in conn.execute(lm._INVALID_VERSIONS + 'SELECT version_id FROM invalid',
                                                      {'book': book_id, 'branch': branch_id})}
    for ids in groups.values():
        events = conn.execute('''SELECT * FROM lm_fact_events WHERE fact_id IN (SELECT value FROM json_each(?))
            AND invalidated=0 AND verified=1 AND id NOT IN (SELECT event_id FROM lm_event_supersessions)''',(dumps(ids),)).fetchall()
        for a,b in combinations(events,2):
            if a['source_version'] in invalid_versions or b['source_version'] in invalid_versions: continue
            if resolve_fact_id(conn,book_id,a['fact_id'],branch_id=branch_id) == resolve_fact_id(conn,book_id,b['fact_id'],branch_id=branch_id): continue
            values = [canonical_value(conn, book_id, json.loads(event['value']), branch_id,
                proposed_source=source, proposed_target=target) for event in (a, b)]
            if json.dumps(values[0], sort_keys=True) == json.dumps(values[1], sort_keys=True): continue
            if max(a['story_valid_from'],b['story_valid_from']) >= min(a['story_valid_to'] if a['story_valid_to'] is not None else float('inf'),b['story_valid_to'] if b['story_valid_to'] is not None else float('inf')): continue
            pair = sorted([a['id'],b['id']])
            conflicts.append({'id': '|'.join(pair), 'event_ids': pair, 'events': [dict(a),dict(b)], 'resolution_effect': 'losing_event_superseded_entire_interval'})
    return dict(source_id=source,target_id=target,affected_fact_ids=affected,conflicts=conflicts,
                revision=conn.execute('SELECT revision FROM books WHERE id=?',(book_id,)).fetchone()[0])


def merge_entities(conn, book_id, source_id, target_id, *, conflict_resolutions, actor, expected_revision, request_id, branch_id='main'):
    from . import memory_workflow as wf
    fingerprint = wf._fingerprint('merge_entities',[source_id,target_id,conflict_resolutions,actor,expected_revision])
    replay = wf._replay(conn,book_id,branch_id,request_id,fingerprint)
    if replay is not None: return replay
    wf._text(actor,'actor',200)
    wf._revision(conn,book_id,expected_revision)
    preview = preview_entity_merge(conn,book_id,source_id,target_id,branch_id=branch_id)
    conflicts = preview['conflicts']
    if not isinstance(conflict_resolutions,dict) or set(conflict_resolutions) != {c['id'] for c in conflicts}:
        raise StoryError('MERGE_CONFLICT', '必须逐项解决全部事件冲突。')
    losers, winners = {}, set()
    for conflict in conflicts:
        winner = conflict_resolutions[conflict['id']]
        if winner not in conflict['event_ids']: raise StoryError('MERGE_CONFLICT','冲突决策必须指定该项事件 ID。')
        winners.add(winner)
        losers[next(e for e in conflict['event_ids'] if e != winner)] = winner
    if winners.intersection(losers): raise StoryError('MERGE_CONFLICT','冲突决策不一致：获选事件同时被否决。')
    revision = expected_revision + 1
    source,target = preview['source_id'],preview['target_id']
    with wf._atomic(conn):
        for loser,winner in losers.items():
            conn.execute('INSERT INTO lm_event_supersessions VALUES (?,?,?,?,?)',(loser,winner,book_id,branch_id,revision))
        conn.execute('INSERT INTO lm_entity_redirects VALUES (?,?,?,?,?)',(book_id,branch_id,source,target,revision))
        conn.execute('UPDATE lm_entity_redirects SET target_id=? WHERE book_id=? AND branch_id=? AND target_id=?',(target,book_id,branch_id,source))
        for row in conn.execute('SELECT * FROM lm_facts WHERE book_id=? AND branch_id=?',(book_id,branch_id)).fetchall():
            key = _key(conn,book_id,branch_id,row)
            fid = 'fact_' + hashlib.sha256(dumps([book_id,branch_id,*key]).encode()).hexdigest()[:32]
            conn.execute('INSERT OR IGNORE INTO lm_facts VALUES (?,?,?,?,?,?,?)',(fid,book_id,branch_id,*key))
            if fid != row['id']:
                conn.execute('INSERT INTO lm_fact_redirects VALUES (?,?,?,?,?) ON CONFLICT(book_id,branch_id,source_id) DO UPDATE SET target_id=excluded.target_id',(book_id,branch_id,row['id'],fid,revision))
        conn.execute('UPDATE books SET revision=? WHERE id=?',(revision,book_id))
        result = dict(source_id=source,target_id=target,revision=revision,canonical_changed=True,superseded_event_ids=sorted(losers))
        wf._audit(conn,book_id,branch_id,request_id,'merge_entities',actor,fingerprint,result)
    return result
