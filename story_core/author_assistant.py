"""Durable, revision-bound author discussion. Only apply can start canonical planning."""
import json
import time
from jsonschema import Draft202012Validator
from .errors import StoryError
from .storage import dumps, uid
from .schemas import SCHEMAS, obj, validate
from . import settings_planning

SCHEMA = '''
CREATE TABLE IF NOT EXISTS author_assistant_turns (
 id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES books(id), message TEXT NOT NULL,
 status TEXT NOT NULL, reply TEXT, proposal TEXT, error TEXT, base_revision INTEGER NOT NULL,
 snapshot TEXT NOT NULL, applied TEXT, created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS assistant_history ON author_assistant_turns(book_id,created_at);
CREATE UNIQUE INDEX IF NOT EXISTS one_pending_assistant ON author_assistant_turns(book_id) WHERE status IN ('queued','running');
'''
OUTPUT = obj({'reply': {'type': 'string', 'minLength': 1, 'maxLength': 16000},
              'proposal': {'anyOf': [{'type': 'null'}, obj({'direction': {'type': 'string', 'minLength': 1, 'maxLength': 8000}, 'brief': SCHEMAS['brief']})]}})
INSTRUCTION = '''你是作者的全局创作助手，用中文交流设定、人物、情节、正文与全书结构。充分回答作者的问题并解释判断；普通讨论 proposal 必须为 null。只有作者希望调整全书规划时，才提出 proposal，其中 direction 是完整可执行的规划方向，brief 是完整拟议设定。提案尚未被作者采纳，不是正史，不得声称已修改任何内容。实际正文与未发生的规划必须区分；上下文是有界快照，明确说明缺失或截断资料，不要声称读过未提供的正文。保留已写正文和候选章节骨架。不得删除或改名既有人物；已发生事实保持不变，只调整作者要求的未来设定与人物动机。只返回符合 output_schema 的 JSON。'''


def _book(service, conn, book_id):
    return service.store.decode_book(conn.execute('SELECT * FROM books WHERE id=?', (book_id,)).fetchone())


def _idle(service, conn, book_id):
    settings_planning.require_idle(conn, book_id)
    run = service._latest_run(conn, book_id)
    if run and run['status'] == 'running':
        raise StoryError('RUN_ACTIVE', '请先暂停正在运行的写作，再与助手讨论。')
    if conn.execute("SELECT 1 FROM workbench_executions WHERE book_id=? AND status='running'", (book_id,)).fetchone():
        raise StoryError('RUN_ACTIVE', '写作执行器正在停止，请稍后重试。')
    return run


def _boundary(conn, book_id, run):
    saved = conn.execute('SELECT COALESCE(MAX(number),0) FROM chapters WHERE book_id=?', (book_id,)).fetchone()[0]
    return max(saved, run['chapter_number'] if run and run.get('candidate') else 0)


def _bounded(value, limit):
    raw = dumps(value)
    encoded = raw.encode('utf-8')
    if len(encoded) <= limit:
        return {'content': value, 'truncated': False}
    return {'excerpt': encoded[:limit].decode('utf-8', errors='ignore'), 'truncated': True, 'original_bytes': len(encoded)}


def _public(row, latest, revision, available):
    result = {key: row[key] for key in ('id', 'message', 'status', 'reply', 'error', 'base_revision')}
    result['proposal'] = json.loads(row['proposal']) if row['proposal'] else None
    result['can_apply'] = bool(available and row['id'] == latest and row['status'] == 'complete' and row['proposal'] and not row['applied'] and row['base_revision'] == revision)
    return result


def history(service, book_id):
    with service.store.read() as conn:
        book = _book(service, conn, book_id)
        rows = conn.execute('SELECT * FROM author_assistant_turns WHERE book_id=? ORDER BY created_at,id', (book_id,)).fetchall()
        run = service._latest_run(conn, book_id)
        available = not settings_planning.pending(conn, book_id) and not (run and run['status'] == 'running')
        available = available and _boundary(conn, book_id, run) < book['settings']['chapter_count']
        available = available and not conn.execute("SELECT 1 FROM workbench_executions WHERE book_id=? AND status='running'", (book_id,)).fetchone()
        return {'turns': [_public(row, rows[-1]['id'], book['revision'], available) for row in rows], 'protected_through': _boundary(conn, book_id, run)}


def begin(service, book_id, message, expected_revision, chapter_number=None):
    if not isinstance(message, str) or not message.strip() or len(message.encode('utf-8')) > 12000:
        raise StoryError('INVALID_REQUEST', '请输入问题，单条消息最多 12000 字节。')
    if type(expected_revision) is not int or (chapter_number is not None and (type(chapter_number) is not int or chapter_number < 1)):
        raise StoryError('INVALID_REQUEST', '作品版本或章节编号无效。')
    with service.store.write(book_id, expected_revision) as conn:
        book = _book(service, conn, book_id)
        run = _idle(service, conn, book_id)
        if conn.execute("SELECT 1 FROM author_assistant_turns WHERE book_id=? AND status IN ('queued','running')", (book_id,)).fetchone():
            raise StoryError('ASSISTANT_ACTIVE', '助手正在回复，请等待当前回复完成。')
        rows = conn.execute('SELECT message,reply,proposal FROM author_assistant_turns WHERE book_id=? ORDER BY created_at DESC,id DESC LIMIT 6', (book_id,)).fetchall()
        history_data = [{key: _clip(row[key] or '', 1000) for key in ('message', 'reply')} for row in reversed(rows)]
        proposal_row = conn.execute('SELECT proposal FROM author_assistant_turns WHERE book_id=? AND proposal IS NOT NULL ORDER BY created_at DESC,id DESC LIMIT 1', (book_id,)).fetchone()
        latest_proposal = json.loads(proposal_row['proposal']) if proposal_row else None
        plan = book.get('plan') or {}
        chapter = None
        if chapter_number:
            row = conn.execute('SELECT c.number,c.status,v.title,v.body FROM chapters c JOIN chapter_versions v ON c.version_id=v.id WHERE c.book_id=? AND c.number=?', (book_id, chapter_number)).fetchone()
            if run and run['chapter_number'] == chapter_number and run.get('candidate'):
                chapter = {'number': chapter_number, 'status': 'candidate', **run['candidate']}
            elif row: chapter = dict(row)
        snapshot = {'instruction': INSTRUCTION, 'message': message.strip(), 'revision': book['revision'],
            'brief': book.get('brief'), 'project': _bounded(book.get('project'), 4000),
            'settings': book['settings'], 'volume_structure': plan.get('volumes', []),
            'chapter_roadmap': [{'number': c['number'], **{key: _clip(c.get(key, ''), 60) for key in ('title', 'goal', 'conflict', 'change')}} for c in plan.get('chapters', [])],
            'selected_chapter': _bounded(chapter, 10000), 'selected_chapter_number': chapter_number,
            'history': history_data, 'latest_proposal': latest_proposal, 'protected_through': _boundary(conn, book_id, run),
            'context_notice': '完整设定与卷纲、全书每章压缩路线图（字段最多60字节，省略号表示截断）、最近6轮压缩对话及最新提案、选中章节有界正文；未选中章节正文未载入。章纲是计划，不是正文事实。'}
        if len(dumps(snapshot).encode('utf-8')) > 180000:
            raise StoryError('CONTEXT_TOO_LARGE', '完整设定和卷纲超过助手上下文上限，请精简资料后重试；不会截断人物设定生成提案。')
        turn_id = uid('assistant')
        conn.execute("INSERT INTO author_assistant_turns(id,book_id,message,status,base_revision,snapshot,created_at) VALUES (?,?,?,'queued',?,?,?)", (turn_id, book_id, message.strip(), book['revision'], dumps(snapshot), time.time()))
    return next(turn for turn in history(service, book_id)['turns'] if turn['id'] == turn_id)


def fail(service, book_id, turn_id, error):
    # Provider exception strings may contain credentials/URLs. Persist only a safe diagnosis.
    message = '助手调用失败，请检查模型配置后重新发送。'
    if isinstance(error, StoryError) and error.code == 'INVALID_RESULT': message = '助手返回格式不完整，请重新发送。'
    with service.store.write(book_id) as conn:
        conn.execute("UPDATE author_assistant_turns SET status='failed',error=? WHERE id=? AND book_id=? AND status IN ('queued','running')", (message, turn_id, book_id))


def execute(service, book_id, turn_id, provider):
    try:
        with service.store.write(book_id) as conn:
            row = conn.execute("SELECT * FROM author_assistant_turns WHERE id=? AND book_id=? AND status='queued'", (turn_id, book_id)).fetchone()
            if not row: return
            snapshot = json.loads(row['snapshot'])
            conn.execute("UPDATE author_assistant_turns SET status='running' WHERE id=?", (turn_id,))
        task = {'task_id': turn_id, 'book_id': book_id, 'stage': 'author_assistant', 'input': snapshot, 'output_schema': OUTPUT}
        result = provider.generate(task)
        if len(dumps(result).encode()) > 64000 or next(Draft202012Validator(OUTPUT).iter_errors(result), None):
            raise StoryError('INVALID_RESULT', '助手回复不符合协议。')
        if result['proposal']:
            validate('brief', result['proposal']['brief'], {})
            _preserve_names(snapshot.get('brief'), result['proposal']['brief'])
        with service.store.write(book_id) as conn:
            conn.execute("UPDATE author_assistant_turns SET status='complete',reply=?,proposal=? WHERE id=? AND book_id=? AND status='running'", (result['reply'], dumps(result['proposal']) if result['proposal'] else None, turn_id, book_id))
            usage = getattr(provider, 'last_usage', None)
            service.store.event(conn, book_id, 'assistant_usage', {'turn_id': turn_id, 'reported_tokens': usage if type(usage) is int and usage >= 0 else None})
    except Exception as error:
        try: fail(service, book_id, turn_id, error)
        except StoryError: pass  # Book may have been deleted while provider was responding.


def recover(service):
    with service.store.write() as conn:
        conn.execute("UPDATE author_assistant_turns SET status='failed',error='服务已重启，上次助手调用已中断，请重新发送。' WHERE status IN ('queued','running')")


def apply(service, book_id, turn_id):
    with service.store.write(book_id) as conn:
        row = conn.execute('SELECT * FROM author_assistant_turns WHERE id=? AND book_id=?', (turn_id, book_id)).fetchone()
        if not row: raise StoryError('NOT_FOUND', '找不到该讨论提案。')
        if row['applied']: return json.loads(row['applied'])
        book = _book(service, conn, book_id)
        latest = conn.execute('SELECT id FROM author_assistant_turns WHERE book_id=? ORDER BY created_at DESC,id DESC LIMIT 1', (book_id,)).fetchone()[0]
        if row['base_revision'] != book['revision'] or latest != turn_id:
            raise StoryError('STALE_REVISION', '作品或讨论已变化，请让助手基于最新内容重新给出提案。')
        if row['status'] != 'complete' or not row['proposal']:
            raise StoryError('INVALID_REQUEST', '该回复没有可确认的规划提案。')
        run = _idle(service, conn, book_id)
        proposal = json.loads(row['proposal'])
        validate('brief', proposal['brief'], book)
        _preserve_names(book.get('brief'), proposal['brief'])
        if _boundary(conn, book_id, run) >= book['settings']['chapter_count']:
            raise StoryError('NO_FUTURE_CHAPTERS', '所有章节已有正文或候选稿，没有可重规划的后续章节；请先增加章节总数。')
        result = settings_planning.begin(service, conn, book, run, proposal['brief']['title'], book['settings'], proposed_brief=proposal['brief'], direction=proposal['direction'])
        conn.execute('UPDATE author_assistant_turns SET applied=? WHERE id=?', (dumps(result), turn_id))
        return result


def _clip(value, byte_limit):
    raw = value.encode('utf-8')
    return value if len(raw) <= byte_limit else raw[:byte_limit].decode('utf-8', errors='ignore') + '…'


def _preserve_names(original, proposed):
    names = {character['name'] for character in (original or {}).get('characters', [])}
    if not names <= {character['name'] for character in proposed['characters']}:
        raise StoryError('INVALID_RESULT', '规划提案必须保留全部既有人物姓名。')
