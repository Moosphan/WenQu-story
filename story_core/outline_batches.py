"""Bounded outline tasks, checkpointed in submitted tasks until atomic publication."""
from copy import deepcopy
import json

from .errors import StoryError
from .schemas import SCHEMAS, validate

SIZE = 20


def progress(conn, book, run):
    total = book['settings']['chapter_count']
    previous = book.get('plan') or {}
    # Existing manuscript/candidate chapters retain their accepted skeleton.
    boundary = run['chapter_number'] if run.get('candidate') else run['chapter_number'] - 1
    chapters = []
    for chapter in previous.get('chapters', []):
        if chapter['number'] != len(chapters)+1 or chapter['number'] > min(boundary, total):
            break
        chapters.append(deepcopy(chapter))
    seed = len(chapters)
    assembled = {**deepcopy(previous), 'chapters': chapters, 'promises': deepcopy(previous.get('promises', []))}
    if 'volumes' in assembled:
        assembled['volumes'] = [volume for volume in assembled['volumes']
            if volume.get('range', [volume.get('start_chapter'), volume.get('end_chapter')])[1] <= total]
    for row in conn.execute("SELECT input,result FROM tasks WHERE run_id=? AND stage='outline' AND status='submitted' AND base_revision=? ORDER BY created_at,id",
                            (run['run_id'], book['revision'])):
        batch = json.loads(row['input']).get('outline_batch', {})
        if batch.get('total') != total or batch.get('seed') != seed or batch.get('start') != len(chapters)+1:
            continue
        result = json.loads(row['result'])
        if [c['number'] for c in result['chapters']] != list(range(batch['start'], batch['end']+1)):
            raise StoryError('INVALID_STATE', '已保存的分批章纲不连续。')
        chapters.extend(result['chapters'])
        assembled.update({key: value for key, value in result.items() if key != 'chapters'})
    return assembled, seed


def prepare(conn, book, run, data):
    if book['settings']['chapter_count'] <= 40:
        return
    assembled, seed = progress(conn, book, run)
    start = len(assembled['chapters']) + 1
    total = book['settings']['chapter_count']
    if start > total:
        return
    end = min(total, start + SIZE - 1)
    data['outline_batch'] = {'start': start, 'end': end, 'total': total, 'seed': seed}
    # Keep input bounded as the outline grows; prior batches remain in SQLite.
    data['previous_plan'] = {**assembled, 'chapters': assembled['chapters'][-SIZE:]}
    data['instruction'] += (f'\n本次为分批规划：全书目标明确为 {total} 章，但本次 chapters 只能返回第 {start}–{end} 章，'
        f'恰好 {end-start+1} 条，编号按顺序连续。不要返回第 1 章起的旧章纲，也不要省略或跨号。'
        f'第 1–{start-1} 章骨架已保存，不在本次重写；previous_plan 仅展示最近一批。'
        f'故事终局应安排在全书第 {total} 章附近，不能沿用旧短篇幅的提前完结安排。'
        'promises 返回更新后的全书伏笔计划，保留固定 key；可跨越本批，回收章节不得超过全书目标。'
        '每章各字段简洁具体；本批完成后系统保存进度并继续下一批，全部完成才发布完整章纲。')


def output_schema(batch):
    schema = deepcopy(SCHEMAS['outline'])
    chapters = schema['properties']['chapters']
    chapters.update(minItems=batch['end']-batch['start']+1, maxItems=batch['end']-batch['start']+1)
    chapters['items']['properties']['number'].update(minimum=batch['start'], maximum=batch['end'])
    return schema


def assemble(conn, book, run, inputs, result):
    batch = inputs['outline_batch']
    assembled, seed = progress(conn, book, run)
    if batch['seed'] != seed or batch['start'] != len(assembled['chapters'])+1 or batch['total'] != book['settings']['chapter_count']:
        raise StoryError('STALE_REVISION', '规划批次已变化，请重新领取。')
    assembled['chapters'].extend(result['chapters'])
    assembled.update({key: value for key, value in result.items() if key != 'chapters'})
    complete = batch['end'] == batch['total']
    if complete:
        validate('outline', assembled, book, result_size_limit=100000*((batch['total']+SIZE-1)//SIZE))
    return assembled, complete
