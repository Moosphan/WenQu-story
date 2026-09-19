"""Proposed settings and outline commit together; chapter execution is separate."""
from copy import deepcopy
import json
import time

from .errors import StoryError
from .storage import dumps, uid

SCHEMA = '''
CREATE TABLE IF NOT EXISTS settings_planning_jobs (
 id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES books(id), run_id TEXT NOT NULL,
 status TEXT NOT NULL, target TEXT NOT NULL, snapshot TEXT NOT NULL,
 base_revision INTEGER NOT NULL, created_at REAL NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS one_pending_settings_plan ON settings_planning_jobs(book_id) WHERE status='pending';
'''


def pending(conn, book_id):
    row = conn.execute("SELECT * FROM settings_planning_jobs WHERE book_id=? AND status='pending'", (book_id,)).fetchone()
    if not row:
        return None
    value = dict(row)
    for key in ('target', 'snapshot'):
        value[key] = json.loads(value[key])
    return value


def require_idle(conn, book_id):
    if pending(conn, book_id):
        raise StoryError('PLANNING_ACTIVE', '作品设置正在规划，请继续规划或取消规划后再修改作品、启动写作。')


def virtual_book(conn, book):
    job = pending(conn, book['book_id'])
    if not job:
        return book
    if book['revision'] != job['base_revision']:
        raise StoryError('STALE_REVISION', '规划期间作品已变化，请取消当前规划后重新保存设置。')
    result = deepcopy(book)
    result.update(title=job['target']['title'], settings=job['target']['settings'])
    if job['target'].get('brief'):
        result['brief'] = deepcopy(job['target']['brief'])
    if result.get('brief'):
        result['brief']['title'] = result['title']
    return result


def begin(service, conn, book, run, title, settings, *, adopt=False, proposed_brief=None, direction=None):
    require_idle(conn, book['book_id'])
    active = run and run['status'] in ('running', 'paused', 'needs_attention', 'awaiting_author')
    snapshot = {'run': {key: run[key] for key in ('stage', 'end_chapter', 'status')} if active else None,
                'book_status': book['status']}
    if not active:
        run = service._insert_run(conn, book, 1, 500, 2, 2000000)
        # _insert_run is shared with writing and clears an ending. A proposed
        # settings change must not discard any canonical data before publication.
        conn.execute('UPDATE books SET ending=? WHERE id=?', (dumps(book['ending']) if book.get('ending') is not None else None, book['book_id']))
    conn.execute("UPDATE tasks SET status='cancelled' WHERE run_id=? AND status='leased'", (run['run_id'],))
    conn.execute("UPDATE workbench_executions SET status='cancelled',finished_at=? WHERE run_id=? AND status='running'", (time.time(), run['run_id']))
    revision = book['revision'] if adopt else book['revision']+1
    conn.execute("UPDATE books SET revision=?,status='planning' WHERE id=?", (revision, book['book_id']))
    conn.execute("UPDATE runs SET status='running',stage='outline',reason=NULL WHERE id=?", (run['run_id'],))
    conn.execute("INSERT INTO settings_planning_jobs VALUES (?,?,?,'pending',?,?,?,?)",
        (uid('planning'), book['book_id'], run['run_id'], dumps({'title': title, 'settings': settings, 'brief': proposed_brief, 'direction': direction}), dumps(snapshot), revision, time.time()))
    service.store.event(conn, book['book_id'], 'settings_planning_started', {'target_settings': settings, 'adopted': adopt}, run['run_id'])
    return {'book_id': book['book_id'], 'title': book['title'], 'settings': book['settings'], 'revision': revision,
            'replan': True, 'planning_pending': True, 'target_settings': settings, 'run_paused': False}


def finish(service, conn, job, book, run, *, cancelled=False):
    previous = job['snapshot']['run']
    if previous:
        stage = previous['stage']
        if not cancelled and stage == 'outline':
            stage = 'revise' if any(r.get('stage') == 'author' for r in run['reviews'] or []) else 'extract' if run.get('candidate') else 'draft'
        status = 'awaiting_author' if previous['status'] == 'awaiting_author' else 'paused'
        conn.execute('UPDATE runs SET status=?,stage=?,end_chapter=?,reason=? WHERE id=?',
            (status, stage, min(previous['end_chapter'], book['settings']['chapter_count']),
             '已取消设置规划，原配置和章纲保留。' if cancelled else '设置规划已完成。新配置已应用，可单独继续章节写作。', run['run_id']))
    else:
        conn.execute("UPDATE runs SET status='cancelled',reason=? WHERE id=?", ('独立设置规划已结束。', run['run_id']))
    conn.execute("UPDATE tasks SET status='cancelled' WHERE run_id=? AND status='leased'", (run['run_id'],))
    conn.execute("UPDATE settings_planning_jobs SET status=? WHERE id=?", ('cancelled' if cancelled else 'complete', job['id']))
    book_status = ('awaiting_author' if previous['status'] == 'awaiting_author' else 'paused') if previous else job['snapshot']['book_status'] if cancelled else 'draft'
    conn.execute("UPDATE books SET status=? WHERE id=?", (book_status, book['book_id']))
    if cancelled:
        conn.execute('UPDATE books SET revision=revision+1 WHERE id=?', (book['book_id'],))
    service.store.event(conn, book['book_id'], 'settings_planning_cancelled' if cancelled else 'settings_planning_completed', {}, run['run_id'])


def public(conn, book, run):
    job = pending(conn, book['book_id'])
    if not job:
        return None
    from .outline_batches import progress
    stale = book['revision'] != job['base_revision']
    proposed = virtual_book(conn, {**book, 'revision': job['base_revision']})
    plan, seed = progress(conn, proposed, run)
    return {'id': job['id'], 'target_title': job['target']['title'], 'target_settings': job['target']['settings'],
            'status': 'needs_attention' if stale else run['status'], 'planned_chapters': len(plan['chapters']), 'protected_chapters': seed,
            'reason': '规划期间作品已变化，请取消规划后重新保存设置。' if stale else run.get('reason')}
