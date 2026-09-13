"""Durable ownership for local HTTP workers; model calls stay outside transactions."""
import fcntl
import json
import time
from contextlib import contextmanager

from .errors import StoryError
from .providers import run_worker
from .storage import dumps, uid


class WorkbenchRuntime:
    def __init__(self, service):
        self.service = service
        self.store = service.store

    @contextmanager
    def server_lock(self):
        # Ownership is released by the OS even after SIGKILL. Never steal Skill/MCP leases.
        with (self.store.root / '.workbench.lock').open('a') as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise StoryError('SERVER_ALREADY_RUNNING', '此书库已有工作台服务在运行，请使用现有服务。') from None
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def reserve(self, book_id, executor):
        now = time.time()
        with self.store.write(book_id) as conn:
            if conn.execute("SELECT 1 FROM workbench_executions WHERE book_id=? AND status='running'", (book_id,)).fetchone():
                raise StoryError('WORKER_RUNNING', '本书已有执行器在运行，无需重复启动。')
            run = self.service._latest_run(conn, book_id)
            if not run or run['status'] != 'running':
                raise StoryError('NO_ACTIVE_RUN', '请先开始或恢复本轮写作。')
            task = conn.execute("SELECT worker_id,lease_until FROM tasks WHERE run_id=? AND status='leased' AND lease_until>?", (run['run_id'], now)).fetchone()
            if task:
                raise StoryError('TASK_BUSY', '当前任务已由其他宿主领取，等待其提交或租约到期后再继续。',
                                 {'worker_id': task['worker_id'], 'lease_until': task['lease_until']})
            session = {'id': uid('web'), 'book_id': book_id, 'run_id': run['run_id'], 'executor': executor}
            conn.execute("INSERT INTO workbench_executions(id,book_id,run_id,executor,status,started_at) VALUES (?,?,?,?,'running',?)",
                         (session['id'], book_id, run['run_id'], executor, now))
            self.store.event(conn, book_id, 'worker_started', {'execution_id': session['id'], 'executor': executor,
                            'stage': run['stage'], 'chapter_number': run['chapter_number']}, run['run_id'])
        return session

    def execute(self, session, provider):
        error = None
        try:
            run_worker(self.service, session['book_id'], provider, worker_id=session['id'])
        except Exception as exc:
            error = exc.as_dict() if isinstance(exc, StoryError) else {'code': 'WORKER_ERROR', 'message': '执行器异常，进度已保留。'}
        with self.store.write(session['book_id']) as conn:
            current = conn.execute('SELECT status FROM workbench_executions WHERE id=?', (session['id'],)).fetchone()
            if not current or current['status'] != 'running':
                return
            run = self.service._latest_run(conn, session['book_id'])
            outcome = 'failed' if error else run['status'] if run and run['run_id'] == session['run_id'] else 'cancelled'
            # A worker that found another lease must never be reported as actively computing.
            if outcome == 'running':
                outcome = 'waiting'
            conn.execute('UPDATE workbench_executions SET status=?,error=?,finished_at=? WHERE id=?',
                         (outcome, dumps(error) if error else None, time.time(), session['id']))
            self.store.event(conn, session['book_id'], 'worker_finished', {'execution_id': session['id'], 'outcome': outcome,
                            'stage': run['stage'] if run else None, 'chapter_number': run['chapter_number'] if run else None,
                            'error': error}, session['run_id'])

    def recover(self):
        """Call only while holding the single-server lock (startup or shutdown)."""
        with self.store.write() as conn:
            sessions = conn.execute("SELECT * FROM workbench_executions WHERE status='running'").fetchall()
            for session in sessions:
                error = {'code': 'SERVER_INTERRUPTED', 'message': '工作台服务中断，本轮已暂停。已保存的正文和候选稿仍在，可从当前阶段恢复。'}
                conn.execute("UPDATE workbench_executions SET status='interrupted',error=?,finished_at=? WHERE id=?",
                             (dumps(error), time.time(), session['id']))
                run = self.service._latest_run(conn, session['book_id'])
                foreign = conn.execute("SELECT 1 FROM tasks WHERE run_id=? AND status='leased' AND worker_id!=? AND lease_until>?",
                                       (session['run_id'], session['id'], time.time())).fetchone()
                conn.execute("UPDATE tasks SET status='cancelled' WHERE run_id=? AND worker_id=? AND status='leased'",
                             (session['run_id'], session['id']))
                if run and run['run_id'] == session['run_id'] and run['status'] == 'running' and not foreign:
                    conn.execute("UPDATE runs SET status='paused',reason=? WHERE id=?", (error['message'], session['run_id']))
                    conn.execute("UPDATE books SET status='paused' WHERE id=?", (session['book_id'],))
                self.store.event(conn, session['book_id'], 'worker_interrupted', {'execution_id': session['id'], **error,
                                'stage': run['stage'] if run else None, 'chapter_number': run['chapter_number'] if run else None}, session['run_id'])

    def latest(self, book_id):
        with self.store.read() as conn:
            row = conn.execute('SELECT * FROM workbench_executions WHERE book_id=? ORDER BY started_at DESC,rowid DESC LIMIT 1', (book_id,)).fetchone()
        if not row:
            return None
        value = dict(row)
        value['error'] = json.loads(value['error']) if value['error'] else None
        return value
