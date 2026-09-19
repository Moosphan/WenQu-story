"""Offline, human-labelled retrieval evaluation; never opens a default library.

CLI requires an explicit Store directory and JSONL labels. Summary artifacts are
verified, source-labelled JSONL records, not generated summaries. Local semantic
models are only loaded with --allow-local-model. No remote providers are used.
"""
import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from time import perf_counter, process_time

STRATEGIES = ('full-history', 'verified-summary', 'vector-only', 'hybrid')


def load_cases(path):
    cases = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
    seen = set()
    for case in cases:
        required = ('case_id','query','scope','expected_ids','forbidden_ids','category','independent_query_id')
        if not isinstance(case, dict) or any(key not in case for key in required):
            raise ValueError('Each label requires case_id/query/scope/expected_ids/forbidden_ids/category/independent_query_id')
        if any(not isinstance(case[key], str) or not case[key] for key in ('case_id','query','category','independent_query_id')):
            raise ValueError('Identifiers, query and category must be nonempty strings')
        if case['case_id'] in seen: raise ValueError('Duplicate case_id')
        seen.add(case['case_id'])
        for key in ('expected_ids','forbidden_ids','hard_required_ids'):
            values = case.get(key, [])
            if not isinstance(values,list) or any(not isinstance(v,str) for v in values) or len(set(values)) != len(values):
                raise ValueError('Labels must be unique string ID lists')
        if set(case['expected_ids']) & set(case['forbidden_ids']): raise ValueError('Conflicting labels')
        from .retrieval import RetrievalScope
        RetrievalScope(**case['scope'])
    return cases


def ratio(numerator, denominator):
    if not denominator: return dict(numerator=numerator, denominator=0, value=None, ci95=None)
    p = numerator / denominator
    z = 1.95996398454
    center = (p + z*z/(2*denominator))/(1+z*z/denominator)
    half = z*math.sqrt(p*(1-p)/denominator+z*z/(4*denominator**2))/(1+z*z/denominator)
    return dict(numerator=numerator, denominator=denominator, value=p, ci95=[max(0,center-half),min(1,center+half)], ci_method='Wilson descriptive; labels within query may correlate')


def metrics(rows, k):
    recall_n=recall_d=precision_n=unknown_n=unknown_d=hard_n=hard_d=leaks=0
    reciprocal=[]
    for case, result in rows:
        ids=list(dict.fromkeys(h['id'] for h in result['hits']))[:k]
        expected=set(case['expected_ids']); hard=set(case.get('hard_required_ids', []))
        matched=len(expected & set(ids))
        recall_n+=matched; recall_d+=len(expected); precision_n+=matched
        leaks+=len(set(ids)&set(case['forbidden_ids']))
        hard_n+=len(hard&set(ids)); hard_d+=len(hard)
        if not expected: unknown_d+=1; unknown_n+=bool(ids)
        reciprocal.append(next((1/(i+1) for i,id in enumerate(ids) if id in expected),0))
    n=len(reciprocal); mean=sum(reciprocal)/n if n else None
    # Distribution-free Hoeffding interval for bounded per-query reciprocal rank.
    radius=math.sqrt(math.log(40)/(2*n)) if n else None
    return {'recall':ratio(recall_n,recall_d), 'precision':ratio(precision_n,k*len(rows)),
            'mrr':dict(numerator=sum(reciprocal),denominator=n,value=mean,ci95=[max(0,mean-radius),min(1,mean+radius)] if n else None,ci_method='Hoeffding per independent query'),
            'forbidden_leaks':leaks,'unknown_false_positive':ratio(unknown_n,unknown_d),'hard_required_preservation':ratio(hard_n,hard_d)}


def percentile(values,p):
    return sorted(values)[max(0, math.ceil(p*len(values))-1)] if values else None


def evaluate(cases, adapters, k=10):
    if type(k) is not int or not 1<=k<=50: raise ValueError('k must be 1–50')
    output={'k':k,'unique_query_count':len({c['independent_query_id'] for c in cases}),
            'distance_checkpoint_count':len(cases)-len({c['independent_query_id'] for c in cases}),
            'strategies':{},'real_model_calls':0,'generation_quality':None,'style_continuity':None,'actual_bill':None}
    for name in STRATEGIES:
        adapter=adapters.get(name)
        if adapter is None:
            output['strategies'][name]={'status':'unavailable','metrics':None,'reason':'Required backend/artifact not enabled'}; continue
        rows=[]; records=[]; failure=None
        for case in cases:
            start=perf_counter(); cpu=process_time()
            try: result=adapter(case,k)
            except Exception as exc:
                failure=f'{type(exc).__name__}: {exc}'; break
            if result.get('status')=='unavailable': failure=result.get('reason','backend unavailable'); break
            rows.append((case,result))
            records.append(dict(case_id=case['case_id'], latency_ms=(perf_counter()-start)*1000,cpu_ms=(process_time()-cpu)*1000,
                                input_bytes=len(json.dumps(result['hits'],ensure_ascii=False).encode()),candidate_count=result.get('candidates_examined',len(result['hits'])),truncated=result.get('retrieval_truncated',False)))
        if failure:
            output['strategies'][name]={'status':'unavailable','metrics':None,'reason':failure}; continue
        independent=[]; distance=[]; seen=set(); categories=defaultdict(list)
        for row in rows:
            id=row[0]['independent_query_id']
            if id in seen: distance.append(row)
            else: independent.append(row); categories[row[0]['category']].append(row); seen.add(id)
        output['strategies'][name]={'status':'available','metrics':metrics(independent,k),'distance_metrics':metrics(distance,k),
            'categories':{category:metrics(group,k) for category,group in categories.items()},'records':records,
            'latency_ms':{'p50':percentile([r['latency_ms'] for r in records],.5),'p95':percentile([r['latency_ms'] for r in records],.95)}}
    output['limitations']=['First occurrence per independent_query_id contributes to quality metrics; later occurrences are distance checkpoints.',
        'Full-history measures source availability; top-k follows descending chapter then ID, not model understanding.',
        'Precision denominator is k per query, including empty answer slots. No 95% population-quality claim from synthetic labels.']
    return output


def store_adapters(store, summary_path=None, allow_local_model=False):
    from .retrieval import RetrievalScope, retriever_for
    def history(case,k):
        scope=RetrievalScope(**case['scope'])
        if scope.branch_id!='main': return {'status':'unavailable','reason':'Full-history archive only supports main branch'}
        with store.read() as conn:
            rows=conn.execute('''SELECT m.* FROM memories m JOIN chapters c ON c.book_id=m.book_id AND c.number=m.chapter_number AND c.version_id=m.version_id
                WHERE m.book_id=? AND m.chapter_number<=? AND c.status='committed'
                AND (?='author' OR m.visibility='reader')
                AND (?='' OR (m.visibility='reader' AND (m.kind!='knowledge' OR json_extract(m.data,'$.owner')=?)))
                ORDER BY m.chapter_number DESC,m.id''',(scope.book_id,scope.through_chapter,scope.role,scope.pov or '',scope.pov or '')).fetchall()
        return {'hits':[dict(json.loads(row['data']),id=row['id']) for row in rows]}
    adapters=dict.fromkeys(STRATEGIES); adapters['full-history']=history
    if summary_path:
        entries=[json.loads(line) for line in Path(summary_path).read_text().splitlines() if line.strip()]
        for item in entries:
            if item.get('verified') is not True or not item.get('source_ids') or not isinstance(item.get('text'),str) or item.get('visibility') not in ('reader','author'):
                raise ValueError('Summary requires verified=true, text, source_ids, visibility, and book_id')
        def summary(case,k):
            from .memory import _terms
            authorized=history(case,k)
            if authorized.get('status')=='unavailable': return authorized
            ids={hit['id'] for hit in authorized['hits']}; scope=case['scope']; terms=_terms(case['query'])
            eligible=[item for item in entries if item.get('book_id')==scope['book_id'] and set(item['source_ids'])<=ids
                      and (scope.get('role','author')=='author' and not scope.get('pov') or item['visibility']=='reader')
                      and (not item.get('owner') or item.get('owner')==scope.get('pov'))]
            eligible.sort(key=lambda item:-len(terms&_terms(item['text'])))
            hits=[]
            for item in eligible:
                if not terms&_terms(item['text']): continue
                hits.extend({'id':id,'summary':item['text']} for id in item['source_ids'])
            return {'hits':list({hit['id']:hit for hit in hits}.values())[:k]}
        adapters['verified-summary']=summary
    if allow_local_model:
        from .semantic_retrieval import load_policy
        policy=load_policy(store)
        if policy.get('strategy')!='lexical':
            for name, mode in (('vector-only','semantic'),('hybrid','hybrid')):
                def search(case,k,mode=mode):
                    retriever=retriever_for(store,{**policy,'strategy':mode})
                    return retriever.search(case['query'],RetrievalScope(**case['scope']),limit=k)
                adapters[name]=search
    return adapters


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--store',required=True,type=Path)
    parser.add_argument('--labels',required=True,type=Path)
    parser.add_argument('--summary',type=Path)
    parser.add_argument('--allow-local-model',action='store_true')
    parser.add_argument('--k',type=int,default=10)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args(argv)
    from .storage import Store
    result=evaluate(load_cases(args.labels),store_adapters(Store(args.store),args.summary,args.allow_local_model),args.k)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__': main()
