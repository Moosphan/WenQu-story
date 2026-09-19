"""Transactional task state machine, used unchanged by every transport."""
import hashlib
import json
import shutil
import time
from difflib import SequenceMatcher
from dataclasses import replace
from pathlib import Path

from .model_context import model_input
from .context_compiler import ContextCompiler, ContextPolicy
from .context_metrics import context_usage
from .task_context import ContextActions, prepare_lookup
from .memory_actions import MemoryActions
from .errors import StoryError
from .result_boundary import validate_output
from .targeted_revision import materialize
from .review_adjudication import SCHEMA as ADJUDICATION_SCHEMA, INSTRUCTION as ADJUDICATION_INSTRUCTION, materialize as adjudicate, output_schema as adjudication_schema
from .review_sources import resolve as resolve_review_sources, output_schema as review_source_schema
from .quality_policy import policy as quality_policy, review_count as quality_review_count, chapter_quality, INSTRUCTION as QUALITY_INSTRUCTION
from .memory_sources import resolve_sources, source_paragraphs
from .extraction_repair import checkpoint, merge as merge_extraction, REPAIR_SCHEMA
from .review_tracking import annotate_reviews
from .revision_response import feedback_items, validate_response, validate_verification, unresolved_hard_requirements
from .memory import canonical_memory, context_layers, index_chapter, promise_obligations, retrieve
from .prompts import READER_PROFILE_ORDER, instruction, reader_profile
from .schemas import SCHEMAS, validate, word_count, length_requirement
from .storage import Store, dumps, uid

LEASE_SECONDS = 900
MAX_TASK_OUTPUT_TOKENS = 12000
ARC_INTERVAL = 5
HUMAN_REVIEWER_TYPES = {
    'target_reader': '真人目标读者',
    'logic_reader': '真人逻辑读者',
    'editor': '编辑',
    'author': '作者自审',
}
PROJECT_PLATFORMS = {'general', 'fanqie', 'qidian', 'jinjiang', 'changpei', 'zhihu', 'other'}
PROJECT_LENGTH_BANDS = {'short', 'medium', 'long', 'serial'}
PROJECT_NARRATIVE_MODES = {'limited_third', 'first_person', 'omniscient', 'multi_pov', 'other'}


def _project_text(value, field, maximum):
    if not isinstance(value, str) or len(value.strip()) > maximum:
        raise StoryError('INVALID_REQUEST', f'{field}需要是 {maximum} 字以内的文本。')
    return value.strip()


def validate_project_metadata(metadata):
    if not isinstance(metadata, dict):
        raise StoryError('INVALID_REQUEST', '项目资料必须是对象。')
    allowed = {'platform', 'length_band', 'narrative_mode', 'genre_tags', 'target_reader', 'content_boundaries',
               'custom_notes', 'seed_characters', 'world_rules', 'reference_tags'}
    unknown = set(metadata) - allowed
    if unknown:
        raise StoryError('INVALID_REQUEST', '项目资料包含未知字段。', {'fields': sorted(unknown)})
    result = {
        'platform': metadata.get('platform', 'general'),
        'length_band': metadata.get('length_band', 'long'),
        'narrative_mode': metadata.get('narrative_mode', 'limited_third'),
        'target_reader': _project_text(metadata.get('target_reader', ''), '目标读者', 300),
        'content_boundaries': _project_text(metadata.get('content_boundaries', ''), '内容边界', 1200),
        'custom_notes': _project_text(metadata.get('custom_notes', ''), '创作备注', 5000),
    }
    if result['platform'] not in PROJECT_PLATFORMS:
        raise StoryError('INVALID_REQUEST', '目标平台无效。')
    if result['length_band'] not in PROJECT_LENGTH_BANDS:
        raise StoryError('INVALID_REQUEST', '篇幅类型无效。')
    if result['narrative_mode'] not in PROJECT_NARRATIVE_MODES:
        raise StoryError('INVALID_REQUEST', '叙事方式无效。')
    for field, maximum in (('genre_tags', 40), ('reference_tags', 60)):
        values = metadata.get(field, [])
        if not isinstance(values, list) or len(values) > 16:
            raise StoryError('INVALID_REQUEST', f'{field}最多 16 项。')
        if any(not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum for value in values):
            raise StoryError('INVALID_REQUEST', f'{field}包含无效标签。')
        result[field] = list(dict.fromkeys(value.strip() for value in values))
    for field, name_key, text_key, label in (('seed_characters', 'name', 'note', '人物种子'), ('world_rules', 'name', 'rule', '世界规则')):
        values = metadata.get(field, [])
        if not isinstance(values, list) or len(values) > 30:
            raise StoryError('INVALID_REQUEST', f'{label}最多 30 项。')
        normalized = []
        for value in values:
            if not isinstance(value, dict) or set(value) - ({name_key, 'role', text_key} if field == 'seed_characters' else {name_key, text_key}):
                raise StoryError('INVALID_REQUEST', f'{label}格式无效。')
            item = {name_key: _project_text(value.get(name_key, ''), label + '名称', 80), text_key: _project_text(value.get(text_key, ''), label + '说明', 1200)}
            if not item[name_key] or not item[text_key]:
                raise StoryError('INVALID_REQUEST', f'{label}名称和说明不能为空。')
            if field == 'seed_characters':
                item['role'] = _project_text(value.get('role', ''), '人物角色', 80)
            normalized.append(item)
        result[field] = normalized
    return result


def _author_material_instruction(project):
    """Make project intent useful to authors without promoting it to canon."""
    parts = ['project 是作者资料：平台、篇幅、题材、叙事方式、目标题材读者、内容边界与创作备注可作为本任务的创作约定和审稿目标。']
    if project.get('seed_characters'):
        parts.append('人物种子只是待采用的作者设想，只有在 brief、章节骨架或正文明确建立后才成为正史。')
    if project.get('world_rules'):
        parts.append('世界规则也是待采用设想，不能在没有正文或已确认骨架支持时当作已发生事实。')
    parts.append('project 中所有资料尚非正史；正文证据、已确认骨架、chapter_plan、pov_context 与 required_memory 优先。')
    return '\n' + ''.join(parts)


def _version_change(before, after):
    old = [part.strip() for part in before.split('\n\n') if part.strip()]
    new = [part.strip() for part in after.split('\n\n') if part.strip()]
    details = []
    counts = {'新增': 0, '删除': 0, '替换': 0}
    for tag, left_start, left_end, right_start, right_end in SequenceMatcher(None, old, new).get_opcodes():
        if tag == 'equal':
            continue
        kind = {'insert': '新增', 'delete': '删除', 'replace': '替换'}[tag]
        counts[kind] += max(left_end - left_start, right_end - right_start)
        if len(details) < 5:
            preview = lambda parts: '\n\n'.join(parts)[:240]
            details.append({'type': kind, 'before': preview(old[left_start:left_end]), 'after': preview(new[right_start:right_end])})
    summary = '、'.join(f'{name} {count} 段' for name, count in counts.items() if count)
    return {'summary': f'正文修订：{summary}', 'details': details}


def _context_manifest(book, role, through_chapter, memories=(), chapters=(), required=(), supplementary=(), pov_guard=None):
    canonical_sources = []
    seen = set()
    tiers = {}
    for tier, layer in (('required', required), ('supplementary', supplementary)):
        for memory in layer:
            source = memory.get('source') or {}
            tiers[(memory.get('kind'), memory.get('key'), source.get('chapter_number'), source.get('version_id'))] = (tier, memory.get('context_reason'))
    for memory in memories:
        source = memory.get('source') or {}
        identity = (memory.get('kind'), memory.get('key'), source.get('chapter_number'), source.get('version_id'))
        if identity in seen:
            continue
        seen.add(identity)
        tier, reason = tiers.get(identity, ('canonical', None))
        item = {
            'kind': memory.get('kind'), 'key': memory.get('key'),
            'chapter_number': source.get('chapter_number'), 'version_id': source.get('version_id'),
            'visibility': memory.get('visibility', 'reader'),
            'tier': tier,
        }
        if reason:
            item['reason'] = reason
        canonical_sources.append(item)
    result = {
        'role': role, 'through_chapter': through_chapter, 'book_revision': book['revision'],
        'canonical_sources': canonical_sources,
        'recent_chapter_sources': [
            {'chapter_number': chapter['chapter_number'], 'version_id': chapter['version_id'], 'title': chapter['title']}
            for chapter in chapters
        ],
    }
    if pov_guard is not None:
        result['pov_context'] = dict(pov_guard)
    return result


def _run(row):
    if row is None:
        return None
    result = dict(row)
    result['run_id'] = result.pop('id')
    for k in ('candidate', 'extraction', 'reviews'):
        result[k] = json.loads(result[k]) if result[k] else None
    return result


def _task(row):
    result = dict(row)
    result['task_id'] = result.pop('id')
    for k in ('input', 'output_schema'):
        result[k] = json.loads(result[k])
    result.pop('result', None)
    result.pop('response', None)
    return result


class StoryService(ContextActions, MemoryActions):
    def __init__(self, root):
        self.store = Store(root)

    def capabilities(self):
        return {'version': '0.1.0', 'contract_version': 1, 'modes': ['host', 'api'],
                'memory': ['versioned-facts', 'chinese-lexical-rag', 'reader-scope', 'evidence'],
                'vector_enabled': False, 'exports': ['txt', 'markdown', 'json', 'epub'],
                'durability': ['sqlite-wal', 'atomic-commit', 'lease-fencing', 'idempotency'],
                'reader_isolation': {'api': 'task-only-no-tools', 'host': 'requires-fresh-isolated-context'},
                'publishing': 'export-only', 'quality': 'requires-human-review',
                'human_feedback': ['version-bound', 'manual-entry'],
                'market_research': ['public-snapshot', 'source-item-evidence', 'author-idea-suggestions'],
                'max_task_output_tokens': MAX_TASK_OUTPUT_TOKENS}

    def market_sources(self):
        from .market import SOURCES
        with self.store.read() as conn:
            latest = {row['source_id']: row['created_at'] for row in conn.execute(
                'SELECT source_id,MAX(created_at) created_at FROM market_snapshots GROUP BY source_id')}
        return [{**{'source_id': source_id}, **source, 'last_collected_at': latest.get(source_id)} for source_id, source in SOURCES.items()]

    def market_snapshot(self, source_id, refresh=False, http_get=None):
        from .market import SOURCES, derive_signals, fetch_fanqie_rank, fetch_qidian_rank
        if source_id not in SOURCES or type(refresh) is not bool:
            raise StoryError('INVALID_REQUEST', '市场来源或刷新参数无效。')
        with self.store.read() as conn:
            row = conn.execute('SELECT snapshot FROM market_snapshots WHERE source_id=? ORDER BY created_at DESC LIMIT 1', (source_id,)).fetchone()
        cached = json.loads(row['snapshot']) if row else None
        if cached and cached.get('source_url') != SOURCES[source_id]['url']:
            cached = None
        if cached and not refresh and time.time() - cached['collected_at'] < 1800:
            return cached
        try:
            items = fetch_qidian_rank(http_get) if source_id == 'qidian_rank' else fetch_fanqie_rank(http_get, source_id)
        except StoryError as error:
            if cached:
                return {**cached, 'status': 'stale', 'error': error.message}
            return {'source_id': source_id, 'label': SOURCES[source_id]['label'], 'source_url': SOURCES[source_id]['url'],
                    'status': 'unavailable', 'collected_at': time.time(), 'items': [], 'signals': [], 'error': error.message}
        source = SOURCES[source_id]
        snapshot = {'source_id': source_id, 'label': source['label'], 'source_url': source['url'], 'status': 'fresh',
                    'collected_at': time.time(), 'items': items, 'signals': derive_signals(items), 'error': None, 'parser_version': 1}
        with self.store.write() as conn:
            conn.execute('INSERT INTO market_snapshots(id,source_id,snapshot,created_at) VALUES (?,?,?,?)',
                         (uid('market'), source_id, dumps(snapshot), snapshot['collected_at']))
            conn.execute('''DELETE FROM market_snapshots WHERE id IN (
                SELECT id FROM market_snapshots WHERE source_id=? ORDER BY created_at DESC LIMIT -1 OFFSET 12)''', (source_id,))
        return snapshot

    def market_ideas(self, source_ids, preferences=None, limit=3):
        from .market import SOURCES, create_idea_cards
        if (not isinstance(source_ids, list) or not source_ids or len(source_ids) > 8
                or any(not isinstance(source_id, str) or source_id not in SOURCES for source_id in source_ids)):
            raise StoryError('INVALID_REQUEST', '请选择 1–8 个已知市场来源。')
        if not isinstance(preferences, dict) or type(limit) is not int or not 1 <= limit <= 3:
            raise StoryError('INVALID_REQUEST', '选题偏好或灵感数量无效。')
        allowed = {'platform', 'genre_tags', 'target_reader', 'content_boundaries', 'request'}
        if set(preferences) - allowed:
            raise StoryError('INVALID_REQUEST', '选题偏好包含未知字段。')
        platform = preferences.get('platform', 'general')
        if platform not in PROJECT_PLATFORMS:
            raise StoryError('INVALID_REQUEST', '目标平台无效。')
        genre_tags = preferences.get('genre_tags', [])
        text_fields = ('target_reader', 'content_boundaries', 'request')
        if (not isinstance(genre_tags, list) or len(genre_tags) > 16 or any(not isinstance(tag, str) or not tag.strip() or len(tag) > 80 for tag in genre_tags)
                or any(not isinstance(preferences.get(field, ''), str) or len(preferences.get(field, '')) > 1200 for field in text_fields)):
            raise StoryError('INVALID_REQUEST', '选题偏好格式无效。')
        observations = []
        with self.store.read() as conn:
            for source_id in source_ids:
                row = conn.execute('SELECT snapshot FROM market_snapshots WHERE source_id=? ORDER BY created_at DESC LIMIT 1', (source_id,)).fetchone()
                if not row:
                    continue
                snapshot = json.loads(row['snapshot'])
                for signal in snapshot.get('signals', []):
                    observations.append({**signal, 'source_id': source_id, 'source_url': snapshot.get('source_url', '')})
        if not observations:
            raise StoryError('MARKET_NOT_READY', '所选来源还没有可验证的公开快照，请先读取或刷新来源。')
        selected = [item for item in observations if item['label'] in genre_tags] or observations
        normalized = {**preferences, 'genre_tags': [tag.strip() for tag in genre_tags if tag.strip()]}
        return {'ideas': create_idea_cards(selected, normalized, limit), 'observations': selected,
                'notice': '灵感卡来自公开条目文字与作者偏好，不代表平台趋势、签约条件或成绩预测。'}

    def project_proposals(self, request, project=None, limit=3):
        if not isinstance(request, str) or not request.strip() or len(request) > 20000:
            raise StoryError('INVALID_REQUEST', '请用一句话描述想写的故事。')
        if type(limit) is not int or not 1 <= limit <= 3:
            raise StoryError('INVALID_REQUEST', '开书方向数量应为 1–3。')
        author_material = validate_project_metadata(project or {})
        topic = author_material['genre_tags'][0] if author_material['genre_tags'] else '成长'
        premise = request.strip()
        variants = [
            ('代价型开局', f'《{topic}：每一次所得都有代价》', '主角的能力解决眼前困局，却把更难还的代价留在下一次选择里。', '让收益、代价和人物关系同时变化。'),
            ('关系型主线', f'《{topic}：与人同行的路》', '主角为一个具体的人或承诺行动，逐步卷入更大的冲突。', '让人物关系成为主线推进器，而非剧情装饰。'),
            ('目标型倒计时', f'《{topic}：期限之前》', '主角必须在明确期限前完成目标，失败会失去最在意的人或位置。', '每章都有可见目标、阻力和阶段回报。'),
        ]
        proposals = []
        for index, (structure, title, core, promise) in enumerate(variants[:limit], start=1):
            proposals.append({
                'proposal_id': f'proposal_{index}', 'title': title, 'structure': structure,
                'logline': premise, 'story_core': core, 'reader_promise': promise,
                'author_note': '这是开书前的可编辑方向，不会创建作品、正文、记忆或正史。',
                'is_canon': False, 'project': {**author_material, 'custom_notes': author_material['custom_notes'] or premise},
            })
        return {'proposals': proposals, 'notice': '选择后可继续编辑；只有创建作品并经过创作约定流程后，内容才会进入项目。'}

    @staticmethod
    def _idea_job(row):
        if row is None:
            raise StoryError('NOT_FOUND', '找不到这次灵感生成。')
        item = dict(row)
        item['job_id'] = item.pop('id')
        item['input'] = json.loads(item.pop('input'))
        item['ideas'] = json.loads(item.pop('result'))['ideas'] if item.get('result') else []
        return item

    def create_idea_job(self, request, genre_hint='', project=None):
        if not isinstance(request, str) or not request.strip() or len(request.strip()) > 20000:
            raise StoryError('INVALID_REQUEST', '灵感需求需要是 1–20000 字文本。')
        if not isinstance(genre_hint, str) or len(genre_hint.strip()) > 100:
            raise StoryError('INVALID_REQUEST', '题材提示无效。')
        payload = {'request': request.strip(), 'genre_hint': genre_hint.strip(), 'project': validate_project_metadata(project or {})}
        now = time.time(); job_id = uid('idea')
        with self.store.write() as conn:
            conn.execute('INSERT INTO idea_jobs(id,input,status,created_at,updated_at) VALUES (?,? ,\'queued\',?,?)', (job_id, dumps(payload), now, now))
            return self._idea_job(conn.execute('SELECT * FROM idea_jobs WHERE id=?', (job_id,)).fetchone())

    def idea_job(self, job_id):
        with self.store.read() as conn:
            return self._idea_job(conn.execute('SELECT * FROM idea_jobs WHERE id=?', (job_id,)).fetchone())

    def run_idea_job(self, job_id, provider):
        with self.store.write() as conn:
            job = self._idea_job(conn.execute('SELECT * FROM idea_jobs WHERE id=?', (job_id,)).fetchone())
            if job['status'] == 'complete':
                return job
            if job['status'] == 'running':
                return job
            conn.execute("UPDATE idea_jobs SET status='running',error=NULL,updated_at=? WHERE id=?", (time.time(), job_id))
        task = {'input': {**job['input'], 'instruction': '生成三条彼此差异明显的中文网文开书方向。每条必须有独特冲突、可执行的主角压力和读者承诺；不要使用林默、顾长生、沈青禾、陆沉等默认网文名，除非作者资料明确指定。只输出 JSON。'},
                'output_schema': {'type': 'object', 'properties': {'ideas': {'type': 'array', 'minItems': 3, 'maxItems': 3, 'items': {'type': 'object', 'properties': {key: {'type': 'string', 'minLength': 1, 'maxLength': 1000} for key in ('title', 'logline', 'story_core', 'reader_promise')}, 'required': ['title', 'logline', 'story_core', 'reader_promise'], 'additionalProperties': False}}}, 'required': ['ideas'], 'additionalProperties': False}}
        try:
            result = provider.generate(task)
            ideas = result.get('ideas') if isinstance(result, dict) else None
            if not isinstance(ideas, list) or len(ideas) != 3 or any(not isinstance(card, dict) or any(not isinstance(card.get(key), str) or not card[key].strip() or len(card[key]) > 1000 for key in ('title', 'logline', 'story_core', 'reader_promise')) for card in ideas):
                raise StoryError('INVALID_RESULT', '灵感执行器没有返回三条完整方向。')
            cards = [{key: card[key].strip() for key in ('title', 'logline', 'story_core', 'reader_promise')} | {'is_canon': False, 'project': job['input']['project']} for card in ideas]
            with self.store.write() as conn:
                conn.execute("UPDATE idea_jobs SET status='complete',result=?,updated_at=? WHERE id=?", (dumps({'ideas': cards}), time.time(), job_id))
                return self._idea_job(conn.execute('SELECT * FROM idea_jobs WHERE id=?', (job_id,)).fetchone())
        except Exception as error:
            message = error.message if isinstance(error, StoryError) else '灵感生成中断，请检查执行器后重试。'
            with self.store.write() as conn:
                conn.execute("UPDATE idea_jobs SET status='failed',error=?,updated_at=? WHERE id=?", (message, time.time(), job_id))
                return self._idea_job(conn.execute('SELECT * FROM idea_jobs WHERE id=?', (job_id,)).fetchone())

    def open_book(self, request, title='', genre='', chapter_count=40, target_words=2500, mode='host', project=None):
        if not isinstance(request, str) or not request.strip() or len(request) > 20000:
            raise StoryError('INVALID_REQUEST', '请用一句话描述要写的书。')
        if type(chapter_count) is not int or not 1 <= chapter_count <= 200:
            raise StoryError('INVALID_REQUEST', 'MVP 支持 1–200 章。')
        if type(target_words) is not int or not 50 <= target_words <= 6000:
            raise StoryError('INVALID_REQUEST', '每章目标字数应为 50–6000，正式中篇建议约 2500。')
        if mode not in ('host', 'api') or not isinstance(title, str) or len(title) > 100 or not isinstance(genre, str):
            raise StoryError('INVALID_REQUEST', '模式、书名或题材无效。')
        return self.store.create_book(request.strip(), title.strip() or '未命名作品',
                                      dict(genre=genre, chapter_count=chapter_count, target_words=target_words, mode=mode),
                                      validate_project_metadata(project or {}))

    def update_book_settings(self, book_id, title=None, chapter_count=None, target_words=None, expected_revision=None):
        if title is not None and (not isinstance(title, str) or not title.strip() or len(title.strip()) > 100):
            raise StoryError('INVALID_REQUEST', '书名应为 1–100 个字符。')
        for value, low, high in [(chapter_count, 1, 200), (target_words, 50, 6000)]:
            if value is not None and (type(value) is not int or not low <= value <= high):
                raise StoryError('INVALID_REQUEST', '章节数或字数超出支持范围。')
        with self.store.write(book_id, expected_revision=expected_revision) as conn:
            book = self.store.decode_book(conn.execute('SELECT * FROM books WHERE id=?', (book_id,)).fetchone())
            last = conn.execute('SELECT COALESCE(MAX(number),0) FROM chapters WHERE book_id=?', (book_id,)).fetchone()[0]
            run = self._latest_run(conn, book_id)
            active = run and run['status'] in ('running', 'paused', 'needs_attention', 'awaiting_author')
            minimum = max(last, run['chapter_number'] if active else 0)
            if chapter_count is not None and chapter_count < minimum:
                raise StoryError('INVALID_REQUEST', f'章节总数不能少于已保存正文或当前任务所在的第 {minimum} 章。')
            settings = dict(book['settings'])
            if chapter_count is not None:
                settings['chapter_count'] = chapter_count
            if target_words is not None:
                settings['target_words'] = target_words
            title = title.strip() if title is not None else book['title']
            brief = dict(book['brief']) if book['brief'] else None
            if brief:
                brief['title'] = title
            replan = settings['chapter_count'] != book['settings']['chapter_count'] and bool(book['plan'])
            if active:
                conn.execute("UPDATE tasks SET status='cancelled' WHERE run_id=? AND status='leased'", (run['run_id'],))
                conn.execute("UPDATE workbench_executions SET status='cancelled',finished_at=? WHERE run_id=? AND status='running'", (time.time(), run['run_id']))
                stage = 'outline' if replan else run['stage']
                conn.execute("UPDATE runs SET status=?,stage=?,end_chapter=?,reason=? WHERE id=?", ('awaiting_author' if run['status'] == 'awaiting_author' else 'paused', stage, min(run['end_chapter'], settings['chapter_count']), '作品设置已更新，继续写作将使用新设置。', run['run_id']))
            conn.execute('UPDATE books SET title=?,config=?,brief=?,ending=NULL,revision=revision+1 WHERE id=?',
                         (title, dumps(settings), dumps(brief) if brief else None, book_id))
            if replan and not active:
                conn.execute("UPDATE books SET status='draft' WHERE id=?", (book_id,))
            self.store.event(conn, book_id, 'book_settings_updated', {'settings': settings, 'replan': replan})
            return {'book_id': book_id, 'title': title, 'settings': settings, 'revision': book['revision'] + 1, 'replan': replan, 'run_paused': bool(active)}

    def project_metadata(self, book_id):
        return self.get_book(book_id)['project']

    def update_project_metadata(self, book_id, metadata, expected_revision=None):
        if expected_revision is not None and (type(expected_revision) is not int or expected_revision < 0):
            raise StoryError('INVALID_REQUEST', '作品版本必须是非负整数。')
        if not isinstance(metadata, dict):
            raise StoryError('INVALID_REQUEST', '项目资料必须是对象。')
        with self.store.write(book_id, expected_revision=expected_revision) as conn:
            book = self.store.decode_book(conn.execute('SELECT * FROM books WHERE id=?', (book_id,)).fetchone())
            project = validate_project_metadata({**book['project'], **metadata})
            conn.execute('UPDATE books SET project=?,revision=revision+1 WHERE id=?', (dumps(project), book_id))
            self.store.event(conn, book_id, 'project_metadata_updated', {'fields': sorted(metadata)}, None)
            revision = conn.execute('SELECT revision FROM books WHERE id=?', (book_id,)).fetchone()[0]
            return {'book_id': book_id, 'project': project, 'revision': revision}

    def _rename_story_characters(self, conn, book, brief, plan):
        """Rename current canon atomically; immutable text/task history is retained."""
        import re
        old_chars = (book.get('brief') or {}).get('characters', [])
        new_chars = brief.get('characters', [])
        old_names = {c['name'] for c in old_chars}
        new_names = {c['name'] for c in new_chars}
        removed = [c for c in old_chars if c['name'] not in new_names]
        added = [c for c in new_chars if c['name'] not in old_names]
        renames = {}
        for old in removed:
            matches = [c for c in added if all(c.get(k) == v for k, v in old.items() if k != 'name')]
            if len(matches) == 1 and matches[0]['name'] not in renames.values():
                renames[old['name']] = matches[0]['name']
        if removed and added and len(renames) != len(removed):
            raise StoryError('AMBIGUOUS_RENAME', '无法确定人物改名对应关系。请先仅修改人物姓名并保存，再调整其他人物设定。')
        if not renames:
            return brief, plan, {}, 0
        names = set(renames) | set(renames.values())
        pattern = re.compile('|'.join(re.escape(n) for n in sorted(names, key=len, reverse=True)))
        def replace(value):
            if isinstance(value, str):
                return pattern.sub(lambda m: renames.get(m.group(), m.group()), value)
            if isinstance(value, list):
                return [replace(v) for v in value]
            if isinstance(value, dict):
                return {k: replace(v) for k, v in value.items()}
            return value
        count = 0
        for row in conn.execute('SELECT v.* FROM chapters c JOIN chapter_versions v ON c.version_id=v.id WHERE c.book_id=?', (book['book_id'],)).fetchall():
            title, body = replace(row['title']), replace(row['body'])
            # Even a chapter without a name mention may have named memory records.
            version = uid('version')
            conn.execute('INSERT INTO chapter_versions VALUES (?,?,?,?,?,?)', (version, book['book_id'], row['number'], title, body, time.time()))
            conn.execute('UPDATE chapters SET version_id=? WHERE book_id=? AND number=?', (version, book['book_id'], row['number']))
            for table, fields in [('memories', ('key', 'value', 'evidence', 'data')), ('chunks', ('text',))]:
                for item in conn.execute(f'SELECT * FROM {table} WHERE book_id=? AND version_id=?', (book['book_id'], row['id'])).fetchall():
                    values = [dumps(replace(json.loads(item[f]))) if f == 'data' else replace(item[f]) for f in fields]
                    conn.execute(f"UPDATE {table} SET version_id=?," + ','.join(f'{f}=?' for f in fields) + ' WHERE id=? AND book_id=?', (version, *values, item['id'], book['book_id']))
            self.store.event(conn, book['book_id'], 'character_rename_applied', {'chapter_number': row['number'], 'version_id': version, 'previous_version_id': row['id'], 'renames': renames})
            count += 1
        # Retain the current candidate under the new vocabulary, discard stale extraction.
        run = self._latest_run(conn, book['book_id'])
        if run and run.get('candidate'):
            conn.execute('UPDATE runs SET candidate=?,extraction=NULL WHERE id=?', (dumps(replace(run['candidate'])), run['run_id']))
        conn.execute('UPDATE books SET request=?,project=? WHERE id=?', (replace(book['request']), dumps(replace(book['project'])), book['book_id']))
        return replace(brief), replace(plan), renames, count

    def update_story_bible(self, book_id, brief, plan, expected_revision=None, *, settings=None, apply_character_renames=False):
        if expected_revision is not None and (type(expected_revision) is not int or expected_revision < 0):
            raise StoryError('INVALID_REQUEST', '作品版本必须是非负整数。')
        with self.store.write(book_id, expected_revision=expected_revision) as conn:
            book = self.store.decode_book(conn.execute('SELECT * FROM books WHERE id=?', (book_id,)).fetchone())
            if settings is not None and (not isinstance(settings, dict) or set(settings) - {'chapter_count', 'target_words'}):
                raise StoryError('INVALID_REQUEST', '作品设置仅支持章节总数和后续章节字数。')
            if type(apply_character_renames) is not bool:
                raise StoryError('INVALID_REQUEST', '历史人物改名选项必须为布尔值。')
            updated_settings = {**book['settings'], **(settings or {})}
            if type(updated_settings['chapter_count']) is not int or not book['settings']['chapter_count'] <= updated_settings['chapter_count'] <= 200:
                raise StoryError('INVALID_REQUEST', '章节总数只能扩展，且不能超过 200 章。')
            if type(updated_settings['target_words']) is not int or not 50 <= updated_settings['target_words'] <= 6000:
                raise StoryError('INVALID_REQUEST', '后续章节字数须为 50–6000 的整数。')
            book = {**book, 'settings': updated_settings}
            validate('brief', brief, book)
            validate('outline', plan, book)
            renames, renamed_chapters = {}, 0
            if apply_character_renames:
                brief, plan, renames, renamed_chapters = self._rename_story_characters(conn, book, brief, plan)
            run = self._latest_run(conn, book_id)
            run_cancelled = bool(run and run['status'] in ('running', 'paused', 'needs_attention', 'awaiting_author'))
            if run_cancelled:
                conn.execute("UPDATE runs SET status='cancelled',reason='故事约定或章节骨架已由作者更新，需要使用新上下文重新开始。' WHERE id=?", (run['run_id'],))
                conn.execute("UPDATE tasks SET status='cancelled' WHERE run_id=? AND status='leased'", (run['run_id'],))
                conn.execute("UPDATE workbench_executions SET status='cancelled',finished_at=? WHERE run_id=? AND status='running'", (time.time(), run['run_id']))
            conn.execute("UPDATE books SET title=?,brief=?,plan=?,config=?,status='draft',revision=revision+1,ending=NULL WHERE id=?",
                         (brief['title'], dumps(brief), dumps(plan), dumps(updated_settings), book_id))
            self.store.event(conn, book_id, 'story_bible_updated', {'revision_from': book['revision'], 'cancelled_run': run_cancelled, 'settings': updated_settings})
            saved = self.store.decode_book(conn.execute('SELECT * FROM books WHERE id=?', (book_id,)).fetchone())
            return {'book_id': book_id, 'title': saved['title'], 'brief': saved['brief'], 'plan': saved['plan'],
                    'renamed_characters': renames, 'renamed_chapters': renamed_chapters,
                    'settings': saved['settings'], 'revision': saved['revision'], 'run_cancelled': run_cancelled}

    def list_books(self, kind='all'):
        if kind not in ('all', 'user', 'sample', 'archived'):
            raise StoryError('INVALID_REQUEST', '作品筛选只能是 all、user、sample 或 archived。')
        with self.store.read() as conn:
            query = 'SELECT * FROM books WHERE id NOT IN (SELECT book_id FROM book_trash) AND ' + ("kind != 'archived'" if kind == 'all' else 'kind=?') + ' ORDER BY created_at DESC'
            rows = conn.execute(query, () if kind == 'all' else (kind,))
            return [self.store.decode_book(r) for r in rows]

    def list_trash(self):
        with self.store.read() as conn:
            rows = conn.execute('SELECT b.*,t.deleted_at FROM books b JOIN book_trash t ON t.book_id=b.id ORDER BY t.deleted_at DESC')
            return [{**self.store.decode_book(r), 'deleted_at': r['deleted_at']} for r in rows]

    def trash_book(self, book_id):
        with self.store.write(book_id, allow_trash=True) as conn:
            if conn.execute("SELECT 1 FROM runs WHERE book_id=? AND status='running'", (book_id,)).fetchone() or conn.execute("SELECT 1 FROM workbench_executions WHERE book_id=? AND status='running'", (book_id,)).fetchone():
                raise StoryError('RUN_ACTIVE', '请先暂停写作并等待当前执行结束，再移入回收站。')
            conn.execute('INSERT OR IGNORE INTO book_trash VALUES (?,?)', (book_id, time.time()))
            return {'book_id': book_id, 'trashed': True}

    def restore_book(self, book_id):
        with self.store.write(book_id, allow_trash=True) as conn:
            conn.execute('DELETE FROM book_trash WHERE book_id=?', (book_id,))
            return {'book_id': book_id, 'restored': True}

    def purge_book(self, book_id):
        with self.store.write(book_id, allow_trash=True) as conn:
            if not conn.execute('SELECT 1 FROM book_trash WHERE book_id=?', (book_id,)).fetchone():
                raise StoryError('DELETE_FORBIDDEN', '只能彻底删除回收站中的作品。')
            for table in ('workbench_executions', 'human_reviews', 'tasks', 'memories', 'chunks', 'chapters', 'chapter_versions', 'runs', 'events', 'exports', 'book_trash'):
                conn.execute(f'DELETE FROM {table} WHERE book_id=?', (book_id,))
            conn.execute('DELETE FROM books WHERE id=?', (book_id,))
        for category in ('tts-cache', 'exports'):
            parent = (self.store.root / category).resolve()
            folder = parent / book_id
            if folder.is_dir() and not folder.is_symlink() and folder.resolve().parent == parent:
                shutil.rmtree(folder)
        return {'book_id': book_id, 'deleted': True}

    def set_book_kind(self, book_id, kind):
        if kind not in ('user', 'sample', 'archived'):
            raise StoryError('INVALID_REQUEST', '作品类型只能是 user、sample 或 archived。')
        with self.store.write(book_id) as conn:
            current = conn.execute('SELECT kind FROM books WHERE id=?', (book_id,)).fetchone()['kind']
            if kind == 'archived' and conn.execute("SELECT 1 FROM runs WHERE book_id=? AND status IN ('running','paused','needs_attention','awaiting_author')", (book_id,)).fetchone():
                raise StoryError('RUN_ACTIVE', '请先结束进行中的任务，再归档作品。')
            conn.execute('UPDATE books SET kind=?,revision=revision+1 WHERE id=?', (kind, book_id))
            event = 'project_archived' if kind == 'archived' else ('project_restored' if current == 'archived' else 'book_kind_changed')
            self.store.event(conn, book_id, event, {'kind': kind})
            return self.store.decode_book(conn.execute('SELECT * FROM books WHERE id=?', (book_id,)).fetchone())

    def delete_sample(self, book_id):
        with self.store.write(book_id) as conn:
            row = conn.execute('SELECT kind FROM books WHERE id=?', (book_id,)).fetchone()
            if row['kind'] != 'sample':
                raise StoryError('DELETE_FORBIDDEN', '只能删除标记为测试样例的作品。')
            if conn.execute("SELECT 1 FROM runs WHERE book_id=? AND status IN ('running','paused','needs_attention','awaiting_author')", (book_id,)).fetchone():
                raise StoryError('RUN_ACTIVE', '请先结束该样例的进行中任务。')
            for table in ('workbench_executions', 'human_reviews', 'tasks', 'memories', 'chunks', 'chapters', 'chapter_versions', 'runs', 'events', 'exports'):
                conn.execute(f'DELETE FROM {table} WHERE book_id=?', (book_id,))
            conn.execute('DELETE FROM books WHERE id=?', (book_id,))
            return {'book_id': book_id, 'deleted': True}

    def get_book(self, book_id):
        with self.store.read() as conn:
            book = self.store.decode_book(conn.execute('SELECT * FROM books WHERE id=?', (book_id,)).fetchone())
            book['chapters'] = [dict(r) for r in conn.execute('''SELECT c.number chapter_number,c.status,c.version_id,v.title,v.body
                FROM chapters c JOIN chapter_versions v ON v.id=c.version_id WHERE c.book_id=? ORDER BY c.number''', (book_id,))]
            quality = chapter_quality(conn, book_id)
            for ch in book['chapters']:
                ch['quality'] = quality.get(ch['version_id'], {})
                ch['word_count'] = word_count(ch['body'])
            return book

    def _latest_run(self, conn, book_id):
        run = _run(conn.execute('SELECT * FROM runs WHERE book_id=? ORDER BY created_at DESC LIMIT 1', (book_id,)).fetchone())
        if run:
            run['quality_policy'] = {**quality_policy(conn,run),'review_count':min(3,quality_review_count(conn,run)),'max_reviews':3}
        return run

    def import_chapters(self, book_id, chapters, expected_revision=None):
        if not isinstance(chapters, list) or not 1 <= len(chapters) <= 200:
            raise StoryError('INVALID_REQUEST', '请提交 1–200 章正文。')
        with self.store.write(book_id, expected_revision) as conn:
            book = self.store.decode_book(conn.execute('SELECT * FROM books WHERE id=?', (book_id,)).fetchone())
            if conn.execute("SELECT id FROM runs WHERE book_id=? AND status IN ('running','paused','needs_attention','awaiting_author')", (book_id,)).fetchone():
                raise StoryError('RUN_ACTIVE', '请先结束当前任务，再导入正文。')
            existing = {row['number']: dict(row) for row in conn.execute(
                'SELECT c.number,v.title,v.body FROM chapters c JOIN chapter_versions v ON v.id=c.version_id WHERE c.book_id=?', (book_id,))}
            incoming = {}
            for chapter in chapters:
                if not isinstance(chapter, dict) or set(chapter) != {'chapter_number', 'title', 'body'}:
                    raise StoryError('INVALID_REQUEST', '每章只应包含 chapter_number、title、body。')
                number = chapter['chapter_number']
                if type(number) is not int or not 1 <= number <= book['settings']['chapter_count'] or number in incoming:
                    raise StoryError('INVALID_REQUEST', '章节编号重复或超出全书范围。')
                if not isinstance(chapter['title'], str) or not chapter['title'].strip() or len(chapter['title']) > 200:
                    raise StoryError('INVALID_REQUEST', '章节标题无效。')
                if not isinstance(chapter['body'], str) or not chapter['body'].strip() or len(chapter['body'].encode()) > 100000:
                    raise StoryError('INVALID_REQUEST', '章节正文为空或超过 100 KB。')
                incoming[number] = chapter
                if number in existing and any(chapter[key] != existing[number][key] for key in ('title', 'body')):
                    raise StoryError('CHAPTER_EXISTS', '该章已有不同正文，请使用修订流程。', {'chapter_number': number})
            numbers = sorted(set(existing) | set(incoming))
            if numbers != list(range(1, max(numbers) + 1)):
                raise StoryError('INVALID_REQUEST', '导入后的章节必须从第一章连续编号。')
            added = []
            for number, chapter in sorted(incoming.items()):
                if number in existing:
                    continue
                version = uid('version')
                conn.execute('INSERT INTO chapter_versions VALUES (?,?,?,?,?,?)',
                             (version, book_id, number, chapter['title'], chapter['body'], time.time()))
                conn.execute("INSERT INTO chapters VALUES (?,?,?,'imported')", (book_id, number, version))
                added.append({'chapter_number': number, 'version_id': version})
            if added:
                conn.execute("UPDATE books SET revision=revision+1,status='draft',ending=NULL WHERE id=?", (book_id,))
                self.store.event(conn, book_id, 'chapters_imported', {'chapters': added, 'reviewed': False})
            revision = conn.execute('SELECT revision FROM books WHERE id=?', (book_id,)).fetchone()[0]
            return {'book_id': book_id, 'imported': len(added), 'revision': revision, 'chapters': added, 'reviewed': False}

    def _insert_run(self, conn, book, chapter_limit, max_steps, max_revisions, budget_tokens, *, candidate=None, chapter=None, review_mode='bounded'):
        if review_mode not in ('bounded','legacy'):
            raise StoryError('INVALID_REQUEST','审稿模式无效。')
        for name, value, low, high in [('max_steps',max_steps,1,10000),('max_revisions',max_revisions,0,10),('budget_tokens',budget_tokens,1,100000000)]:
            if type(value) is not int or not low <= value <= high:
                raise StoryError('INVALID_REQUEST', f'{name} 超出支持范围。')
        active = conn.execute("SELECT id FROM runs WHERE book_id=? AND status IN ('running','paused','needs_attention','awaiting_author')", (book['book_id'],)).fetchone()
        if active:
            raise StoryError('RUN_ACTIVE', '已有任务，请继续、暂停或取消当前任务。')
        total = book['settings']['chapter_count']
        if chapter_limit is not None and (type(chapter_limit) is not int or chapter_limit < 1 or chapter_limit > total):
            raise StoryError('INVALID_REQUEST', 'chapter_limit 无效。')
        first = conn.execute("SELECT number FROM chapters WHERE book_id=? AND status!='committed' ORDER BY number LIMIT 1", (book['book_id'],)).fetchone()
        count = conn.execute('SELECT COUNT(*) FROM chapters WHERE book_id=?', (book['book_id'],)).fetchone()[0]
        start = chapter or (first[0] if first else count + 1)
        end = total if chapter_limit is None else min(total, start + chapter_limit - 1)
        stage = 'brief' if not book['brief'] else 'outline' if not book['plan'] else 'extract' if candidate else 'ending' if start > total else 'draft'
        if book['brief'] and book['plan'] and len(book['plan']['chapters']) != total:
            stage = 'outline'
        repair = bool(chapter or first)
        if repair and not candidate and start <= total:
            ch = conn.execute('SELECT v.title,v.body FROM chapters c JOIN chapter_versions v ON v.id=c.version_id WHERE c.book_id=? AND c.number=?', (book['book_id'],start)).fetchone()
            if ch:
                candidate = dict(ch)
                if book['brief'] and book['plan']:
                    stage = 'extract'
        run_id = uid('run')
        conn.execute('''INSERT INTO runs(id,book_id,status,stage,chapter_number,end_chapter,max_steps,max_revisions,budget_tokens,candidate,repair,created_at)
                     VALUES (?,?,'running',?,?,?,?,?,?,?,?,?)''',
                     (run_id,book['book_id'],stage,start,end,max_steps,max_revisions,budget_tokens,dumps(candidate) if candidate else None,int(repair),time.time()))
        conn.execute("UPDATE books SET status='writing',ending=NULL WHERE id=?", (book['book_id'],))
        self.store.event(conn,book['book_id'],'run_started',{'run_id':run_id,'review_mode':review_mode,'single':end == start},run_id)
        return self._latest_run(conn,book['book_id'])

    def start_run(self, book_id, chapter_limit=None, max_steps=500, max_revisions=2, budget_tokens=2000000, review_mode='bounded'):
        with self.store.write(book_id) as conn:
            book = self.store.decode_book(conn.execute('SELECT * FROM books WHERE id=?',(book_id,)).fetchone())
            if book['book_kind'] == 'archived':
                raise StoryError('ARCHIVED_PROJECT', '已归档作品请先恢复到“我的作品”或“测试样例”，再继续写作。')
            return self._insert_run(conn,book,chapter_limit,max_steps,max_revisions,budget_tokens,review_mode=review_mode)

    def _input(self, conn, book, run):
        stage, number = run['stage'], run['chapter_number']
        candidate = run['candidate']
        common = {'instruction': instruction(stage), 'chapter_number': number}
        prior = [dict(r) for r in conn.execute('''SELECT c.number chapter_number,c.version_id,v.title,v.body FROM chapters c JOIN chapter_versions v ON v.id=c.version_id
            WHERE c.book_id=? AND c.number<? AND c.status='committed' ORDER BY c.number''', (book['book_id'], number))]
        # Reader never gets plans, hidden facts, author reviews or future chunks.
        if stage == 'reader':
            completed = {review.get('reader_profile') for review in run['reviews'] or [] if review.get('stage') == 'reader'}
            profile_id = next((profile for profile in READER_PROFILE_ORDER if profile not in completed), None)
            if profile_id is None:
                raise StoryError('INVALID_STATE', '当前候选稿的读者审稿已完成。')
            profile = reader_profile(profile_id)
            history = [{'chapter_number': c['chapter_number'], 'title': c['title'], 'body': c['body']} for c in prior[-3:]]
            public = [m for m in canonical_memory(conn,book['book_id'],number-1) if m.get('visibility','reader')=='reader' and m['kind'] in ('summary','emotion','relationship')]
            return {**common, 'instruction': instruction('reader', profile_id), 'reader_profile': profile,
                    'candidate':candidate,'reader_history':history,'reader_memory':public,
                    'read_boundary':number,
                    'context_manifest': _context_manifest(book, 'reader', number, public, prior[-3:]),
                    'context_limit_note':'提供近三章正文及公开记忆，非人类完整阅读实验。'}
        data = {**common, 'request':book['request'],'title':book['title'],'settings':book['settings'], 'project': book['project']}
        data['instruction'] += _author_material_instruction(book['project'])
        if stage in ('brief', 'outline'):
            data['imported_chapters'] = [dict(row) for row in conn.execute(
                "SELECT c.number chapter_number,v.title,v.body FROM chapters c JOIN chapter_versions v ON v.id=c.version_id WHERE c.book_id=? AND c.status='imported' ORDER BY c.number", (book['book_id'],))]
            if data['imported_chapters']:
                if candidate:
                    for imported in data['imported_chapters']:
                        if imported['chapter_number'] == number:
                            imported.update(candidate)
                data['instruction'] += '\n这是已有稿导入。根据现有正文逆向整理设定和前章骨架；发现矛盾应保留供审稿处理，不要假装已解决。后续规划不得把未审导入稿当成已确认事实。'
        if stage == 'brief':
            data['context_manifest'] = _context_manifest(book, 'author', 0)
            return data
        data['brief'] = book['brief']
        if stage == 'outline':
            if book['plan']:
                data['previous_plan'] = book['plan']
                data['instruction'] += '\n这是中途调整篇幅。保留已写章节的骨架与已发生事实，扩展或收束后续世界观、卷纲、章纲和伏笔；不要重写已完成正文。'
            data['context_manifest'] = _context_manifest(book, 'author', 0)
            return data
        data['chapter_plan'] = next((c for c in book['plan']['chapters'] if c['number']==number), None)
        if stage in ('draft', 'revise', 'arc', 'ending'):
            structure = {key: book['plan'][key] for key in ('payoff_design', 'climax', 'conflicts', 'reversals', 'joy_points', 'book_climaxes', 'conflicts_reversals') if book['plan'].get(key)}
            volumes = [volume for volume in book['plan'].get('volumes', []) if
                       volume.get('range', [volume.get('start_chapter'), volume.get('end_chapter')])[0] <= number <=
                       volume.get('range', [volume.get('start_chapter'), volume.get('end_chapter')])[1]]
            if volumes:
                structure['volumes'] = volumes
            if structure:
                data['story_structure'] = structure
                data['instruction'] += '\nstory_structure 是创作计划，不是已发生事实；只推进本章目标，不能提前兑现后续高潮或把伏笔计划当成记忆。'
        data['arc_window'] = [c for c in book['plan']['chapters'] if abs(c['number']-number)<=2]
        data['planned_promises'] = book['plan']['promises']
        memory_boundary = number if stage in ('ending', 'arc') else number - 1
        memories = canonical_memory(conn,book['book_id'],memory_boundary)
        data['promise_obligations'] = promise_obligations(memories, book['plan'], number)
        data['context_selection'] = {'settled_promise_ids': [m['key'] for m in memories if m.get('kind') == 'promise' and m.get('status') in ('paid', 'waived')]}
        layers = context_layers(memories, data['chapter_plan'] or {}, number)
        # Serialize the authoritative layer once; duplicate aliases inflate requests.
        data['required_memory'] = layers['required']
        data['supplementary_memory'] = layers['supplementary']
        data['context_queries'] = layers['queries']
        data['pov_context'] = layers['pov_guard']
        data['recent_chapters'] = prior[-2:]
        data['context_manifest'] = _context_manifest(book, 'author', memory_boundary,
                                                      [*layers['required'], *layers['supplementary']], prior[-2:],
                                                      layers['required'], layers['supplementary'], layers['pov_guard'])
        if candidate:
            data['candidate'] = candidate
        if stage == 'continuity':
            data['source_paragraphs'] = source_paragraphs(candidate['body'])
            data['instruction'] += '\n每个问题用 evidence_paragraph 选择当前 source_paragraphs 的编号，不输出 evidence。系统填入该段原文。explanation 中说明与既有规则的对照，不能把推测或不存在的句子当成引用。'
            last_review = (run['reviews'] or [{}])[-1]
            if last_review.get('adjudication_pending'):
                data['review_adjudication'] = last_review
                data['adjudication_items'] = [{'index': i, **issue} for i, issue in enumerate(last_review['issues']) if issue['severity'] in ('blocker','major')]
                data['instruction'] = ADJUDICATION_INSTRUCTION
                data['source_paragraphs'] = source_paragraphs(candidate['body'])
            data['extraction'] = run['extraction']
            last_draft = conn.execute('''SELECT id,stage,input,result,base_revision FROM tasks
                WHERE book_id=? AND run_id=? AND chapter_number=? AND status='submitted'
                AND stage IN ('draft','revise') ORDER BY created_at DESC,rowid DESC LIMIT 1''',
                (book['book_id'],run['run_id'],number)).fetchone()
            if last_draft and last_draft['stage'] == 'revise' and last_draft['base_revision'] == book['revision']:
                output = json.loads(last_draft['result'])
                if candidate == {key: output[key] for key in ('title', 'body')}:
                    inputs = json.loads(last_draft['input'])
                    data['revision_check'] = {'task_id': last_draft['id'],
                        'feedback_items': inputs.get('feedback_items', []),
                        'authoring_claims': []}

        if stage == 'continuity' and quality_policy(conn,run)['mode'] == 'bounded':
            data.pop('review_adjudication',None)
            data.pop('adjudication_items',None)
            data['instruction'] = QUALITY_INSTRUCTION + '\n每条问题以 evidence_paragraph 引用 source_paragraphs 的当前正文段落。'
            data['quality_policy'] = {'id':'quality-v1','max_reviews':3,'review_number':min(3,quality_review_count(conn,run)+1)}
            row = conn.execute("SELECT result FROM tasks WHERE run_id=? AND chapter_number=? AND stage='continuity' AND status='submitted' ORDER BY created_at DESC,rowid DESC LIMIT 1", (run['run_id'],number)).fetchone()
            data['previous_quality_review'] = json.loads(row[0]) if row else None
        if stage == 'extract':
            previous = conn.execute('''SELECT id,input,status FROM tasks WHERE book_id=? AND run_id=?
                AND chapter_number=? AND stage='extract' ORDER BY created_at DESC,rowid DESC LIMIT 1''',
                (book['book_id'],run['run_id'],number)).fetchone()
            if previous and previous['status'] != 'submitted' and json.loads(previous['input']).get('candidate') == candidate:
                if json.loads(previous['input']).get('extraction_repair'):
                    data['extraction_repair'] = json.loads(previous['input'])['extraction_repair']
                for row in conn.execute("SELECT payload FROM events WHERE book_id=? AND kind='extraction_rejected' ORDER BY seq DESC LIMIT 30", (book['book_id'],)):
                    saved = json.loads(row['payload'])
                    if saved['task_id'] == previous['id']:
                        data['extraction_repair'] = saved['checkpoint']
                        break
                for row in conn.execute("SELECT payload FROM events WHERE book_id=? AND kind='worker_failed' ORDER BY seq DESC LIMIT 30", (book['book_id'],)):
                    failure = json.loads(row['payload'])
                    if failure.get('task_id') == previous['id'] and failure.get('code') in ('INVALID_RESULT','INVALID_EVIDENCE','INVALID_SCOPE','INVALID_EVIDENCE_SOURCE'):
                        data['validation_feedback'] = {key: failure[key] for key in ('task_id','code','message','details') if key in failure}
                        break
            feedback = [review for review in run['reviews'] or [] if review.get('repair_target') == 'memory']
            if feedback:
                data['extraction_feedback'] = feedback[-1]
        if stage == 'revise':
            data['reviews'] = run['reviews']
            data['feedback_items'] = feedback_items(run['reviews'])
        if stage in ('draft', 'revise'):
            data['length_requirement'] = length_requirement(book['settings']['target_words'])
            if candidate:
                data['length_requirement']['current_count'] = word_count(candidate['body'])
            data['instruction'] += '\n按 length_requirement.target 写足本章，不以 min 为写作目标。计数不含标点空白；返修后的完整正文（包括应用 patches 后）也须满足范围。原稿不足时，补足本章目标内的尝试、阻力、对话交锋、结果及情绪余波；不能靠重复解释、回顾或无关支线凑字数。删除有问题的说明后保留必要场景，不把去AI味理解为持续缩短正文。length_feedback 是上次实际校验结果，重试须结合它调整正文，不能重复提交同样的删改。'
            previous = conn.execute("SELECT id,input,status,base_revision FROM tasks WHERE book_id=? AND run_id=? AND chapter_number=? AND stage=? AND status!='lookup' ORDER BY created_at DESC,rowid DESC LIMIT 1",
                                    (book['book_id'], run['run_id'], number, stage)).fetchone()
            if previous and previous['status'] != 'submitted' and json.loads(previous['input']).get('candidate') == candidate:
                for row in conn.execute("SELECT payload FROM events WHERE book_id=? AND kind IN ('task_result_rejected','worker_failed') ORDER BY seq DESC", (book['book_id'],)):
                    failure = json.loads(row['payload'])
                    if failure.get('task_id') == previous['id'] and failure.get('code') == 'WORD_COUNT' and failure.get('details'):
                        data['length_feedback'] = {'task_id': previous['id'], 'count': failure['details'].get('count'), **length_requirement(book['settings']['target_words'])}
                        break
            if stage == 'draft' and previous and previous['status'] != 'submitted' and previous['base_revision'] == book['revision']:
                for row in conn.execute("SELECT payload FROM events WHERE book_id=? AND run_id=? AND kind='task_result_rejected' ORDER BY seq DESC", (book['book_id'], run['run_id'])):
                    failure = json.loads(row['payload'])
                    if failure.get('task_id') != previous['id'] or failure.get('code') != 'WORD_COUNT':
                        continue
                    raw = failure.get('raw_result')
                    if isinstance(raw, dict) and isinstance(raw.get('title'), str) and isinstance(raw.get('body'), str) and raw['body'].strip():
                        count = word_count(raw['body'])
                        bounds = data['length_requirement']
                        data['length_repair_source'] = {'title': raw['title'], 'body': raw['body']}
                        data['expansion_requirement'] = {'source_words': count,
                            'minimum_additional_words': max(0, bounds['min'] - count),
                            'target_additional_words': max(0, bounds['target'] - count)}
                        direction = '在现有场景内部补足行动、阻力和对话，保留事件顺序和结局' if count < bounds['min'] else '压缩重复解释与铺垫，保留事件顺序和结局'
                        data['instruction'] += f"\n本次是初稿长度修复，不是重新起稿。length_repair_source 是上次保存的完整正文，共 {count} 字。以它为底稿，{direction}，达到 {bounds['target']} 字附近。必须返回完整 title/body，不只返回新增段落、不返回修改建议，不得照原样重复提交。"
                    break
        if stage == 'revise' and candidate:
            if previous and previous['status'] != 'submitted' and json.loads(previous['input']).get('candidate') == candidate:
                data['revision_mode'] = 'full_body'
                data['instruction'] = instruction('revise', revision_mode='full_body') + _author_material_instruction(book['project'])
            bounds = data['length_requirement']
            failed_count = data.get('length_feedback', {}).get('count')
            if isinstance(failed_count, int) and failed_count > bounds['max']:
                data['instruction'] += f"\n本次是超长返修纠正：上次实际 {failed_count} 字，超过上限 {bounds['max']}。本次以 {bounds['target']} 字为目标，删除重复铺垫和解释，保留本章事件、关键对话和结局，不新增场景。必须输出整章。上限不是写作目标。"
            if bounds['current_count'] < bounds['min'] or (isinstance(failed_count, int) and failed_count < bounds['min']):
                data['revision_mode'] = 'expand_full_body'
                data['expansion_requirement'] = {'source_words': bounds['current_count'],
                    'minimum_additional_words': max(0, bounds['min'] - bounds['current_count']),
                    'target_additional_words': max(0, bounds['target'] - bounds['current_count'])}
                data['instruction'] = instruction('revise', revision_mode='expand_full_body') + _author_material_instruction(book['project'])
        if stage == 'ending':
            data['plan'] = book['plan']
            data['committed_chapters'] = [dict(r) for r in conn.execute('''SELECT c.number,c.version_id,v.title FROM chapters c JOIN chapter_versions v ON v.id=c.version_id
                WHERE c.book_id=? AND c.status='committed' ORDER BY c.number''',(book['book_id'],))]
        if stage == 'arc':
            start = max(1, number - ARC_INTERVAL + 1)
            data['arc_scope'] = {'from_chapter': start, 'to_chapter': number}
            data['arc_chapters'] = [dict(r) for r in conn.execute('''SELECT c.number chapter_number,c.version_id,v.title,v.body FROM chapters c JOIN chapter_versions v ON v.id=c.version_id
                WHERE c.book_id=? AND c.status='committed' AND c.number BETWEEN ? AND ? ORDER BY c.number''', (book['book_id'], start, number))]
            data['arc_plan'] = [chapter for chapter in book['plan']['chapters'] if start <= chapter['number'] <= number]
        if stage == 'extract':
            # Only current prose can substantiate this chapter's extracted facts.
            # Supply identities for stable keys, not previous prose to misquote.
            allowed = ('instruction','chapter_number','candidate','promise_obligations','extraction_feedback','validation_feedback','extraction_repair')
            scoped = {key: data[key] for key in allowed if key in data}
            scoped['source_paragraphs'] = source_paragraphs(candidate['body'])
            scoped['existing_memory_keys'] = [{'kind': m['kind'], 'key': m['key']} for m in memories]
            return scoped
        return data

    def _attention(self, conn, run, reason):
        conn.execute("UPDATE runs SET status='needs_attention',reason=? WHERE id=?",(reason,run['run_id']))
        conn.execute("UPDATE books SET status='needs_attention' WHERE id=?",(run['book_id'],))
        self.store.event(conn,run['book_id'],'needs_attention',{'reason':reason},run['run_id'])

    def _task_spec(self, conn, book, run):
        data=self._input(conn,book,run)
        schema = adjudication_schema(data['review_adjudication']) if data.get('review_adjudication') else REPAIR_SCHEMA if data.get('extraction_repair') else review_source_schema(run['candidate']) if run['stage'] == 'continuity' else SCHEMAS[run['stage']]
        if data.get('revision_mode') in ('expand_full_body', 'full_body'):
            schema = SCHEMAS['revise']['oneOf'][0]
        # UTF-8 bytes upper bound input tokens for mainstream byte tokenizers, plus output cap.
        reservation=len(dumps(model_input(data)).encode())+len(dumps(schema).encode())+MAX_TASK_OUTPUT_TOKENS+1000
        policy = ContextPolicy.from_env(run['stage'], self.store.root / 'context-policy.json')
        policy = replace(policy, output_reserve=max(MAX_TASK_OUTPUT_TOKENS, policy.output_reserve))
        if policy.mode == 'adaptive' and run['stage'] not in ('brief', 'outline'):
            from .long_memory import current_state, resolve_alias, select_promises
            plan = next((c for c in book['plan']['chapters'] if c['number'] == run['chapter_number']), {})
            pov = plan.get('pov')
            # An unresolved POV cannot gain author-only state or another belief.
            pov_id = resolve_alias(conn, book['book_id'], pov) if pov else None
            role = 'reader' if pov or run['stage'] == 'reader' else 'author'
            if plan.get('entity_ids') and 'story_time' in plan:
                data['current_state'] = current_state(conn, book['book_id'], plan['entity_ids'],
                    through_chapter=run['chapter_number']-1, story_time=plan['story_time'], role=role, pov_entity_id=pov_id)
            scheduled = select_promises(conn, book['book_id'], run['chapter_number'],
                explicit_ids=plan.get('promise_ids', []), trigger_keys=plan.get('trigger_keys', []), role=role, pov_entity_id=pov_id)
            data['scheduled_promises'] = scheduled['required']
            # Reader/extraction views intentionally lack author chapter_plan.
            # Keep only dependency IDs, never its future plot text.
            data['context_required_ids'] = [*plan.get('required_fact_ids', []), *plan.get('promise_ids', [])]
            from .dependency_resolution import resolve_dependencies, attach_dependencies
            resolved = resolve_dependencies(conn, book['book_id'], plan, run['stage'], run['chapter_number'],
                role=role, pov_entity_id=pov_id)
            attach_dependencies(data, resolved)
        data, schema = prepare_lookup(conn, book, run, data, schema, policy)
        compiled = ContextCompiler(policy).compile(data, schema, stage=run['stage'])
        manifest = data.get('context_manifest')
        data = compiled.input
        if manifest is not None:
            data['context_manifest'] = manifest
        data['context_diagnostics'] = compiled.diagnostics
        # Shadow/off preserve the existing financial reservation and gates.
        if compiled.diagnostics['mode'] == 'adaptive':
            reservation = compiled.reservation
        return data, schema, reservation

    def next_task(self, book_id, worker_id='host'):
        if not isinstance(worker_id,str) or not worker_id or len(worker_id)>100:
            raise StoryError('INVALID_REQUEST','worker_id 无效。')
        with self.store.write(book_id) as conn:
            run = self._latest_run(conn,book_id)
            session = conn.execute('SELECT status,run_id FROM workbench_executions WHERE id=?', (worker_id,)).fetchone()
            if session and (session['status'] != 'running' or not run or session['run_id'] != run['run_id']):
                return {'task_id': None, 'status': 'cancelled'}
            if not run:
                return {'task_id':None,'status':'not_started'}
            if run['status']!='running':
                return {'task_id':None,'status':run['status'],'reason':run['reason']}
            book = self.store.decode_book(conn.execute('SELECT * FROM books WHERE id=?',(book_id,)).fetchone())
            task = conn.execute("SELECT * FROM tasks WHERE run_id=? AND status='leased' ORDER BY created_at DESC LIMIT 1",(run['run_id'],)).fetchone()
            now=time.time()
            if task and task['lease_until']>now:
                if task['worker_id']==worker_id:
                    return _task(task)
                return {'task_id':None,'status':'leased','lease_until':task['lease_until']}
            data, schema, reservation = self._task_spec(conn, book, run)
            if not data['context_diagnostics']['executable']:
                self._attention(conn,run,'本阶段上下文容量不足，请检查上下文诊断。')
                self.store.event(conn,book_id,'context_blocked',{'context':data['context_diagnostics']},run['run_id'])
                return {'task_id':None,'status':'needs_attention','reason':'context_capacity'}
            if run['steps']>=run['max_steps'] or run['tokens']+reservation>run['budget_tokens']:
                self._attention(conn,run,'步骤或 token 预留预算不足；可提高预算后继续。')
                self.store.event(conn,book_id,'context_blocked',{'context':data['context_diagnostics'],'gate':'financial_budget'},run['run_id'])
                return {'task_id':None,'status':'needs_attention','reason':'budget'}
            if len(dumps(model_input(data)).encode())>180000:
                self._attention(conn,run,'上下文超过 MVP 安全上限，需要缩小篇幅或人工整理记忆。')
                self.store.event(conn,book_id,'context_blocked',{'context':data['context_diagnostics'],'gate':'transport_bytes'},run['run_id'])
                return {'task_id':None,'status':'needs_attention','reason':'context_limit'}
            if task:
                conn.execute("UPDATE tasks SET status='expired' WHERE id=?",(task['id'],))
            task_id,lease_id=uid('task'),uid('lease')
            conn.execute('''INSERT INTO tasks(id,run_id,book_id,stage,chapter_number,input,output_schema,base_revision,status,worker_id,lease_id,lease_until,created_at)
                          VALUES (?,?,?,?,?,?,?,?,'leased',?,?,?,?)''',
                         (task_id,run['run_id'],book_id,run['stage'],run['chapter_number'],dumps(data),dumps(schema),book['revision'],worker_id,lease_id,now+LEASE_SECONDS,now))
            conn.execute('UPDATE runs SET steps=steps+1,tokens=tokens+? WHERE id=?',(reservation,run['run_id']))
            self.store.event(conn,book_id,'context_compiled',{'task_id':task_id,'context':data['context_diagnostics']},run['run_id'])
            self.store.event(conn,book_id,'task_leased',{'task_id':task_id,'stage':run['stage'],'reserved_tokens':reservation},run['run_id'])
            return _task(conn.execute('SELECT * FROM tasks WHERE id=?',(task_id,)).fetchone())

    def _set_stage(self,conn,run,stage):
        conn.execute('UPDATE runs SET stage=? WHERE id=?',(stage,run['run_id']))

    def _review(self,conn,book,run,result,task_input):
        review = {'stage':run['stage'], **result}
        if task_input.get('review_adjudication'):
            review['adjudicated'] = True
        if run['stage'] == 'reader':
            profile = task_input.get('reader_profile') if isinstance(task_input, dict) else None
            if not isinstance(profile, dict) or profile.get('id') not in READER_PROFILE_ORDER:
                raise StoryError('INVALID_STATE', '读者任务缺少 Core 签发的审稿画像。')
            review['reader_profile'] = profile['id']
        reviews=(run['reviews'] or [])+[review]
        conn.execute('UPDATE runs SET reviews=? WHERE id=?',(dumps(reviews),run['run_id']))
        if run['stage'] == 'continuity' and quality_policy(conn,run)['mode'] == 'bounded':
            count = min(3, quality_review_count(conn,run) + 1)
            severe = any(i['severity'] in ('blocker','major') for i in result['issues'])
            if severe and count < 3:
                target = 'extract' if result.get('repair_target') == 'memory' else 'revise'
                conn.execute('UPDATE runs SET stage=?,attempts=? WHERE id=?',(target,count,run['run_id']))
            else:
                self._commit_chapter(conn,book,run)
                version = conn.execute('SELECT version_id FROM chapters WHERE book_id=? AND number=?',(book['book_id'],run['chapter_number'])).fetchone()[0]
                single = quality_policy(conn,run)['single']
                self.store.event(conn,book['book_id'],'chapter_quality_released',{'chapter_number':run['chapter_number'],'version_id':version,'release_reason':'review_limit' if severe else 'ai_pass','review_count':count,'author_status':'pending' if single else 'assumed','remaining_issues':result['issues']},run['run_id'])
                if single:
                    conn.execute("UPDATE runs SET status='awaiting_author',reason='本章AI审稿已结束，等待作者确认或提出修改。' WHERE id=?",(run['run_id'],))
                    conn.execute("UPDATE books SET status='awaiting_author' WHERE id=?",(book['book_id'],))
            return
        # A minor recommendation is useful editorial context, not a publication
        # gate. Blocking it created a self-reinforcing rewrite loop when a reviewer
        # merely asked to soften the wording of extracted memory.
        failure=any(i['severity'] in ('blocker','major') for i in result['issues'])
        # Reader profiles are advisory editorial lenses. They may flag pacing or
        # appeal as major, but only a reader-level blocker halts a candidate; hard
        # factual/continuity gates keep the final authority for correctness.
        if run['stage'] == 'reader':
            failure=any(i['severity'] == 'blocker' for i in result['issues'])
        conflicts = unresolved_hard_requirements(result, task_input) if run['stage'] == 'continuity' else []
        if conflicts and not failure:
            self.store.event(conn, book['book_id'], 'revision_verification_conflict',
                {'feedback_ids': conflicts, 'chapter_number': run['chapter_number']}, run['run_id'])
            self._attention(conn, run, '返修核验结论矛盾：硬性问题仍未满足，但审稿未列出阻断问题。候选稿已保留，请重试当前审校核对结论，不必重复改写正文。')
            return
        if failure:
            if run['stage'] == 'continuity' and result.get('repair_target') == 'memory':
                memory_rounds = sum(r.get('stage') == 'continuity' and r.get('repair_target') == 'memory' and any(i.get('severity') in ('major','blocker') for i in r.get('issues', [])) for r in run['reviews'] or [])
                if memory_rounds >= 2:
                    self._attention(conn,run,'记忆提取已修复两次仍有问题，已停止；正文未改动，请检查记忆证据。')
                else:
                    self._set_stage(conn,run,'extract')
            elif run['stage'] == 'arc':
                self._attention(conn,run,'故事弧审校未通过，需要作者处理已定稿阶段问题。')
            elif run['stage'] == 'continuity' and run['attempts'] >= run['max_revisions'] and not task_input.get('review_adjudication') and not any(r.get('adjudicated') for r in run['reviews'] or []):
                reviews[-1]['adjudication_pending'] = True
                conn.execute('UPDATE runs SET reviews=? WHERE id=?', (dumps(reviews),run['run_id']))
                self.store.event(conn,book['book_id'],'review_adjudication_requested',{'chapter_number':run['chapter_number']},run['run_id'])
            elif run['stage']=='ending' or run['attempts']>=run['max_revisions']:
                reason = '审稿未通过：独立裁决仍保留阻断问题或存在不确定项。已停止自动返修，请查看独立审稿裁决依据。' if task_input.get('review_adjudication') else '审稿未通过，已达到返修上限或需要处理全书结构问题。'
                self._attention(conn,run,reason)
            else:
                target = 'extract' if run['stage'] == 'continuity' and result.get('repair_target') == 'memory' else 'revise'
                conn.execute("UPDATE runs SET stage=?,attempts=attempts+1 WHERE id=?",(target,run['run_id']))
        elif run['stage']=='continuity':
            self._set_stage(conn,run,'reader')
        elif run['stage']=='reader':
            completed = {review.get('reader_profile') for review in reviews if review.get('stage') == 'reader'}
            if any(profile not in completed for profile in READER_PROFILE_ORDER):
                self._set_stage(conn,run,'reader')
            else:
                self._commit_chapter(conn,book,run)
        elif run['stage'] == 'arc':
            self._advance_after_commit(conn, book, run)
        else:
            unresolved=self._unresolved(conn,book)
            if unresolved:
                self._attention(conn,run,'仍有必收伏笔没有正文回收证据：'+', '.join(unresolved))
            else:
                conn.execute("UPDATE books SET status='complete',ending=?,revision=revision+1 WHERE id=?",(dumps(result),book['book_id']))
                conn.execute("UPDATE runs SET status='complete',reason=NULL WHERE id=?",(run['run_id'],))
                self.store.event(conn,book['book_id'],'book_completed',{},run['run_id'])

    def _unresolved(self,conn,book):
        total=book['settings']['chapter_count']
        memories=canonical_memory(conn,book['book_id'],total)
        paid={m['key'] for m in memories if m['kind']=='promise' and m.get('status')=='paid'}
        required={p['key'] for p in (book['plan'] or {}).get('promises',[]) if p.get('mandatory')}
        required.update(m['key'] for m in memories if m['kind']=='promise' and m.get('mandatory',False))
        return sorted(required-paid)

    def _commit_chapter(self,conn,book,run):
        number=run['chapter_number']
        candidate=run['candidate']
        version=uid('version')
        old=conn.execute('SELECT version_id FROM chapters WHERE book_id=? AND number=?',(book['book_id'],number)).fetchone()
        conn.execute('INSERT INTO chapter_versions VALUES (?,?,?,?,?,?)',(version,book['book_id'],number,candidate['title'],candidate['body'],time.time()))
        conn.execute("INSERT INTO chapters VALUES (?,?,?,'committed') ON CONFLICT(book_id,number) DO UPDATE SET version_id=excluded.version_id,status='committed'",(book['book_id'],number,version))
        index_chapter(conn,book['book_id'],number,version,candidate['body'],run['extraction']['memories'])
        from .memory_workflow import enqueue_maintenance
        enqueue_maintenance(conn, book['book_id'], version)
        if old:
            enqueue_maintenance(conn, book['book_id'], old[0], invalidated=True)
            conn.execute("UPDATE chapters SET status='needs_review' WHERE book_id=? AND number>? AND status='committed'",(book['book_id'],number))
        conn.execute('UPDATE books SET revision=revision+1,ending=NULL WHERE id=?',(book['book_id'],))
        self.store.event(conn,book['book_id'],'chapter_committed',{'chapter_number':number,'version_id':version,'replaces':old[0] if old else None},run['run_id'])
        total=book['settings']['chapter_count']
        if number>=total:
            conn.execute("UPDATE runs SET stage='ending',reviews='[]',attempts=0 WHERE id=?",(run['run_id'],))
        elif number % ARC_INTERVAL == 0 and quality_policy(conn,run)['mode'] == 'legacy':
            conn.execute("UPDATE runs SET stage='arc',candidate=NULL,extraction=NULL,reviews='[]',attempts=0 WHERE id=?",(run['run_id'],))
        else:
            self._advance_after_commit(conn, book, run)

    def _advance_after_commit(self, conn, book, run):
        number = run['chapter_number']
        if number>=run['end_chapter']:
            conn.execute("UPDATE runs SET status='batch_complete',reason=NULL WHERE id=?",(run['run_id'],))
            conn.execute("UPDATE books SET status='draft' WHERE id=?",(book['book_id'],))
        else:
            following=conn.execute('SELECT v.title,v.body FROM chapters c JOIN chapter_versions v ON v.id=c.version_id WHERE c.book_id=? AND c.number=?',(book['book_id'],number+1)).fetchone()
            conn.execute("UPDATE runs SET chapter_number=?,stage=?,candidate=?,extraction=NULL,reviews='[]',attempts=0 WHERE id=?",
                         (number+1,'extract' if following else 'draft',dumps(dict(following)) if following else None,run['run_id']))

    def submit_task(self, task_id, lease_id, result, worker_id='host'):
        try:
            if isinstance(result, dict) and 'context_lookup' in result:
                if set(result) != {'context_lookup'} or not isinstance(result['context_lookup'], dict) or set(result['context_lookup']) != {'query','reason'}:
                    raise StoryError('INVALID_LOOKUP', '补查须单独提交 query 与 reason。')
                return self.lookup_task(task_id, lease_id, **result['context_lookup'], worker_id=worker_id)
            return self._submit_task(task_id, lease_id, result, worker_id)
        except StoryError as failure:
            # The validation transaction rolled back. Retain only a live, owned,
            # same-revision output as a diagnostic artifact, never canonical memory.
            with self.store.write() as conn:
                task = conn.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
                if task and task['status'] == 'leased' and task['lease_id'] == lease_id and task['worker_id'] == worker_id and task['lease_until'] > time.time():
                    run = self._latest_run(conn, task['book_id'])
                    book = self.store.decode_book(conn.execute('SELECT * FROM books WHERE id=?', (task['book_id'],)).fetchone())
                    if run and run['run_id'] == task['run_id'] and run['status'] == 'running' and book['revision'] == task['base_revision']:
                        failure.details.setdefault('phase', 'result_validation')
                        failure.details.setdefault('next_action', '已保留本次模型返回与错误字段；修复协议后可重放，不需要重写正文。')
                        self.store.event(conn, task['book_id'], 'task_result_rejected',
                            {'task_id': task_id, 'stage': task['stage'], 'raw_result': result,
                             'code': failure.code, 'message': failure.message,
                             'errors': failure.details.get('errors', []), 'details': failure.details}, task['run_id'])
                        if task['stage'] == 'extract':
                            inputs = json.loads(task['input'])
                            try:
                                draft = merge_extraction(result, inputs.get('extraction_repair'))
                                saved = checkpoint(draft, inputs.get('candidate'), book)
                            except StoryError:
                                saved = inputs.get('extraction_repair')
                            if saved:
                                failure.details.update({'retained_count': saved['retained_count'],
                                    'repair_count': len(saved['invalid']), 'phase': 'memory_validation',
                                    'next_action': f"已保存 {saved['retained_count']} 条有效提取；重试仅修复 {len(saved['invalid'])} 条问题记忆，不重写正文。"})
                                self.store.event(conn, task['book_id'], 'extraction_rejected',
                                    {'task_id': task_id, 'checkpoint': saved, 'raw_result': result}, task['run_id'])
            raise

    def _submit_task(self, task_id, lease_id, result, worker_id='host'):
        submitted_result = result
        with self.store.write() as conn:
            task=conn.execute('SELECT * FROM tasks WHERE id=?',(task_id,)).fetchone()
            if not task:
                raise StoryError('NOT_FOUND','找不到任务。')
            if task['lease_id']!=lease_id or task['worker_id']!=worker_id:
                raise StoryError('INVALID_LEASE','任务租约或执行者不匹配。')
            if task['stage'] == 'extract':
                inputs = json.loads(task['input'])
                if len(json.dumps(result, ensure_ascii=False).encode()) > 100000:
                    raise StoryError('INVALID_RESULT', '单次任务原始结果过大。', {'phase': 'result_validation', 'next_action': '减少重复记忆，仅提交本章新增且有证据的条目。'})
                result = resolve_sources(merge_extraction(result, inputs.get('extraction_repair')), inputs.get('candidate'))
            if task['stage'] == 'continuity' and json.loads(task['input']).get('review_adjudication'):
                inputs = json.loads(task['input'])
                result = adjudicate(result, inputs['review_adjudication'], inputs['candidate'])
            if task['stage'] == 'continuity':
                result = resolve_review_sources(result, json.loads(task['input'])['candidate'])
            if task['stage'] == 'revise':
                if json.loads(task['input']).get('revision_mode') == 'expand_full_body' and isinstance(result, dict) and 'patches' in result:
                    raise StoryError('REVISION_MODE_MISMATCH', '当前任务为补写式返修，必须返回完整正文，不能提交局部补丁。',
                                     {'phase': 'result_validation', 'next_action': '按当前任务返回完整 title/body，并满足 length_requirement。'})
                result = materialize(result, json.loads(task['input']).get('candidate'))
            if task['status']=='submitted':
                if json.loads(task['result'])!=result:
                    raise StoryError('IDEMPOTENCY_CONFLICT','同一任务已提交不同结果。')
                return json.loads(task['response'])
            run=_run(conn.execute('SELECT * FROM runs WHERE id=?',(task['run_id'],)).fetchone())
            if task['status']!='leased' or task['lease_until']<time.time() or run['status']!='running':
                raise StoryError('INVALID_LEASE','任务已暂停、过期或失效；请重新领取。')
            book=self.store.decode_book(conn.execute('SELECT * FROM books WHERE id=?',(task['book_id'],)).fetchone())
            if book['revision']!=task['base_revision']:
                raise StoryError('STALE_REVISION','作品已修改，拒绝旧上下文结果。')
            stage=task['stage']
            processing_result, warning = validate_output(stage, result, book, run['candidate'], json.loads(task['input']))
            if warning:
                self.store.event(conn, book['book_id'], 'supplement_rejected',
                    {'task_id': task_id, **warning}, run['run_id'])
            if stage in ('brief','outline'):
                field='brief' if stage=='brief' else 'plan'
                conn.execute(f'UPDATE books SET {field}=?,revision=revision+1 WHERE id=?',(dumps(result),book['book_id']))
                if stage=='brief' and book['title']=='未命名作品':
                    conn.execute('UPDATE books SET title=? WHERE id=?',(result['title'],book['book_id']))
                requested_revision = any(review.get('stage') == 'author' for review in run['reviews'] or [])
                self._set_stage(conn,run,'outline' if stage=='brief' else 'revise' if requested_revision else 'extract' if run['candidate'] else 'draft')
            elif stage in ('draft','revise'):
                conn.execute("UPDATE runs SET candidate=?,extraction=NULL,reviews='[]',stage='extract' WHERE id=?",(dumps({'title': result['title'], 'body': result['body']}),run['run_id']))
            elif stage=='extract':
                due = [item for item in promise_obligations(canonical_memory(conn, book['book_id'], run['chapter_number'] - 1),
                                                             book['plan'], run['chapter_number']) if item['urgency'] == 'due']
                settled = {memory.get('key') for memory in result['memories'] if memory.get('kind') == 'promise'
                           and memory.get('status') in ('paid', 'waived')}
                missing = [item['key'] for item in due if item['key'] not in settled]
                if missing:
                    raise StoryError('PROMISE_DUE', '到期必收承诺需要正文证据标记为已回收或豁免。', {'keys': missing})
                conn.execute("UPDATE runs SET extraction=?,stage='continuity' WHERE id=?",(dumps(result),run['run_id']))
            else:
                self._review(conn,book,run,processing_result,json.loads(task['input']))
            revision=conn.execute('SELECT revision FROM books WHERE id=?',(book['book_id'],)).fetchone()[0]
            latest=self._latest_run(conn,book['book_id'])
            response={'accepted':True,'task_id':task_id,'book_revision':revision,'status':latest['status'],'next_stage':latest['stage']}
            conn.execute("UPDATE tasks SET status='submitted',result=?,response=? WHERE id=?",(dumps(result),dumps(response),task_id))
            if stage == 'continuity' and json.loads(task['input']).get('review_adjudication'):
                self.store.event(conn, book['book_id'], 'review_adjudicated',
                    {'task_id': task_id, 'decisions': submitted_result['decisions']}, run['run_id'])
            if stage == 'extract' and json.loads(task['input']).get('extraction_repair'):
                self.store.event(conn, book['book_id'], 'extraction_repaired',
                    {'task_id': task_id, 'repairs': submitted_result['repairs']}, run['run_id'])
            self.store.event(conn,book['book_id'],'task_submitted',{'task_id':task_id,'stage':stage},run['run_id'])
            return response

    def task_active(self, task_id, lease_id):
        with self.store.read() as conn:
            row = conn.execute("SELECT t.status,t.lease_id,t.lease_until,r.status run_status FROM tasks t JOIN runs r ON r.id=t.run_id WHERE t.id=?", (task_id,)).fetchone()
            return bool(row and row['status'] == 'leased' and row['lease_id'] == lease_id
                        and row['lease_until'] > time.time() and row['run_status'] == 'running')

    def renew_task_lease(self, task_id, lease_id, worker_id):
        """Extend a live worker lease without reviving cancelled or expired work."""
        with self.store.write() as conn:
            row = conn.execute("""SELECT t.book_id,t.status,t.lease_id,t.worker_id,t.lease_until,r.status run_status
                FROM tasks t JOIN runs r ON r.id=t.run_id WHERE t.id=?""", (task_id,)).fetchone()
            now = time.time()
            if not row or row['status'] != 'leased' or row['lease_id'] != lease_id or row['worker_id'] != worker_id:
                return False
            if row['lease_until'] <= now or row['run_status'] != 'running':
                return False
            conn.execute('UPDATE tasks SET lease_until=? WHERE id=?', (now + LEASE_SECONDS, task_id))
            return True

    def fail_task(self, task_id, lease_id, error):
        with self.store.write() as conn:
            task = conn.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
            if not task or task['lease_id'] != lease_id or task['status'] != 'leased':
                return
            run = self._latest_run(conn, task['book_id'])
            if not run or run['run_id'] != task['run_id'] or run['status'] != 'running':
                return
            reason = error.message if isinstance(error, StoryError) else '执行被中断，进度已保留。'
            conn.execute("UPDATE runs SET status='paused',reason=? WHERE id=?", (reason, run['run_id']))
            conn.execute("UPDATE tasks SET status='cancelled' WHERE id=?", (task_id,))
            conn.execute("UPDATE books SET status='paused' WHERE id=?", (task['book_id'],))
            payload={'task_id': task_id, 'code': getattr(error, 'code', 'INTERRUPTED'), 'message': reason}
            details=getattr(error, 'details', None)
            if details:
                payload['details']=details
            self.store.event(conn, task['book_id'], 'worker_failed', payload, run['run_id'])

    def query(self,book_id,query,role='author',through_chapter=None,limit=10,task_id=None,lease_id=None,strategy='legacy'):
        pov = None
        if task_id or lease_id:
            with self.store.read() as conn:
                task=conn.execute('SELECT * FROM tasks WHERE id=? AND book_id=?',(task_id,book_id)).fetchone()
                if not task or task['lease_id']!=lease_id or task['status']!='leased' or task['lease_until']<time.time():
                    raise StoryError('INVALID_LEASE','检索凭据失效。')
                run = conn.execute('SELECT status FROM runs WHERE id=?', (task['run_id'],)).fetchone()
                if run['status'] != 'running':
                    raise StoryError('INVALID_LEASE', '检索任务已暂停或失效。')
                book = self.store.book(book_id)
                if task['base_revision'] != book['revision']:
                    raise StoryError('STALE_REVISION', '作品已更新，请重新领取任务。')
                if task['stage'] == 'reader' and role != 'reader':
                    raise StoryError('INVALID_SCOPE', '读者任务禁止读取作者资料。')
                boundary = max(0, task['chapter_number'] - 1)
                if through_chapter is not None and (type(through_chapter) is not int or not 0 <= through_chapter <= boundary):
                    raise StoryError('INVALID_SCOPE', '任务检索只能读取当前章之前的资料。')
                through_chapter = boundary if through_chapter is None else through_chapter
                pov = (json.loads(task['input']).get('pov_context') or {}).get('pov')
                # Scoped task queries use the same source/POV filter as lookup.
                strategy = 'bounded'
        if strategy == 'bounded':
            from .retrieval import RetrievalScope, SQLiteRetriever
            if role == 'reader' and through_chapter is None:
                raise StoryError('INVALID_SCOPE', '读者检索必须指定已读章节。')
            result = SQLiteRetriever(self.store).search(query, RetrievalScope(book_id,
                through_chapter=through_chapter if through_chapter is not None else 2**31,
                role=role, pov=pov), limit=limit)
            result['hits'] = [{**hit, 'text': f"{hit['key']}：{hit['value']}",
                'chapter_number': hit['source']['chapter_number'],
                'source': {**hit['source'], 'quote': hit.get('evidence', '')}} for hit in result['hits']]
            return {**result, 'role': role, 'through_chapter': through_chapter}
        if strategy != 'legacy':
            raise StoryError('INVALID_REQUEST', '未知检索策略。')
        return retrieve(self.store,book_id,query,role,through_chapter,limit)

    def _token_usage(self, conn, book_id):
        """Aggregate provider-reported usage without treating reserved budget as cost."""
        rows = conn.execute("SELECT payload FROM events WHERE book_id=? AND kind='provider_usage' ORDER BY seq", (book_id,)).fetchall()
        # Resolve task identity in Python so this works with SQLite builds that do
        # not ship the JSON1 extension.
        task_info = {row['id']: (row['chapter_number'], row['stage']) for row in conn.execute(
            'SELECT id,chapter_number,stage FROM tasks WHERE book_id=?', (book_id,))}
        calls = {}
        for row in conn.execute("SELECT payload FROM events WHERE book_id=? AND kind='provider_call_started' ORDER BY seq", (book_id,)):
            payload = json.loads(row['payload'])
            calls[payload['call_id']] = {**payload, 'status': 'unconfirmed', 'reported_tokens': None}
        for row in rows:
            payload = json.loads(row['payload'])
            if payload.get('call_id'):
                calls[payload['call_id']] = payload
        for call in calls.values():
            chapter, stage = task_info.get(call.get('task_id'), (None, None))
            call.update(chapter_number=chapter, stage=stage)
        legacy_count = sum(1 for row in rows if not json.loads(row['payload']).get('call_id'))
        total = 0
        by_chapter = {}
        by_stage = {}
        records = 0
        for row in rows:
            payload = json.loads(row['payload'])
            amount = payload.get('reported_tokens')
            if type(amount) is not int or amount < 0:
                continue
            task_id = payload.get('task_id')
            chapter, stage = task_info.get(task_id, (None, None))
            total += amount; records += 1
            if type(chapter) is int and chapter > 0:
                entry = by_chapter.setdefault(chapter, {'chapter_number': chapter, 'reported_tokens': 0, 'task_count': 0})
                entry['reported_tokens'] += amount; entry['task_count'] += 1
            if isinstance(stage, str):
                entry = by_stage.setdefault(stage, {'stage': stage, 'reported_tokens': 0, 'task_count': 0})
                entry['reported_tokens'] += amount; entry['task_count'] += 1
        return {'book_total': total, 'usage_records': records,
                'call_count': len(calls) + legacy_count,
                'unknown_calls': sum(call.get('reported_tokens') is None for call in calls.values()),
                'calls': list(reversed(list(calls.values())))[:100],
                'by_chapter': [by_chapter[number] for number in sorted(by_chapter)],
                'by_stage': [by_stage[name] for name in sorted(by_stage)]}

    def status(self,book_id):
        with self.store.read() as conn:
            book=self.store.decode_book(conn.execute('SELECT * FROM books WHERE id=?',(book_id,)).fetchone())
            run=self._latest_run(conn,book_id)
            counts={r['status']:r['n'] for r in conn.execute('SELECT status,COUNT(*) n FROM chapters WHERE book_id=? GROUP BY status',(book_id,))}
            now=time.time()
            active_task = conn.execute("SELECT id task_id,worker_id,stage,chapter_number,created_at,lease_until FROM tasks WHERE book_id=? AND run_id=? AND status='leased' AND lease_until>? ORDER BY created_at DESC LIMIT 1", (book_id, run['run_id'] if run else '', now)).fetchone()
            events=[dict(r) for r in conn.execute('SELECT seq,kind,payload,created_at FROM events WHERE book_id=? ORDER BY seq DESC LIMIT 30',(book_id,))]
            for event in events: event['payload']=json.loads(event['payload'])
            latest_task = conn.execute("SELECT id task_id,stage,chapter_number,status,response,created_at,lease_until FROM tasks WHERE book_id=? AND run_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1", (book_id, run['run_id'] if run else '')).fetchone()
            execution=None
            if latest_task:
                task=dict(latest_task)
                related=next((event for event in events if event['payload'].get('task_id') == task['task_id'] and event['kind'] in ('worker_failed','task_submitted')), None)
                response=json.loads(task['response']) if task['response'] else {}
                failure=None
                failure_details=None
                if related and related['kind'] == 'worker_failed':
                    failure={'code': related['payload'].get('code', 'INTERRUPTED'), 'message': related['payload'].get('message', '任务未完成。')}
                    failure_details=related['payload'].get('details')
                expired = task['status'] == 'leased' and task['lease_until'] <= now
                if expired:
                    failure = {'code': 'TASK_LEASE_EXPIRED', 'message': '执行租约已到期，未收到有效结果。可以恢复当前阶段。'}
                outcome='running' if task['status'] == 'leased' and not expired and run and run['status'] == 'running' else 'succeeded' if task['status'] == 'submitted' else 'failed' if failure else task['status']
                execution={'task_id':task['task_id'],'stage':task['stage'],'chapter_number':task['chapter_number'],
                           'outcome':outcome,'next_stage':response.get('next_stage'),'failure':failure,
                           'failure_details':failure_details,
                           'started_at':task['created_at'],'elapsed_seconds':max(0, int((related['created_at'] if related else now)-task['created_at']))}
            blocker = None
            if run and run['status'] == 'needs_attention' and run.get('reason') == '步骤或 token 预留预算不足；可提高预算后继续。':
                _, _, reservation = self._task_spec(conn, book, run)
                blocker = {'code': 'BUDGET_LIMIT', 'chapter_number': run['chapter_number'], 'stage': run['stage'],
                           'reserved_tokens': run['tokens'], 'budget_tokens': run['budget_tokens'],
                           'next_reservation': reservation, 'minimum_budget_tokens': run['tokens'] + reservation,
                           'steps': run['steps'], 'max_steps': run['max_steps'], 'minimum_max_steps': run['steps'] + 1}
            if run and run['status'] == 'needs_attention' and run.get('reason') == '上下文超过 MVP 安全上限，需要缩小篇幅或人工整理记忆。':
                context, _, _ = self._task_spec(conn, book, run)
                blocker = {'code': 'CONTEXT_LIMIT', 'chapter_number': run['chapter_number'], 'stage': run['stage'],
                           'input_bytes': len(dumps(model_input(context)).encode()), 'limit_bytes': 180000}
            if run and run['status'] == 'needs_attention' and run.get('reason') == '本阶段上下文容量不足，请检查上下文诊断。':
                context, _, _ = self._task_spec(conn, book, run)
                blocker = {'code': 'CONTEXT_CAPACITY', 'chapter_number': run['chapter_number'], 'stage': run['stage'],
                           'context': context['context_diagnostics']}
            token_usage = self._token_usage(conn, book_id)
            return {'book_id':book_id,'title':book['title'],'status':book['status'],'revision':book['revision'],
                    'chapters':counts,'target_chapters':book['settings']['chapter_count'],'run':run,'events':events,
                    'token_usage': token_usage, 'blocker': blocker, 'context_usage': context_usage(conn, book_id),
                    'active_task': dict(active_task) if active_task else None,
                    'execution':execution,
                    'budget_note':'tokens 为保守预留量，含重领任务；不是服务商账单。'}

    def control(self,book_id,action,**options):
        if action=='start': return self.start_run(book_id,**options)
        expected_revision = options.pop('expected_revision', None)
        if expected_revision is not None and (type(expected_revision) is not int or expected_revision < 0):
            raise StoryError('INVALID_REQUEST', '作品版本必须是非负整数。')
        with self.store.write(book_id, expected_revision=expected_revision) as conn:
            book=self.store.decode_book(conn.execute('SELECT * FROM books WHERE id=?',(book_id,)).fetchone())
            run=self._latest_run(conn,book_id)
            if action=='approve_chapter':
                number=options.get('chapter_number');version=options.get('version_id')
                current=conn.execute('SELECT version_id FROM chapters WHERE book_id=? AND number=?',(book_id,number)).fetchone()
                if not current or current[0]!=version:
                    raise StoryError('STALE_REVISION','章节版本已变化，请重新确认。')
                info=chapter_quality(conn,book_id).get(version,{})
                if info.get('author_status')=='approved':return run
                if info.get('author_status')!='pending' or not run or run['status']!='awaiting_author':
                    raise StoryError('INVALID_STATE','该章节当前不在等待作者确认阶段。')
                self.store.event(conn,book_id,'chapter_author_approved',{'chapter_number':number,'version_id':version,'author_status':'approved'},run['run_id'])
                status='running' if run['stage']=='ending' else 'batch_complete'
                conn.execute('UPDATE runs SET status=?,reason=NULL WHERE id=?',(status,run['run_id']))
                conn.execute("UPDATE books SET status='draft' WHERE id=?",(book_id,))
                return self._latest_run(conn,book_id)
            if action=='review_chapter':
                number=options.get('chapter_number')
                row=conn.execute('SELECT v.title,v.body FROM chapters c JOIN chapter_versions v ON v.id=c.version_id WHERE c.book_id=? AND c.number=?',(book_id,number)).fetchone()
                if not row:raise StoryError('NOT_FOUND','所选章节尚无已保存正文。')
                return self._insert_run(conn,book,1,500,2,2000000,candidate=dict(row),chapter=number)
            if action=='revise':
                feedback = options.get('feedback', '')
                if not isinstance(feedback, str) or len(feedback) > 10000:
                    raise StoryError('INVALID_REQUEST', '修改意见必须是 10000 字以内的文本。')
                number=options.get('chapter_number')
                if type(number) is not int:
                    raise StoryError('INVALID_REQUEST','请指定章节。')
                if run and run['status'] in ('running', 'paused'):
                    raise StoryError('RUN_ACTIVE', f'第 {run["chapter_number"]} 章正在 AI {run["stage"]} 阶段或等待重试；不能新开返修覆盖当前链路。请用“继续推进”完成本轮 AI 流程。')
                if run and run['status'] == 'needs_attention' and (not run['candidate'] or not any(
                        phrase in (run['reason'] or '') for phrase in ('审稿未通过', '返修上限', '全书结构问题'))):
                    raise StoryError('RUN_ACTIVE', '当前流程需要处理预算、记忆或全书结构问题，不能用章节返修覆盖。')
                # A rejected candidate has not entered the canonical chapters table yet.
                # Let its author start a fresh, bounded revision run without promoting it.
                if run and run['candidate'] and run['chapter_number'] == number:
                    base = run['candidate']
                else:
                    row=conn.execute('SELECT v.title,v.body FROM chapters c JOIN chapter_versions v ON v.id=c.version_id WHERE c.book_id=? AND c.number=?',(book_id,number)).fetchone()
                    if not row: raise StoryError('NOT_FOUND','章节尚未提交，无法修改。')
                    base = dict(row)
                candidate={'title':options.get('title') or base['title'],'body':options.get('body')}
                validate('revise',candidate,book)
                if run and run['status'] in ('running','paused','needs_attention','awaiting_author'):
                    conn.execute("UPDATE runs SET status='cancelled' WHERE id=?",(run['run_id'],))
                    conn.execute("UPDATE tasks SET status='cancelled' WHERE run_id=? AND status='leased'",(run['run_id'],))
                result=self._insert_run(conn,book,None if run and quality_policy(conn,run)['mode']=='legacy' else 1,500,2,2000000,candidate=candidate,chapter=number,review_mode=quality_policy(conn,run)['mode'] if run else 'bounded')
                if feedback.strip():
                    reviews = [{'stage': 'author', 'verdict': 'revise', 'issues': [], 'notes': feedback.strip()}]
                    stage = 'revise' if book['brief'] and book['plan'] else result['stage']
                    conn.execute('UPDATE runs SET reviews=?,stage=? WHERE id=?', (dumps(reviews), stage, result['run_id']))
                    self.store.event(conn, book_id, 'revision_requested', {'chapter_number': number, 'feedback': feedback.strip()}, result['run_id'])
                conn.execute('UPDATE books SET revision=revision+1 WHERE id=?',(book_id,))
                return self._latest_run(conn, book_id)
            if action not in ('pause','resume','cancel'):
                raise StoryError('INVALID_REQUEST','未知控制动作。')
            if not run or run['status'] not in ('running','paused','needs_attention'):
                raise StoryError('NO_ACTIVE_RUN','没有可控制的进行中任务。')
            if action=='resume':
                for key in ('budget_tokens','max_steps'):
                    if key in options:
                        value=options[key]
                        if type(value) is not int or value<1 or value>100000000:
                            raise StoryError('INVALID_REQUEST','预算无效。')
                        conn.execute(f'UPDATE runs SET {key}=? WHERE id=?',(value,run['run_id']))
                conn.execute("UPDATE runs SET status='running',reason=NULL WHERE id=?",(run['run_id'],))
                conn.execute("UPDATE books SET status='writing' WHERE id=?",(book_id,))
            else:
                state='paused' if action=='pause' else 'cancelled'
                conn.execute('UPDATE runs SET status=? WHERE id=?',(state,run['run_id']))
                conn.execute("UPDATE tasks SET status='cancelled' WHERE run_id=? AND status='leased'",(run['run_id'],))
                conn.execute('UPDATE books SET status=? WHERE id=?',('paused' if action=='pause' else 'draft',book_id))
            self.store.event(conn,book_id,action,options,run['run_id'])
            return self._latest_run(conn,book_id)

    def export(self,book_id,allow_partial=False):
        from .exporting import export_book
        return export_book(self,book_id,allow_partial)

    def execution_report(self, book_id):
        book = self.get_book(book_id)
        with self.store.read() as conn:
            tasks = {row['status']: row['count'] for row in conn.execute('SELECT status,COUNT(*) count FROM tasks WHERE book_id=? GROUP BY status', (book_id,))}
            versions = {row['number']: row['count'] for row in conn.execute('SELECT number,COUNT(*) count FROM chapter_versions WHERE book_id=? GROUP BY number', (book_id,))}
            token_usage = self._token_usage(conn, book_id)
            usage = [json.loads(row['payload']) for row in conn.execute("SELECT payload FROM events WHERE book_id=? AND kind='provider_usage'", (book_id,))]
            submitted = list(conn.execute("SELECT stage,chapter_number,created_at,result FROM tasks WHERE book_id=? AND status='submitted' AND stage IN ('continuity','reader','arc','ending') ORDER BY created_at", (book_id,)))
            failures = [json.loads(row['payload']) for row in conn.execute("SELECT payload FROM events WHERE book_id=? AND kind='worker_failed' ORDER BY seq", (book_id,))]
            human_review_count = conn.execute('SELECT COUNT(*) FROM human_reviews WHERE book_id=?', (book_id,)).fetchone()[0]
        reviews = [{**dict(row), 'result': json.loads(row['result'])} for row in submitted]
        return {'book_id': book_id, 'revision': book['revision'], 'status': book['status'],
                'chapters': [{'number': chapter['chapter_number'], 'title': chapter['title'], 'status': chapter['status'],
                              'word_count': chapter['word_count'], 'versions': versions[chapter['chapter_number']]} for chapter in book['chapters']],
                'tasks': tasks, 'review_count': len(reviews), 'reviews': reviews, 'failures': failures,
                'reported_tokens': token_usage['book_total'], 'usage_records': token_usage['usage_records'], 'token_usage': token_usage,
                'execution_metadata': [item['execution'] for item in usage if item.get('execution')],
                'human_review_count': human_review_count,
                'literary_quality': 'human-feedback-recorded' if human_review_count else 'not-human-validated'}

    def add_human_feedback(self, book_id, chapter_number, reviewer_type, notes, reviewer_name='', verdict='revise',
                           rating=None, would_continue=None, version_id=None):
        if type(chapter_number) is not int or chapter_number < 1:
            raise StoryError('INVALID_REQUEST', '章节编号无效。')
        if reviewer_type not in HUMAN_REVIEWER_TYPES:
            raise StoryError('INVALID_REQUEST', '反馈身份无效。')
        if verdict not in ('pass', 'revise'):
            raise StoryError('INVALID_REQUEST', '反馈结论只能是 pass 或 revise。')
        if not isinstance(notes, str) or not notes.strip() or len(notes) > 10000:
            raise StoryError('INVALID_REQUEST', '反馈意见需要为 1–10000 字文本。')
        if not isinstance(reviewer_name, str) or len(reviewer_name.strip()) > 80:
            raise StoryError('INVALID_REQUEST', '反馈者名称需要为 80 字以内文本。')
        if rating is not None and (type(rating) is not int or not 1 <= rating <= 5):
            raise StoryError('INVALID_REQUEST', '评分需要为 1–5 的整数。')
        if would_continue is not None and type(would_continue) is not bool:
            raise StoryError('INVALID_REQUEST', '是否继续阅读需要是布尔值。')
        if version_id is not None and (not isinstance(version_id, str) or not version_id):
            raise StoryError('INVALID_REQUEST', '章节版本无效。')
        with self.store.write(book_id) as conn:
            current = conn.execute('SELECT version_id,status FROM chapters WHERE book_id=? AND number=?', (book_id, chapter_number)).fetchone()
            if not current or current['status'] != 'committed':
                raise StoryError('NOT_FOUND', '只能为已定稿章节保存真人反馈。')
            bound_version = version_id or current['version_id']
            exists = conn.execute('SELECT 1 FROM chapter_versions WHERE id=? AND book_id=? AND number=?',
                                  (bound_version, book_id, chapter_number)).fetchone()
            if not exists:
                raise StoryError('NOT_FOUND', '指定的章节版本不属于本作品。')
            review_id = uid('human_review')
            now = time.time()
            conn.execute('''INSERT INTO human_reviews(id,book_id,chapter_number,version_id,reviewer_type,reviewer_name,verdict,rating,would_continue,notes,created_at)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                         (review_id, book_id, chapter_number, bound_version, reviewer_type, reviewer_name.strip(), verdict,
                          rating, None if would_continue is None else int(would_continue), notes.strip(), now))
            self.store.event(conn, book_id, 'human_feedback_submitted',
                             {'review_id': review_id, 'chapter_number': chapter_number, 'version_id': bound_version,
                              'reviewer_type': reviewer_type, 'verdict': verdict}, None)
            return {'review_id': review_id, 'book_id': book_id, 'chapter_number': chapter_number, 'version_id': bound_version,
                    'reviewer_type': reviewer_type, 'reviewer_name': reviewer_name.strip(), 'verdict': verdict,
                    'rating': rating, 'would_continue': would_continue, 'notes': notes.strip(), 'created_at': now}

    def review_history(self, book_id, chapter_number=None, limit=100, include_history=False):
        if chapter_number is not None and (type(chapter_number) is not int or chapter_number < 1):
            raise StoryError('INVALID_REQUEST', '章节编号无效。')
        if type(limit) is not int or not 1 <= limit <= 500:
            raise StoryError('INVALID_REQUEST', '审稿记录数量必须为 1–500。')
        if not isinstance(include_history, bool):
            raise StoryError('INVALID_REQUEST', '审稿范围无效。')
        self.store.book(book_id)
        with self.store.read() as conn:
            current = self._latest_run(conn, book_id)
            current_run_id = current['run_id'] if current else ''
            rows = conn.execute("""SELECT id task_id,run_id,stage,chapter_number,base_revision,input,result,created_at
                FROM tasks WHERE book_id=? AND status='submitted' AND stage IN ('continuity','reader','arc','ending')
                AND (? IS NULL OR chapter_number=?) AND (? OR run_id=?) ORDER BY created_at,rowid""",
                (book_id, chapter_number, chapter_number, int(include_history), current_run_id)).fetchall()
            versions = conn.execute('SELECT id,number FROM chapter_versions WHERE book_id=? ORDER BY number,created_at,rowid', (book_id,)).fetchall()
            version_numbers = {}
            per_chapter = {}
            for version in versions:
                per_chapter[version['number']] = per_chapter.get(version['number'], 0) + 1
                version_numbers[version['id']] = per_chapter[version['number']]
            human_rows = conn.execute('''SELECT id review_id,chapter_number,version_id,reviewer_type,reviewer_name,verdict,rating,would_continue,notes,created_at
                FROM human_reviews WHERE book_id=? AND (? IS NULL OR chapter_number=?) ORDER BY created_at,rowid''',
                (book_id, chapter_number, chapter_number)).fetchall()
            warnings = {}
            for warning_row in conn.execute("SELECT payload FROM events WHERE book_id=? AND kind='supplement_rejected' ORDER BY seq", (book_id,)):
                warning = json.loads(warning_row['payload']); warnings[warning['task_id']] = warning
            counts = {}
            candidate_numbers = {}
            candidate_keys = {}
            result = []
            for row in rows:
                item = {**dict(row), 'result': json.loads(row['result'])}
                warning = warnings.get(item['task_id'])
                if warning:
                    item['supplement_warning'] = warning
                    item['result'].pop(warning['field'], None)
                task_input = json.loads(item.pop('input'))
                candidate = task_input.get('candidate') if isinstance(task_input, dict) else None
                candidate_key = hashlib.sha256(dumps(candidate or {}).encode()).hexdigest()
                candidate_group = (item['run_id'], item['chapter_number'])
                candidates = candidate_keys.setdefault(candidate_group, {})
                if candidate_key not in candidates:
                    candidate_numbers[candidate_group] = candidate_numbers.get(candidate_group, 0) + 1
                    candidates[candidate_key] = candidate_numbers[candidate_group]
                item['candidate_revision'] = candidates[candidate_key]
                profile = task_input.get('reader_profile') if item['stage'] == 'reader' else None
                profile_id = profile.get('id') if isinstance(profile, dict) and profile.get('id') in READER_PROFILE_ORDER else None
                if profile_id:
                    item['reader_profile'] = profile_id
                    item['reviewer'] = reader_profile(profile_id)['label']
                else:
                    item['reviewer'] = {'continuity': '连续性审校', 'reader': '独立读者', 'arc': '故事弧审校', 'ending': '完结审校'}[item['stage']]
                if task_input.get('review_adjudication'):
                    item['reviewer'] = '独立审稿裁决'
                item['version_label'] = f"基于作品版本 {item['base_revision']}"
                item['source'] = 'model'
                item['revision_check'] = task_input.get('revision_check')
                result.append(item)
            for row in human_rows:
                item = dict(row)
                item['stage'] = 'human'
                item['source'] = 'human'
                item['result'] = {'verdict': item.pop('verdict'), 'issues': [], 'notes': item.pop('notes')}
                item['would_continue'] = None if item['would_continue'] is None else bool(item['would_continue'])
                label = HUMAN_REVIEWER_TYPES[item['reviewer_type']]
                item['reviewer'] = f"{label} · {item['reviewer_name']}" if item['reviewer_name'] else label
                item['version_label'] = f"绑定章节版本 {version_numbers.get(item['version_id'], '?')}"
                result.append(item)
            result.sort(key=lambda item: (item['created_at'], item['source'], item.get('task_id', item.get('review_id', ''))))
            for item in result:
                key = (item.get('run_id'), item['chapter_number'], item['stage'], item.get('candidate_revision'), item.get('reader_profile'), item.get('reviewer_type'), item.get('reviewer_name'), item.get('version_id'))
                counts[key] = counts.get(key, 0) + 1
                item['attempt'] = counts[key]
                if item.get('reader_profile'):
                    item['profile_attempt'] = counts[key]
            return annotate_reviews(book_id, result)[-limit:]

    def candidate_versions(self, book_id, chapter_number=None, limit=100):
        """Return author-facing candidate revisions without promoting them to canon.

        Candidate prose lives in task results until every automated gate passes.  Keeping
        that history separate from ``chapter_versions`` makes the review stop auditable
        without incorrectly treating a rejected draft as published story memory.
        """
        if chapter_number is not None and (type(chapter_number) is not int or chapter_number < 1):
            raise StoryError('INVALID_REQUEST', '章节编号无效。')
        if type(limit) is not int or not 1 <= limit <= 500:
            raise StoryError('INVALID_REQUEST', '候选稿记录数量必须为 1–500。')
        self.store.book(book_id)
        with self.store.read() as conn:
            rows = conn.execute("""SELECT id task_id,run_id,stage,chapter_number,result,input,created_at
                FROM tasks WHERE book_id=? AND status='submitted' AND stage IN ('draft','revise')
                AND (? IS NULL OR chapter_number=?) ORDER BY created_at,rowid""",
                (book_id, chapter_number, chapter_number)).fetchall()
            run = self._latest_run(conn, book_id)
            warnings = {}
            for warning_row in conn.execute("SELECT payload FROM events WHERE book_id=? AND kind='supplement_rejected' ORDER BY seq", (book_id,)):
                warning = json.loads(warning_row['payload']); warnings[warning['task_id']] = warning

        entries = []
        for row in rows:
            result = json.loads(row['result']) if row['result'] else {}
            if not isinstance(result, dict) or not isinstance(result.get('title'), str) or not isinstance(result.get('body'), str):
                continue
            entries.append({
                'candidate_id': row['task_id'], 'task_id': row['task_id'], 'run_id': row['run_id'],
                'stage': row['stage'], 'chapter_number': row['chapter_number'],
                'source': 'AI 初稿' if row['stage'] == 'draft' else 'AI 返修',
                'revision_response': None if row['task_id'] in warnings else result.get('revision_response'),
                'supplement_warning': warnings.get(row['task_id']),
                'feedback_items': json.loads(row['input']).get('feedback_items', []),
                'title': result['title'], 'body': result['body'], 'created_at': row['created_at'],
            })

        # A just-written candidate is visible before its task is submitted.  It has no
        # task record yet, so append it only when it is materially different from the
        # latest historical candidate for that chapter.
        candidate = run.get('candidate') if run else None
        if isinstance(candidate, dict) and (chapter_number is None or run['chapter_number'] == chapter_number):
            latest = next((item for item in reversed(entries) if item['chapter_number'] == run['chapter_number']), None)
            if not latest or latest['title'] != candidate.get('title') or latest['body'] != candidate.get('body'):
                entries.append({
                    'candidate_id': f"current:{run['run_id']}", 'task_id': None, 'run_id': run['run_id'],
                    'stage': run['stage'], 'chapter_number': run['chapter_number'], 'source': '当前候选稿',
                    'title': candidate.get('title', ''), 'body': candidate.get('body', ''),
                    'created_at': run['created_at'], 'current': True,
                })

        previous = {}
        numbers = {}
        for item in entries:
            number = item['chapter_number']
            numbers[number] = numbers.get(number, 0) + 1
            item['version_number'] = numbers[number]
            item['word_count'] = word_count(item['body'])
            before = previous.get(number)
            if before is None:
                item['change'] = {'summary': '首次候选稿', 'details': [], 'word_delta': item['word_count'], 'title_changed': False}
            else:
                delta = item['word_count'] - before['word_count']
                unchanged = item['title'] == before['title'] and item['body'] == before['body']
                text_change = _version_change(before['body'], item['body']) if not unchanged else {'summary': '候选正文无文字变化', 'details': []}
                summary = text_change['summary'].replace('正文修订：', '候选稿修订：')
                changes = []
                if item['title'] != before['title']:
                    changes.append('修改标题')
                if delta:
                    changes.append(f"字数{'增加' if delta > 0 else '减少'} {abs(delta)}")
                item['change'] = {**text_change, 'summary': '、'.join(changes + ([] if unchanged else [summary])) or summary,
                                  'word_delta': delta, 'title_changed': item['title'] != before['title']}
            previous[number] = item
        return entries[-limit:]

    def chapter_versions(self, book_id, chapter_number):
        if type(chapter_number) is not int or chapter_number < 1:
            raise StoryError('INVALID_REQUEST', '章节编号无效。')
        self.store.book(book_id)
        with self.store.read() as conn:
            rows = list(conn.execute('SELECT id,title,body,created_at FROM chapter_versions WHERE book_id=? AND number=? ORDER BY created_at,rowid', (book_id, chapter_number)))
        if not rows:
            raise StoryError('NOT_FOUND', '该章节没有已保存版本。')
        result = []
        previous = None
        previous_body = None
        for index, row in enumerate(rows, 1):
            words = word_count(row['body'])
            if previous is None:
                change = {'summary': '首次定稿', 'details': [], 'word_delta': words, 'title_changed': False}
            else:
                delta = words - previous['word_count']
                changes = []
                if row['title'] != previous['title']:
                    changes.append('修改标题')
                if delta:
                    changes.append(f"字数{'增加' if delta > 0 else '减少'} {abs(delta)}")
                unchanged = row['title'] == previous['title'] and row['body'] == previous_body
                text_change = _version_change(previous_body, row['body']) if not unchanged else {'summary': '重新审查定稿，正文无文字变化', 'details': []}
                summary = '、'.join(changes + ([] if unchanged else [text_change['summary']])) or text_change['summary']
                change = {**text_change, 'summary': summary, 'word_delta': delta, 'title_changed': row['title'] != previous['title']}
            item = {'version_id': row['id'], 'version_number': index, 'title': row['title'], 'word_count': words, 'created_at': row['created_at'], 'change': change}
            result.append(item)
            previous = item
            previous_body = row['body']
        return result

    def memory_facets(self, book_id):
        self.store.book(book_id)
        groups = {'人物': set(), '物品': set(), '地点': set(), '组织': set(), '生灵': set(), '其他实体': set(), '实体': set(), '人物与关系': set(), '约定': set(), '事实': set(), '时间': set(), '角色认知': set()}
        names = {'relationship': '人物与关系', 'promise': '约定', 'timeline': '时间', 'knowledge': '角色认知'}
        entity_names = {'person': '人物', 'item': '物品', 'place': '地点', 'organization': '组织', 'creature': '生灵', 'other': '其他实体'}
        with self.store.read() as conn:
            rows = conn.execute("""SELECT m.kind,m.key,m.data FROM memories m JOIN chapters c ON
                c.book_id=m.book_id AND c.number=m.chapter_number AND c.version_id=m.version_id
                WHERE m.book_id=? AND c.status='committed'""", (book_id,))
            for row in rows:
                if row['kind'] == 'entity':
                    entity = json.loads(row['data']).get('entity_type')
                    groups[entity_names.get(entity, '实体')].add(row['key'])
                else:
                    groups[names.get(row['kind'], '事实')].add(row['key'])
        return {name: sorted(keys) for name, keys in groups.items() if keys}
