import json
import pytest
from story_core.retrieval_evaluation import evaluate, load_cases, store_adapters


def case(identity='q1', **kw):
    return dict(case_id=identity, query='改写', scope={'book_id':'synthetic','through_chapter':2}, expected_ids=['a','b'], forbidden_ids=['secret'], category='paraphrase', independent_query_id='independent-1', **kw)


def test_metrics_denominators_and_distance_are_separate():
    cases = [case(), case('q2')]
    result = evaluate(cases, {'full-history':lambda c,k:{'hits':[{'id':'b'},{'id':'secret'}]}}, k=2)
    score = result['strategies']['full-history']
    assert result['unique_query_count'] == 1
    assert result['distance_checkpoint_count'] == 1
    assert score['metrics']['recall']['numerator'] == 1
    assert score['metrics']['recall']['denominator'] == 2
    assert score['metrics']['precision']['value'] == .5
    assert score['metrics']['mrr']['value'] == 1
    assert score['metrics']['forbidden_leaks'] == 1
    assert score['distance_metrics']['forbidden_leaks'] == 1
    assert result['strategies']['vector-only']['status'] == 'unavailable'
    assert result['strategies']['vector-only']['metrics'] is None


def test_unknown_and_schema_validation(tmp_path):
    item = case(); item['expected_ids'] = []
    result=evaluate([item], {'hybrid':lambda c,k:{'hits':[{'id':'x'}]}}, k=3)
    assert result['strategies']['hybrid']['metrics']['unknown_false_positive']['value']==1
    path=tmp_path/'labels.jsonl'; path.write_text(json.dumps({'query':'missing labels'})+'\n')
    with pytest.raises(ValueError): load_cases(path)


def test_default_store_does_not_fake_vector(tmp_path):
    from story_core.storage import Store
    adapters=store_adapters(Store(tmp_path))
    assert adapters['vector-only'] is None
    assert adapters['verified-summary'] is None
    assert adapters['hybrid'] is None


def test_real_state_machine_and_guards():
    from story_core.state_machine_benchmark import benchmark
    result=benchmark(2)
    assert result['status']=='complete'
    assert result['committed_chapters']==2
    assert {'draft','extract','continuity','reader','ending'} <= set(result['stages'])
    assert result['guards']['budget']['status']=='needs_attention'
    assert result['guards']['retry']['status']=='needs_attention'
    assert result['guards']['retry']['committed_chapters']==0
    assert result['real_model_calls']==0


def test_real_store_history_and_verified_summary_authorize_sources(tmp_path):
    from story_core.storage import Store
    from story_core.memory import index_chapter
    store=Store(tmp_path/'store'); book=store.create_book('synthetic','synthetic',{})['book_id']
    with store.write(book) as conn:
        conn.execute('INSERT INTO chapter_versions VALUES (?,?,?,?,?,?)',('v1',book,1,'fixture','铜牌借你。作者秘密。',0))
        conn.execute("INSERT INTO chapters VALUES (?,?,?,'committed')",(book,1,'v1'))
        index_chapter(conn,book,1,'v1','铜牌借你。作者秘密。',[
            dict(kind='fact',key='铜牌',value='铜牌借你',evidence='铜牌借你',visibility='reader'),
            dict(kind='fact',key='秘密',value='作者秘密',evidence='作者秘密',visibility='author')])
        ids={row['key']:row['id'] for row in conn.execute('SELECT id,key FROM memories')}
    path=tmp_path/'summary.jsonl'
    path.write_text('\n'.join(json.dumps(dict(verified=True,text='归还旧物',book_id=book,visibility='reader',source_ids=[ids[key]])) for key in ids))
    item=dict(case_id='p1',query='归还旧物',scope=dict(book_id=book,through_chapter=1,role='reader'),expected_ids=[ids['铜牌']],forbidden_ids=[ids['秘密']],category='paraphrase',independent_query_id='p1')
    report=evaluate([item],store_adapters(store,path),k=1)
    for strategy in ('full-history','verified-summary'):
        assert report['strategies'][strategy]['metrics']['recall']['value']==1
        assert report['strategies'][strategy]['metrics']['forbidden_leaks']==0
