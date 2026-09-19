"""Synthetic author repair packages, without models or external resources."""
import pytest


def test_invalidation_does_not_open_empty_unrepairable_case(tmp_path):
    from story_core.storage import Store
    from test_long_memory import chapter
    from story_core.long_memory import invalidate_version
    store = Store(tmp_path)
    book = store.create_book('test', 'test', {})['book_id']
    with store.write(book) as conn:
        version = chapter(conn, book, 1)
        invalidate_version(conn, book, version)
        assert conn.execute("SELECT count(*) FROM lm_repair_cases WHERE status='open'").fetchone()[0] == 0
from story_core import long_memory as lm
from story_core.storage import Store, uid
from story_core.errors import StoryError


def chapter(c, b, n):
    v = uid('v')
    c.execute('INSERT INTO chapter_versions VALUES (?,?,?,?,?,?)', (v,b,n,'test','证据：铜钱三枚。',0))
    c.execute("INSERT OR REPLACE INTO chapters VALUES (?,?,?,'committed')", (b,n,v))
    return v


@pytest.fixture
def chain(tmp_path):
    s = Store(tmp_path); b = s.create_book('test','test',{})['book_id']
    with s.write() as c:
        e = lm.register_entity(c,b,'主角')
        a,v,w = [chapter(c,b,n) for n in (1,2,3)]
        for n,source,dep in [(1,a,[]),(2,v,[a]),(3,w,[v])]:
            lm.record_fact(c,b,e,'p'+str(n),n,source_chapter=n,source_version=source,evidence='铜钱',story_valid_from=n,verified=True,dependency_versions=dep)
        p = lm.schedule_promise(c,b,'约定')
        lm.set_promise_status(c,b,p,'resolved',source_chapter=2,source_version=v,dependency_versions=[a])
        new_a = chapter(c,b,1)
        lm.invalidate_version(c,b,a)
        chapter(c,b,4)
        c.execute("UPDATE chapters SET status='needs_review' WHERE book_id=? AND number=4",(b,))
    return s,b,e,a,v,w,new_a


def api():
    import story_core
    from importlib.util import find_spec
    assert find_spec('story_core.memory_repair'), 'author repair API is missing'
    from story_core import memory_repair
    return memory_repair


def package(c,b,e,v,a):
    r = api(); preview = r.preview_repair(c,b,v)
    fact = next(x for x in preview['events'] if x['kind']=='fact')
    statuses = [x['id'] for x in preview['events'] if x['kind']=='status']
    return dict(replacements=[{'event_id':fact['id'],'candidate':dict(subject_entity_id=e,predicate='p2',value=20,source_chapter=2,source_version=v,evidence='铜钱',story_valid_from=2,visibility='reader')}],retire_event_ids=statuses,dependency_versions=[a],actor='author',expected_revision=0,request_id='repair-b',trust=True)


def test_whole_package_repairs_b_without_reviving_c_and_reinvalidates(chain):
    s,b,e,a,v,w,new_a=chain; r=api()
    with s.write() as c:
        old=[tuple(x) for x in c.execute('SELECT * FROM lm_fact_events')]
        args=package(c,b,e,v,new_a)
        result=r.submit_repair(c,b,v,**args)
        assert r.submit_repair(c,b,v,**args)==result
        assert [(x['predicate'],x['value']) for x in lm.current_state(c,b,[e],story_time=5)]==[('p2',20)]
        assert [tuple(x) for x in c.execute('SELECT * FROM lm_fact_events LIMIT 3')]==old
        assert c.execute("SELECT status FROM chapters WHERE book_id=? AND number=4",(b,)).fetchone()[0]=='needs_review'
        assert c.execute('SELECT depends_on_version FROM lm_dependencies WHERE source_version=?',(v,)).fetchone()[0]==new_a
        assert len(r.preview_repair(c,b,w)['events'])==1
        chapter(c,b,1); lm.invalidate_version(c,b,new_a)
        assert lm.current_state(c,b,[e],story_time=5)==[]
        assert len(r.preview_repair(c,b,v)['events'])==1


@pytest.mark.parametrize('change,code', [({'trust':False},'TRUST_REQUIRED'),({'actor':'model'},'TRUST_REQUIRED'),({'retire_event_ids':[]},'INCOMPLETE_REPAIR'),({'expected_revision':8},'STALE_REVISION')])
def test_rejected_packages_are_atomic(chain,change,code):
    s,b,e,a,v,w,new_a=chain; r=api()
    with s.write() as c:
        args=package(c,b,e,v,new_a); args.update(change)
        with pytest.raises(StoryError) as error: r.submit_repair(c,b,v,**args)
        assert error.value.code==code
        assert c.execute('SELECT count(*) FROM lm_fact_events').fetchone()[0]==3
        assert c.execute('SELECT count(*) FROM lm_repair_actions').fetchone()[0]==0


def test_bad_quote_rolls_back_and_all_retire_does_not_approve_body(chain):
    s,b,e,a,v,w,new_a=chain; r=api()
    with s.write() as c:
        args=package(c,b,e,v,new_a); args['replacements'][0]['candidate']['evidence']='不存在'
        with pytest.raises(StoryError): r.submit_repair(c,b,v,**args)
        assert c.execute('SELECT count(*) FROM lm_retired_events').fetchone()[0]==0
        ids=[x['id'] for x in r.preview_repair(c,b,v)['events']]
        args.update(replacements=[],retire_event_ids=ids,dependency_versions=[])
        r.submit_repair(c,b,v,**args)
        assert lm.current_state(c,b,[e],story_time=5)==[]
        assert c.execute("SELECT status FROM chapters WHERE number=4").fetchone()[0]=='needs_review'


def test_foreign_dependency_and_uncommitted_source_are_rejected(chain):
    s,b,e,a,v,w,new_a=chain; r=api()
    foreign=s.create_book('other','other',{})['book_id']
    with s.write() as c:
        foreign_v=chapter(c,foreign,1)
        args=package(c,b,e,v,new_a); args['dependency_versions']=[foreign_v]
        with pytest.raises(StoryError): r.submit_repair(c,b,v,**args)
        args['dependency_versions']=[new_a]
        c.execute("UPDATE chapters SET status='needs_review' WHERE version_id=?",(v,))
        with pytest.raises(StoryError) as error: r.submit_repair(c,b,v,**args)
        assert error.value.code=='STALE_SOURCE'
        assert c.execute('SELECT count(*) FROM lm_repair_actions').fetchone()[0]==0
        assert c.execute('SELECT depends_on_version FROM lm_dependencies WHERE source_version=?',(v,)).fetchone()[0]==a


def test_status_replacement_and_partial_insert_rollback(chain):
    s,b,e,a,v,w,new_a=chain; r=api()
    with s.write() as c:
        args=package(c,b,e,v,new_a)
        old=next(x for x in r.preview_repair(c,b,v)['events'] if x['kind']=='status')
        args['retire_event_ids']=[]
        status=dict(promise_id=old['promise_id'],status='resolved',source_chapter=2,source_version=v,evidence='not present')
        args['replacements'].append(dict(event_id=old['id'],candidate=status))
        with pytest.raises(StoryError): r.submit_repair(c,b,v,**args)
        assert c.execute('SELECT count(*) FROM lm_fact_events').fetchone()[0]==3
        assert c.execute('SELECT count(*) FROM lm_promise_status_events').fetchone()[0]==1
        assert c.execute('SELECT revision FROM books WHERE id=?',(b,)).fetchone()[0]==0
        status['evidence']='铜钱'
        result=r.submit_repair(c,b,v,**args)
        assert len(result['mappings'])==2
        promises=lm.select_promises(c,b,4,explicit_ids=[old['promise_id']])
        assert promises['required'][0]['status']=='resolved'
        args['dependency_versions']=[]
        with pytest.raises(StoryError) as error:r.submit_repair(c,b,v,**args)
        assert error.value.code=='IDEMPOTENCY_CONFLICT'


def test_bad_candidate_shape_returns_domain_error(chain):
    s,b,e,a,v,w,new_a=chain; r=api()
    with s.write() as c:
        args=package(c,b,e,v,new_a)
        del args['replacements'][0]['candidate']['source_chapter']
        with pytest.raises(StoryError): r.submit_repair(c,b,v,**args)


def test_lease_fence_and_replay_does_not_cancel_new_lease(chain):
    s,b,e,a,v,w,new_a=chain; r=api()
    with s.write() as c:
        c.execute("INSERT INTO runs (id,book_id,status,stage,chapter_number,end_chapter,max_steps,max_revisions,budget_tokens,created_at) VALUES ('r',?,'running','draft',5,5,10,3,1000,0)",(b,))
        c.execute("INSERT INTO tasks VALUES ('t','r',?,'draft',5,'{}','{}',0,'leased','worker','lease',9999999999,NULL,NULL,0)",(b,))
        args=package(c,b,e,v,new_a)
        result=r.submit_repair(c,b,v,**args)
        assert c.execute("SELECT status FROM tasks WHERE id='t'").fetchone()[0]=='cancelled'
        c.execute("UPDATE tasks SET status='leased',base_revision=1 WHERE id='t'")
        assert r.submit_repair(c,b,v,**args)==result
        assert c.execute("SELECT status FROM tasks WHERE id='t'").fetchone()[0]=='leased'


def test_second_repair_retires_only_latest_generation(chain):
    s,b,e,a,v,w,new_a=chain; r=api()
    with s.write() as c:
        r.submit_repair(c,b,v,**package(c,b,e,v,new_a))
        a3=chapter(c,b,1); lm.invalidate_version(c,b,new_a)
        args=package(c,b,e,v,a3); args.update(expected_revision=1,request_id='repair-b-again')
        r.submit_repair(c,b,v,**args)
        assert [x['value'] for x in lm.current_state(c,b,[e],story_time=5)]==[20]
        assert len(r.preview_repair(c,b,w)['events'])==1
        assert c.execute('SELECT count(*) FROM lm_repair_actions').fetchone()[0]==2
        assert c.execute('SELECT count(*) FROM lm_retired_events').fetchone()[0]==3
        chapter(c,b,1);lm.invalidate_version(c,b,a3)
        assert lm.current_state(c,b,[e],story_time=5)==[]
