"""Author-verified, whole-source semantic repairs; never approve manuscript text."""
import time
from . import long_memory as lm, memory_workflow as wf
from .errors import StoryError
from .storage import dumps, uid

SCHEMA = """
CREATE TABLE IF NOT EXISTS lm_repair_cases (
 book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE, branch_id TEXT NOT NULL,
 source_version TEXT NOT NULL, status TEXT NOT NULL, updated_at REAL NOT NULL,
 PRIMARY KEY(book_id,branch_id,source_version));
CREATE TABLE IF NOT EXISTS lm_repair_actions (
 id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
 branch_id TEXT NOT NULL, source_version TEXT NOT NULL, actor TEXT NOT NULL,
 revision INTEGER NOT NULL, manifest TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS lm_retired_events (
 event_id TEXT PRIMARY KEY, event_kind TEXT NOT NULL,
 repair_id TEXT NOT NULL REFERENCES lm_repair_actions(id) ON DELETE CASCADE,
 replacement_event_id TEXT);
"""


def open_case(conn, book, branch, version):
    conn.execute('''INSERT INTO lm_repair_cases VALUES (?,?,?,'open',?)
        ON CONFLICT(book_id,branch_id,source_version) DO UPDATE SET status='open',updated_at=excluded.updated_at''',
        (book,branch,version,time.time()))


def _invalid(conn, book, branch):
    return {r[0] for r in conn.execute(lm._INVALID_VERSIONS+'SELECT version_id FROM invalid', {'book':book,'branch':branch})}


def preview_repair(conn, book_id, source_version, *, branch_id='main'):
    lm._book(conn,book_id,branch_id)
    source = conn.execute('SELECT number FROM chapter_versions WHERE id=? AND book_id=?',(source_version,book_id)).fetchone()
    if not source: raise StoryError('INVALID_SCOPE','来源不属于当前作品。')
    invalid = _invalid(conn,book_id,branch_id)
    events = []
    for kind,table in [('fact','lm_fact_events'),('status','lm_promise_status_events')]:
        for row in conn.execute(f'''SELECT * FROM {table} e WHERE book_id=? AND branch_id=? AND source_version=?
            AND NOT EXISTS (SELECT 1 FROM lm_retired_events r WHERE r.event_id=e.id)
            AND (invalidated=1 OR ?=1) ORDER BY id''',(book_id,branch_id,source_version,int(source_version in invalid))):
            events.append(dict(row,kind=kind))
    current=conn.execute('SELECT version_id,status FROM chapters WHERE book_id=? AND number=?',(book_id,source[0])).fetchone()
    dependencies=[r[0] for r in conn.execute('SELECT depends_on_version FROM lm_dependencies WHERE book_id=? AND branch_id=? AND source_version=? ORDER BY depends_on_version',(book_id,branch_id,source_version))]
    chain=[dict(r) for r in conn.execute('''WITH RECURSIVE ancestry(v) AS (
        SELECT ? UNION SELECT d.depends_on_version FROM lm_dependencies d JOIN ancestry a ON d.source_version=a.v
        WHERE d.book_id=? AND d.branch_id=?)
        SELECT d.source_version,d.depends_on_version FROM lm_dependencies d JOIN ancestry a ON a.v=d.source_version
        WHERE d.book_id=? AND d.branch_id=? ORDER BY d.source_version,d.depends_on_version''',(source_version,book_id,branch_id,book_id,branch_id))]
    return dict(source_version=source_version,events=events,current_source=dict(current) if current else None,
        dependency_versions=dependencies,reason_chain=chain,invalid_versions=sorted(invalid),
        dependency_candidates=[dict(r) for r in conn.execute("SELECT number,version_id FROM chapters WHERE book_id=? AND status='committed' ORDER BY number",(book_id,)) if r['version_id'] not in invalid],
        revision=conn.execute('SELECT revision FROM books WHERE id=?',(book_id,)).fetchone()[0])


def submit_repair(conn, book_id, source_version, *, replacements, retire_event_ids,
                  dependency_versions, actor, expected_revision, request_id, trust=False, branch_id='main'):
    lm._book(conn,book_id,branch_id)
    if actor != 'author' or trust is not True: raise StoryError('TRUST_REQUIRED','语义修复必须由作者明确核实。')
    for values in (replacements,retire_event_ids,dependency_versions):
        if not isinstance(values,list) or len(values)>1000: raise StoryError('INVALID_REQUEST','修复清单必须为至多1000项的列表。')
    fingerprint=wf._fingerprint('repair',dict(source_version=source_version,replacements=replacements,
        retire_event_ids=retire_event_ids,dependency_versions=dependency_versions,actor=actor,trust=trust,expected_revision=expected_revision))
    replay=wf._replay(conn,book_id,branch_id,request_id,fingerprint)
    if replay is not None: return replay
    revision=wf._revision(conn,book_id,expected_revision)
    preview=preview_repair(conn,book_id,source_version,branch_id=branch_id)
    required={e['id']:e for e in preview['events']}
    if not required: raise StoryError('NO_REPAIR_REQUIRED','该来源没有待修复事件。')
    if any(not isinstance(x,dict) or set(x)!={'event_id','candidate'} or not isinstance(x['candidate'],dict) for x in replacements):
        raise StoryError('INVALID_REQUEST','替代项需要event_id与candidate。')
    ids=retire_event_ids+[x['event_id'] for x in replacements]
    if any(not isinstance(x,str) for x in ids) or len(set(ids))!=len(ids) or set(ids)!=set(required):
        raise StoryError('INCOMPLETE_REPAIR','必须恰好覆盖该来源全部失效事实与状态事件。')
    if any(not isinstance(x,str) for x in dependency_versions) or len(set(dependency_versions))!=len(dependency_versions):
        raise StoryError('INVALID_REQUEST','依赖版本必须为不重复的版本ID。')
    invalid=set(preview['invalid_versions'])
    for dep in dependency_versions:
        row=conn.execute("SELECT 1 FROM chapters WHERE book_id=? AND version_id=? AND status='committed'",(book_id,dep)).fetchone()
        if not row or dep in invalid or dep==source_version: raise StoryError('STALE_SOURCE','新依赖必须是本书当前有效已提交版本。')
    with wf._atomic(conn):
        # Materialize every downstream invalidation before retiring this seed.
        lm.invalidate_version(conn,book_id,source_version,branch_id=branch_id)
        old_dependencies=preview['dependency_versions']
        conn.execute('DELETE FROM lm_dependencies WHERE book_id=? AND branch_id=? AND source_version=?',(book_id,branch_id,source_version))
        mapping=[]
        for replacement in replacements:
            previous=required[replacement['event_id']]; candidate=dict(replacement['candidate'])
            if candidate.get('source_version')!=source_version:
                raise StoryError('INVALID_SCOPE','整来源修复包的替代项必须使用该来源版本。')
            if 'source_chapter' not in candidate:
                raise StoryError('INVALID_REQUEST','替代项必须明确来源章节。')
            body=wf._current_source(conn,book_id,candidate)
            from .memory import exact_evidence
            if not isinstance(candidate.get('evidence'),str) or exact_evidence(body,candidate['evidence']) is None:
                raise StoryError('INVALID_EVIDENCE','替代证据必须匹配当前正文。')
            if previous['kind']=='fact':
                allowed={'subject_entity_id','predicate','value','source_chapter','source_version','evidence','story_valid_from','story_valid_to','known_from_chapter','revealed_from_chapter','scope','owner_entity_id','visibility'}
                mandatory={'subject_entity_id','predicate','value','source_chapter','source_version','evidence','story_valid_from','visibility'}
                if set(candidate)-allowed or not mandatory<=set(candidate): raise StoryError('INVALID_REQUEST','事实替代项缺少显式语义字段。')
                lm.record_fact(conn,book_id,**candidate,verified=True,dependency_versions=dependency_versions,branch_id=branch_id,recorded_revision=revision+1)
                new_id=conn.execute('SELECT id FROM lm_fact_events ORDER BY rowid DESC LIMIT 1').fetchone()[0]
            else:
                if set(candidate)!={'promise_id','status','source_chapter','source_version','evidence'} or candidate['promise_id']!=previous['promise_id']:
                    raise StoryError('INVALID_REQUEST','状态替代项必须明确原伏笔、新状态与证据。')
                candidate.pop('evidence')
                new_id=lm.set_promise_status(conn,book_id,**candidate,dependency_versions=dependency_versions,branch_id=branch_id)
            mapping.append(dict(event_id=previous['id'],kind=previous['kind'],replacement_event_id=new_id,candidate=replacement['candidate']))
        for event_id in retire_event_ids:
            mapping.append(dict(event_id=event_id,kind=required[event_id]['kind'],replacement_event_id=None))
        for dep in dependency_versions:
            conn.execute('INSERT OR IGNORE INTO lm_dependencies VALUES (?,?,?,?)',(book_id,branch_id,source_version,dep))
        repair_id=uid('repair')
        conn.execute('INSERT INTO lm_repair_actions VALUES (?,?,?,?,?,?,?,?)',(repair_id,book_id,branch_id,source_version,actor,revision+1,dumps(dict(old_dependencies=old_dependencies,new_dependencies=dependency_versions,mappings=mapping)),time.time()))
        for m in mapping:
            conn.execute('INSERT INTO lm_retired_events VALUES (?,?,?,?)',(m['event_id'],m['kind'],repair_id,m['replacement_event_id']))
        conn.execute("UPDATE lm_repair_cases SET status='closed',updated_at=? WHERE book_id=? AND branch_id=? AND source_version=?",(time.time(),book_id,branch_id,source_version))
        conn.execute('UPDATE books SET revision=revision+1 WHERE id=?',(book_id,))
        conn.execute("UPDATE tasks SET status='cancelled' WHERE book_id=? AND status='leased' AND base_revision<?",(book_id,revision+1))
        result=dict(repair_id=repair_id,source_version=source_version,revision=revision+1,mappings=mapping,canonical_changed=True)
        wf._audit(conn,book_id,branch_id,request_id,'repair',actor,fingerprint,result)
        return result


class RepairActions:
    def memory_repair_preview(self, book_id, source_version):
        with self.store.read() as conn:
            return preview_repair(conn,book_id,source_version)

    def memory_repair(self, book_id, source_version, **kwargs):
        with self.store.write(book_id) as conn:
            return submit_repair(conn,book_id,source_version,**kwargs)
