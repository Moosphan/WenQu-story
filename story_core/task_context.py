"""Lease-bound lookup turns: two per chapter, separate from content reviews."""
import json
import time

from .errors import StoryError
from .retrieval import MAX_LOOKUPS, RetrievalScope, SQLiteRetriever
from .storage import dumps

LOOKUP_STAGES = {'draft', 'revise', 'continuity', 'reader', 'arc', 'ending'}
LOOKUP_SCHEMA = {'type': 'object', 'properties': {'context_lookup': {
    'type': 'object', 'properties': {'query': {'type': 'string', 'minLength': 1, 'maxLength': 1000},
                                   'reason': {'type': 'string', 'minLength': 1, 'maxLength': 500}},
    'required': ['query', 'reason'], 'additionalProperties': False}},
    'required': ['context_lookup'], 'additionalProperties': False}


def lookup_count(conn, run_id, number):
    return sum(json.loads(row[0]).get('chapter_number') == number for row in conn.execute(
        "SELECT payload FROM events WHERE run_id=? AND kind='context_lookup'", (run_id,)))


def prepare_lookup(conn, book, run, data, schema, policy):
    if policy.mode != 'adaptive' or run['stage'] not in LOOKUP_STAGES:
        return data, schema
    used = lookup_count(conn, run['run_id'], run['chapter_number'])
    data['lookup_policy'] = {'remaining': max(0, MAX_LOOKUPS-used),
        'instruction': '仅缺少必要历史证据时单独返回 context_lookup(query,reason)，不可与正文/审稿混合。最多两次；结果替换可选证据。未检索到表示未知，不得据此编造。额度用完请完成当前任务并明确无法确定之处。'}
    previous = conn.execute("""SELECT response FROM tasks WHERE run_id=? AND chapter_number=?
        AND stage=? AND status='lookup' AND base_revision=? ORDER BY created_at DESC,rowid DESC LIMIT 1""",
        (run['run_id'],run['chapter_number'],run['stage'],book['revision'])).fetchone()
    if previous:
        result = json.loads(previous[0])
        data['historical_evidence'] = result['evidence']
        data['lookup_result'] = {'hit_count': result['hit_count'], 'index_complete': result['index_complete'],
                                 'lookup_number': result['lookup_number']}
    return data, {'anyOf': [schema, LOOKUP_SCHEMA]} if used < MAX_LOOKUPS else schema


class ContextActions:
    def lookup_task(self, task_id, lease_id, query, reason, worker_id='host'):
        from jsonschema import Draft202012Validator
        result = {'context_lookup': {'query': query, 'reason': reason}}
        if list(Draft202012Validator(LOOKUP_SCHEMA).iter_errors(result)) or not query.strip() or not reason.strip():
            raise StoryError('INVALID_LOOKUP', '补查需要 1–1000 字查询与 1–500 字说明。')
        with self.store.write() as conn:
            task = conn.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
            if not task:
                raise StoryError('NOT_FOUND', '找不到任务。')
            if task['lease_id'] != lease_id or task['worker_id'] != worker_id:
                raise StoryError('INVALID_LEASE', '补查租约或执行者不匹配。')
            if task['status'] == 'lookup':
                if json.loads(task['result']) != result:
                    raise StoryError('IDEMPOTENCY_CONFLICT', '该补查任务已提交不同查询。')
                return {k: v for k, v in json.loads(task['response']).items() if k != 'evidence'}
            run = conn.execute('SELECT * FROM runs WHERE id=?', (task['run_id'],)).fetchone()
            if task['status'] != 'leased' or task['lease_until'] <= time.time() or run['status'] != 'running':
                raise StoryError('INVALID_LEASE', '补查任务已暂停、过期或失效。')
            book = self.store.decode_book(conn.execute('SELECT * FROM books WHERE id=?', (task['book_id'],)).fetchone())
            if conn.execute('SELECT 1 FROM book_trash WHERE book_id=?', (task['book_id'],)).fetchone():
                raise StoryError('BOOK_TRASHED', '作品已移入回收站。')
            if book['revision'] != task['base_revision']:
                raise StoryError('STALE_REVISION', '作品已修改，请重新领取上下文。')
            data = json.loads(task['input'])
            if data.get('context_diagnostics', {}).get('mode') != 'adaptive' or task['stage'] not in LOOKUP_STAGES:
                raise StoryError('LOOKUP_DISABLED', '当前任务未启用自适应补查。')
            used = lookup_count(conn, task['run_id'], task['chapter_number'])
            if used >= MAX_LOOKUPS:
                raise StoryError('LOOKUP_LIMIT', '本章已用完两次补查，请处理已知资料中的不确定项。')
            pov = (data.get('pov_context') or {}).get('pov')
            role = 'reader' if task['stage'] == 'reader' else 'author'
            scope = RetrievalScope(task['book_id'], through_chapter=task['chapter_number']-1, role=role, pov=pov)
            retrieved = SQLiteRetriever(self.store).search(query, scope, limit=10)
            response = {'accepted': True, 'task_id': task_id, 'status': 'context_refreshed', 'next_stage': task['stage'],
                        'lookup_number': used+1, 'hit_count': len(retrieved['hits']),
                        'index_complete': retrieved['index_complete'], 'evidence': retrieved['hits']}
            # No candidate change, content-review count or world-state promotion.
            # Next lease builds a fresh bounded package and reserves the next call.
            conn.execute("UPDATE tasks SET status='lookup',result=?,response=? WHERE id=?", (dumps(result), dumps(response), task_id))
            self.store.event(conn, task['book_id'], 'context_lookup', {'task_id': task_id,
                'chapter_number': task['chapter_number'], 'lookup_number': used+1,
                'hit_count': len(retrieved['hits']), 'index_complete': retrieved['index_complete']}, task['run_id'])
            return {k: v for k, v in response.items() if k != 'evidence'}
