import pytest
from test_long_memory import world, chapter, fact
from story_core import long_memory as lm
from story_core.errors import StoryError


def test_merge_preserves_history_and_resolves_old_ids(world):
    from story_core.memory_identity import merge_entities, resolve_fact_id, resolve_entity_id, preview_entity_merge
    store, book = world
    with store.write() as c:
        a, b = [lm.register_entity(c, book, n) for n in ('old', 'new')]
        lm.register_alias(c, book, a, 'shared')
        lm.register_alias(c, book, b, 'shared')
        with pytest.raises(StoryError) as ambiguous:
            lm.resolve_alias(c, book, 'shared')
        assert ambiguous.value.code == 'AMBIGUOUS_ENTITY'
        v = chapter(c, book, 1)
        fa = fact(c, book, a, v, 1)
        fb = fact(c, book, b, v, 1, value=3)
        original = [tuple(r) for r in c.execute('SELECT * FROM lm_fact_events')]
        preview = preview_entity_merge(c, book, a, b)
        assert len(preview['conflicts']) == 1
        kwargs = dict(actor='author', expected_revision=0, request_id='merge')
        with pytest.raises(StoryError, match='冲突'):
            merge_entities(c, book, a, b, conflict_resolutions={}, **kwargs)
        conflict = preview['conflicts'][0]
        result = merge_entities(c, book, a, b, conflict_resolutions={conflict['id']: conflict['event_ids'][0]}, **kwargs)
        assert resolve_entity_id(c, book, a) == b
        assert resolve_fact_id(c, book, fa) == fb
        assert lm.resolve_alias(c, book, 'old') == b
        assert lm.resolve_alias(c, book, 'shared') == b
        assert [tuple(r) for r in c.execute('SELECT * FROM lm_fact_events')] == original
        assert len(lm.current_state(c, book, [a], story_time=1)) == 1
        assert lm.current_state(c, book, [], fact_ids=[fa], story_time=1)[0]['fact_id'] == fb
        assert merge_entities(c, book, a, b, conflict_resolutions={conflict['id']: conflict['event_ids'][0]}, **kwargs) == result


def test_owner_collision_chain_and_scope(world):
    from story_core.memory_identity import merge_entities, preview_entity_merge, resolve_entity_id
    store, book = world
    other = store.create_book('other', 'other', {})['book_id']
    with store.write() as c:
        a,b,d = [lm.register_entity(c, book, n) for n in ('a','b','d')]
        foreign = lm.register_entity(c, other, 'foreign')
        v = chapter(c, book, 1)
        for owner in (a,b):
            fact(c, book, d, v, 1, value=10 if owner==a else 3, scope='character_belief', owner_entity_id=owner)
        assert len(preview_entity_merge(c, book, a,b)['affected_fact_ids']) == 2
        collision=preview_entity_merge(c,book,a,b)['conflicts'][0]
        merge_entities(c, book, a,b,conflict_resolutions={collision['id']:collision['event_ids'][0]},actor='author',expected_revision=0,request_id='a')
        assert len(lm.current_state(c, book,[d],story_time=1,pov_entity_id=a)) == 1
        merge_entities(c, book,b,d,conflict_resolutions={},actor='author',expected_revision=1,request_id='b')
        assert resolve_entity_id(c,book,a) == d
        with pytest.raises(StoryError): preview_entity_merge(c,book,d,a)
        with pytest.raises(StoryError): preview_entity_merge(c,book,d,foreign)


def test_merge_writes_canonical_reference_and_old_dependency(world):
    from story_core.memory_identity import merge_entities
    from story_core.dependency_resolution import resolve_dependencies
    from story_core.context_compiler import _stable_ids
    store,book = world
    with store.write() as c:
        a,b = [lm.register_entity(c,book,n) for n in ('a','b')]
        v=chapter(c,book,1)
        old=fact(c,book,a,v,1)
        merge_entities(c,book,a,b,conflict_resolutions={},actor='author',expected_revision=0,request_id='m')
        new=fact(c,book,a,v,1,predicate='relation',value={'type':'entity','entity_id':a,'description':a})
        state=lm.current_state(c,book,[a],story_time=1)
        ref=next(x for x in state if x['predicate']=='relation')
        assert ref['subject_entity_id']==b
        assert ref['value']=={'type':'entity','entity_id':b,'description':a}
        resolved=resolve_dependencies(c,book,{'required_fact_ids':[old],'story_time':1},'draft',2,role='author',pov_entity_id=None)
        assert old in _stable_ids(resolved['state'][0])
        assert resolved['state'][0]['fact_id'] != old


def test_merge_guards_stale_type_and_cycle(world):
    from story_core.memory_identity import merge_entities, resolve_entity_id
    store,book=world
    with store.write() as c:
        a=lm.register_entity(c,book,'a')
        b=lm.register_entity(c,book,'b',entity_type='person')
        with pytest.raises(StoryError) as e:
            merge_entities(c,book,a,b,conflict_resolutions={},actor='author',expected_revision=1,request_id='m')
        assert e.value.code=='STALE_REVISION'
        with pytest.raises(StoryError) as e:
            merge_entities(c,book,a,b,conflict_resolutions={},actor='author',expected_revision=0,request_id='m')
        assert e.value.code=='INVALID_MERGE'
        c.execute('INSERT INTO lm_entity_redirects VALUES (?,?,?,?,?)',(book,'main',a,b,0))
        c.execute('INSERT INTO lm_entity_redirects VALUES (?,?,?,?,?)',(book,'main',b,a,0))
        with pytest.raises(StoryError) as e: resolve_entity_id(c,book,a)
        assert e.value.code=='IDENTITY_CYCLE'


def test_merge_service_replay_preserves_new_lease(world):
    from story_core.memory_actions import MemoryActions
    store,book=world
    actions=MemoryActions(); actions.store=store
    with store.write() as c:
        a,b=[lm.register_entity(c,book,n) for n in ('a','b')]
        c.execute("INSERT INTO runs (id,book_id,status,stage,chapter_number,end_chapter,max_steps,max_revisions,budget_tokens,created_at) VALUES ('run',?,'running','draft',2,2,10,2,1000,0)",(book,))
        c.execute("INSERT INTO tasks VALUES ('old','run',?,'draft',2,'{}','{}',0,'leased','w','old',99999,NULL,NULL,0)",(book,))
    kwargs=dict(conflict_resolutions={},actor='author',expected_revision=0,request_id='merge')
    result=actions.memory_merge(book,a,b,**kwargs)
    with store.write() as c:
        assert c.execute("SELECT status FROM tasks WHERE id='old'").fetchone()[0]=='cancelled'
        c.execute("INSERT INTO tasks VALUES ('new','run',?,'draft',2,'{}','{}',1,'leased','w','new',99999,NULL,NULL,0)",(book,))
    assert actions.memory_merge(book,a,b,**kwargs)==result
    with store.read() as c:
        assert c.execute("SELECT status FROM tasks WHERE id='new'").fetchone()[0]=='leased'


def test_merge_book_deletion_cascades(world):
    from story_core.memory_identity import merge_entities
    store,book=world
    with store.write() as c:
        a,b=[lm.register_entity(c,book,n) for n in ('a','b')]
        v=chapter(c,book,1)
        fact(c,book,a,v,1)
        merge_entities(c,book,a,b,conflict_resolutions={},actor='author',expected_revision=0,request_id='m')
        c.execute('DELETE FROM chapters WHERE book_id=?',(book,))
        c.execute('DELETE FROM chapter_versions WHERE book_id=?',(book,))
        c.execute('DELETE FROM books WHERE id=?',(book,))
        assert not c.execute('PRAGMA foreign_key_check').fetchall()


def test_merge_http_and_cli(world, tmp_path):
    from fastapi.testclient import TestClient
    from story_core.http import create_app
    from test_cli import cli, output
    store,book=world
    with store.write() as c:
        a,b,d=[lm.register_entity(c,book,n) for n in ('a','b','d')]
    with TestClient(create_app(store.root)) as client:
        base=f'/api/books/{book}/memory/merge'
        preview=client.post(base+'/preview',json={'source_id':a,'target_id':b})
        assert preview.status_code==200
        response=client.post(base,json=dict(source_id=a,target_id=b,conflict_resolutions={},expected_revision=0,request_id='m'))
        assert response.status_code==200, response.text
    preview=cli(store.root,'memory',book,'merge-preview',b,d)
    assert preview.returncode==0, preview.stderr
    assert output(preview)['revision']==1
    decisions=tmp_path/'decisions.json'; decisions.write_text('{}')
    merged=cli(store.root,'memory',book,'merge',b,d,'--actor','author','--expected-revision','1','--request-id','cli','--resolutions-file',str(decisions))
    assert merged.returncode==0, merged.stderr
    assert output(merged)['target_id']==d


def test_explicit_dependency_deduplicates_index_hit():
    from story_core.dependency_resolution import attach_dependencies
    hit={'id':'mem1','kind':'fact','key':'coins','source':{'chapter_number':1,'version_id':'v1'},'visibility':'reader'}
    data={'historical_evidence':[dict(hit)],'required_memory':[]}
    attach_dependencies(data,{'evidence':[dict(hit,context_reason='explicit_dependency',state_semantics='historical_evidence')],'state':[]})
    assert data['historical_evidence']==[]
    assert len(data['required_memory'])==1
