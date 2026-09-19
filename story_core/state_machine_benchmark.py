"""Synthetic next_task/submit_task trajectory, without any model/provider calls.

Runs in a fresh temporary Store. Experiments above the product's 200-chapter cap
use an explicit database fixture; public product validation is never changed.
"""
import argparse
import json
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from .service import StoryService
from .storage import dumps

BODY='沈知秋拧下最后一颗螺丝。妹妹端着冷掉的面站在门边，手背上还沾着面粉。他没抬头，先把桌边空出一小块。收音机忽然响了。熟悉的咳嗽声传来，他手里的螺丝刀落在桌上。妹妹把碗放下，抓住了他的衣袖。'


def synthetic_result(task, block=False):
    if task.get('input',{}).get('review_adjudication'):
        return {'decisions':[{'index':i,'decision':'confirmed_conflict','evidence_paragraph':1,'reason':'合成阻断实验'} for i,item in enumerate(task['input']['review_adjudication']['issues']) if item['severity'] in ('major','blocker')]}
    stage=task['stage']
    if stage=='brief': return dict(title='合成收音机',premise='兄妹修复收音机寻找父亲。',audience='悬疑读者',pov='沈知秋',style='克制具体',ending='父亲归来。',characters=[dict(name='沈知秋',desire='寻找父亲',fear='失去妹妹',boundary='不牺牲妹妹',voice='短句')])
    if stage=='outline': return dict(chapters=[dict(number=i,title=f'回音{i}',goal='修好收音机',conflict='零件不足',change='听到留言',payoff='找到线索',emotion='期望',pov='沈知秋') for i in range(1,task['input']['settings']['chapter_count']+1)],promises=[])
    if stage in ('draft','revise'): return dict(title='回音',body=BODY)
    if stage=='extract': return dict(memories=[dict(kind='fact',key='收音机',value='传来熟悉咳嗽声',evidence='熟悉的咳嗽声传来',visibility='reader')])
    if stage in ('continuity','reader','arc','ending'): return dict(verdict='revise' if block else 'pass',issues=[dict(severity='blocker',dimension='continuity',evidence='收音机',explanation='合成冲突',suggestion='改正')] if block else [],notes='合成评审；不代表文学质量')
    raise ValueError(stage)


def trajectory(root,chapters,mode='adaptive',block=False,budget=100000000):
    service=StoryService(root)
    book=service.open_book('合成实验：修复收音机寻找父亲。',title='Synthetic only',chapter_count=min(chapters,200),target_words=80)['book_id']
    # Explicit fixture-only extension: no weakening of open_book/update validators.
    if chapters>200:
        with service.store.write(book) as conn:
            config=json.loads(conn.execute('SELECT config FROM books WHERE id=?',(book,)).fetchone()[0])
            config['chapter_count']=chapters
            conn.execute('UPDATE books SET config=? WHERE id=?',(dumps(config),book))
    service.start_run(book,max_steps=10000,max_revisions=1,budget_tokens=budget,review_mode='legacy')
    points=[]; stages=Counter(); stop={}; upper=chapters*12+20
    # Context policy is frozen per task by the product's environment adapter.
    import os
    from unittest.mock import patch
    with patch.dict(os.environ,{'HULK_CONTEXT_MODE':mode,'HULK_CONTEXT_WINDOW_TOKENS':'64000'}):
        for _ in range(upper):
            start=perf_counter(); task=service.next_task(book); elapsed=(perf_counter()-start)*1000
            if not task.get('task_id'): stop=task; break
            stages[task['stage']]+=1
            points.append({'chapter':task.get('chapter_number'),'stage':task['stage'],'next_task_ms':elapsed,
                           'input_bytes':len(json.dumps(task['input'],ensure_ascii=False).encode()),
                           'context_diagnostics':task['input'].get('context_diagnostics')})
            result=synthetic_result(task,block=block and task['stage']=='continuity')
            service.submit_task(task['task_id'],task['lease_id'],result)
        else: raise RuntimeError('Synthetic driver exceeded explicit finite transition bound')
    status=service.status(book)
    with service.store.read() as conn:
        committed=conn.execute("SELECT count(*) FROM chapters WHERE book_id=? AND status='committed'",(book,)).fetchone()[0]
    run=status['run']
    return {'status':stop.get('status'),'committed_chapters':committed,'stages':dict(stages),'points':points,
            'steps':run['steps'],'reserved_tokens':run['tokens'],'budget_tokens':budget,'driver_transition_bound':upper,
            'max_revisions':1,'reason':run.get('reason')}


def benchmark(chapters=500,mode='adaptive',include_guards=True):
    if type(chapters) is not int or not 1<=chapters<=500: raise ValueError('chapters must be 1–500')
    if mode not in ('off','shadow','adaptive'): raise ValueError('Invalid context mode')
    with TemporaryDirectory(prefix='wenqu-state-experiment-') as directory:
        root=Path(directory)
        result=trajectory(root/'main',chapters,mode)
        result['guards']={'budget':trajectory(root/'budget',1,mode,budget=1),
                          'retry':trajectory(root/'retry',1,mode,block=True)} if include_guards else {}
    result.update(version='synthetic-service-state-v1',mode=mode,real_model_calls=0,generation_quality=None,actual_bill=None,
                  checkpoints=[{'chapter':n,'points':[p for p in result['points'] if p['chapter']==n]} for n in (100,300,500) if n<=chapters],
                  limitations=['Real StoryService transitions with a deterministic synthetic result adapter, not model quality or billing.',
                               'Above 200 chapters uses a database-only experimental fixture; product chapter limits remain unchanged.',
                               'Wall time and serialized input bytes are measured; reserved tokens are product estimates.'])
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--chapters',type=int,default=500)
    parser.add_argument('--mode',choices=['off','shadow','adaptive'],default='adaptive')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(argv)
    result=benchmark(args.chapters,args.mode)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(dumps({key:result[key] for key in ('status','committed_chapters','stages','steps','real_model_calls')}))


if __name__=='__main__': main()
