const element = id => document.getElementById(id);
const state = { book: null, chapter: null, task: null, api: false, executor: 'manual', worker: false, dirty: false, books: [], reviews: [], candidateVersions: [], facets: {}, memoryFacet: '全部', versions: [], status: null, selection: 0, polling: false, busy: new Set(), editRevision: null, marketSources: new Set(), marketSelectionInitialized: false, aiConfig: null, activeView: 'overview', reviewView: 'reviews', focused: false, tts: { request: 0, objectUrl: '', key: '', playing: false }, ttsVoices: [] };
let memoryOffset = 0;
const statuses = { awaiting_author: '等待作者确认', new: '新作品', imported: '导入待审', writing: '写作中', running: '进行中', paused: '已暂停', needs_attention: '需要处理', complete: '已完结', draft: '未完稿', batch_complete: '本轮完成', cancelled: '已结束' };
const stages = { brief: '建立创作约定', outline: '规划全书', draft: '写作正文', extract: '提取故事记忆', continuity: '核对连续性', reader: '读者审稿', arc: '故事弧审校', revise: '修改候选稿', ending: '全书完结审查', human: '真人反馈' };
const readerProfiles = { target_reader: '目标题材读者', logic_reader: '逻辑敏感读者' };
const events = { worker_started: '执行器启动', worker_finished: '执行器结束', worker_interrupted: '服务中断，保存恢复点', run_started: '启动写作批次', task_leased: '领取任务', task_submitted: '提交处理结果', chapter_committed: '章节通过并写入定稿', chapters_imported: '导入待审正文', revision_requested: '创建作者返修', human_feedback_submitted: '保存真人反馈', project_metadata_updated: '更新项目资料', provider_usage: '完成模型调用', worker_failed: '执行中断', book_completed: '全书审查完成', needs_attention: '等待作者处理', pause: '暂停任务', resume: '恢复任务', cancel: '结束本轮任务', book_kind_changed: '修改作品类型', project_archived: '归档作品', project_restored: '恢复归档作品' };
const labels = { premise: '故事核心', audience: '目标读者', pov: '叙述视角', style: '文字风格', ending: '终局方向', desire: '想得到什么', fear: '害怕失去什么', boundary: '人物底线', voice: '说话方式', goal: '行动目标', conflict: '当前阻力', change: '本章变化', payoff: '读者回报', emotion: '情绪推进' };
const GENRE_LIBRARY = [
  ['男频幻想', ['修仙', '玄幻', '高武', '奇幻', '洪荒', '神话', '东方玄幻', '无敌流']],
  ['男频现实', ['都市', '历史', '军事', '职场', '商战', '体育', '乡村', '年代']],
  ['剧情与脑洞', ['悬疑', '推理', '灵异', '规则怪谈', '无限流', '末世', '科幻', '克苏鲁']],
  ['系统与经营', ['金手指', '系统', '种田', '经营', '领主', '直播', '游戏', '副本']],
  ['女频古代', ['古言', '宫斗宅斗', '权谋', '女强', '医妃', '穿越', '重生', '空间']],
  ['女频现代', ['现言', '豪门总裁', '甜宠', '先婚后爱', '娱乐圈', '校园', '婚恋', '职场恋爱']],
  ['情感关系', ['双男主', '双女主', '无CP', '救赎', '破镜重圆', '暗恋', '青梅竹马', '群像']],
  ['形式与篇幅', ['轻小说', '二次元', '短篇', '脑洞故事', '现实情感', '推文文', '多视角', '第一人称']],
];
function node(tag, text, className) { const item = document.createElement(tag); if (text !== undefined) item.textContent = text; if (className) item.className = className; return item; }
function notice(message) { element('notice-message').textContent = message || ''; element('notice').hidden = !message; }
function openDialog(id) {
  const dialog = element(id);
  try {
    if (!dialog.open && typeof dialog.showModal === 'function') dialog.showModal();
    else { dialog.setAttribute('open', ''); dialog.classList.add('dialog-fallback'); }
  } catch { dialog.setAttribute('open', ''); dialog.classList.add('dialog-fallback'); }
  dialog.scrollTop = 0; return dialog;
}
function closeDialog(id) {
  const dialog = element(id);
  if (typeof dialog.close === 'function' && dialog.open) dialog.close();
  else { dialog.removeAttribute('open'); dialog.classList.remove('dialog-fallback'); }
}
function formatElapsed(createdAt) {
  const seconds = Math.max(0, Math.floor(Date.now() / 1000 - Number(createdAt || 0)));
  return seconds >= 60 ? `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒` : `${seconds} 秒`;
}
function formatTokens(value) {
  const amount = Number(value || 0);
  return amount >= 1000000 ? `${(amount / 1000000).toFixed(amount >= 10000000 ? 0 : 2)}M` : amount >= 1000 ? `${(amount / 1000).toFixed(amount >= 100000 ? 0 : 1)}K` : String(amount);
}
function chapterTokenUsage(number) {
  return (state.status?.token_usage?.by_chapter || []).find(item => item.chapter_number === Number(number)) || { reported_tokens: 0, task_count: 0 };
}
function formatSeconds(seconds) {
  const value = Math.max(0, Math.floor(Number(seconds || 0)));
  return value >= 60 ? `${Math.floor(value / 60)} 分 ${value % 60} 秒` : `${value} 秒`;
}
function storage(action, key, value) { try { return localStorage[action](key, value); } catch { return null; } }
function display(value) { return JSON.stringify(value, null, 2); }
function countWords(text) { return (text.match(/[\u3400-\u9fff]|[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*/g) || []).length; }
function ttsAudioUrl(bookId, chapter, voice = 'zh-CN-YunxiNeural', rate = 1) {
  const version = chapter?.candidate ? 'candidate' : chapter?.version_id || 'current';
  const params = new URLSearchParams({ voice, rate: String(Number(rate)) });
  if (version) params.set('version_id', version);
  return `/api/books/${encodeURIComponent(bookId)}/chapters/${encodeURIComponent(chapter.chapter_number)}/audio?${params}`;
}
function nextPlayableChapter(chapters, currentNumber) {
  return [...(chapters || [])].filter(chapter => Number(chapter.chapter_number) > Number(currentNumber))
    .sort((a, b) => Number(a.chapter_number) - Number(b.chapter_number))[0] || null;
}
function formatAudioTime(seconds) {
  const value = Number.isFinite(Number(seconds)) ? Math.max(0, Math.floor(Number(seconds))) : 0;
  return `${Math.floor(value / 60)}:${String(value % 60).padStart(2, '0')}`;
}
function ttsSetStatus(message) { const target = element('tts-status'); if (target) target.textContent = message; }
function setTTSLoading(loading) {
  const button = element('tts-play');
  button.classList.toggle('is-loading', loading);
  button.setAttribute('aria-busy', String(loading));
  button.setAttribute('aria-label', loading ? '正在生成音频' : state.tts.playing ? '暂停本章' : '播放本章');
  button.disabled = loading;
  button.textContent = loading ? '' : state.tts.playing ? 'Ⅱ' : '▶';
}
function clearTTSPreload() {
  state.tts.preload?.controller.abort(); state.tts.preload = null;
}
function maybePreloadTTS() {
  const audio = element('chapter-audio');
  if (!state.tts.playing || !element('tts-auto-next').checked || state.dirty || !audio.duration || audio.currentTime / audio.duration < .7) return;
  const next = nextPlayableChapter(state.book?.chapters, state.chapter?.chapter_number);
  if (!next || !next.version_id) return;
  const url = ttsAudioUrl(state.book.book_id, next, element('tts-voice').value, Number(element('tts-rate').value));
  if (state.tts.preload?.url === url) return;
  clearTTSPreload();
  const controller = new AbortController(); const entry = {url, controller, promise: null}; state.tts.preload = entry;
  const timer = setTimeout(() => controller.abort(), 120000);
  entry.promise = fetch(url, {signal: controller.signal}).then(response => {
    if (!response.ok) throw new Error('预加载失败');
    return response.blob();
  }).then(blob => {
    if (state.tts.preload === entry) ttsSetStatus('播放中 · 下一章已就绪');
    return blob;
  }).catch(() => null).finally(() => clearTimeout(timer));
}
function resetTTSForChapter(chapter) {
  setTTSLoading(false);
  const nextUrl = chapter && state.book ? ttsAudioUrl(state.book.book_id, chapter, element('tts-voice').value, Number(element('tts-rate').value)) : null;
  if (state.tts.preload?.url !== nextUrl) clearTTSPreload();
  state.tts.request += 1; state.tts.playing = false; state.tts.key = chapter ? `${chapter.chapter_number}:${chapter.candidate ? 'candidate' : chapter.version_id}` : ''; const audio = element('chapter-audio');
  if (!audio) return;
  audio.pause(); if (state.tts.objectUrl && typeof URL !== 'undefined' && URL.revokeObjectURL) URL.revokeObjectURL(state.tts.objectUrl); state.tts.objectUrl = '';
  audio.removeAttribute('src'); audio.load(); element('tts-play').disabled = false; element('tts-progress').value = 0; element('tts-time').textContent = '0:00 / 0:00'; element('tts-play').textContent = '▶'; element('tts-version-label').textContent = chapter?.candidate ? '候选版本' : '当前定稿';
  ttsSetStatus(chapter ? '点击播放后生成音频' : '选择章节后播放');
}
async function prepareAndPlayTTS() {
  const chapter = state.chapter; const book = state.book; if (!chapter || !book) throw new Error('请先选择有正文的章节。');
  const request = ++state.tts.request; const voice = element('tts-voice').value || 'zh-CN-YunxiNeural'; const rate = Number(element('tts-rate').value || 1); const audio = element('chapter-audio');
  ttsSetStatus('正在生成听书音频…'); setTTSLoading(true);
  const controller = new AbortController(); const timer = setTimeout(() => controller.abort(), 120000);
  try {
    const url = ttsAudioUrl(book.book_id, chapter, voice, rate);
    let blob = state.tts.preload?.url === url ? await state.tts.preload.promise : null;
    if (!blob) {
    const response = await fetch(ttsAudioUrl(book.book_id, chapter, voice, rate), { signal: controller.signal }); clearTimeout(timer);
    if (!response.ok) { let detail = 'Edge TTS 生成失败。'; try { const body = await response.json(); detail = body.detail?.message || body.detail || detail; } catch (_) {} throw new Error(detail); }
    blob = await response.blob();
    }
    if (request !== state.tts.request || chapter !== state.chapter) return;
    clearTTSPreload();
    if (state.tts.objectUrl && typeof URL !== 'undefined' && URL.revokeObjectURL) URL.revokeObjectURL(state.tts.objectUrl);
    state.tts.objectUrl = typeof URL !== 'undefined' && URL.createObjectURL ? URL.createObjectURL(blob) : ttsAudioUrl(book.book_id, chapter, voice, rate);
    audio.src = state.tts.objectUrl; audio.load(); await audio.play(); state.tts.playing = true; element('tts-play').textContent = 'Ⅱ'; ttsSetStatus(`${element('tts-voice').selectedOptions?.[0]?.textContent || voice} · 播放中`);
  } catch (error) {
    if (request !== state.tts.request) return;
    if (error.name === 'AbortError') error = new Error('听书音频生成超时，请稍后重试。');
    ttsSetStatus(error.message); throw error;
  } finally { clearTimeout(timer); if (request === state.tts.request) setTTSLoading(false); }
}
async function toggleTTS() {
  const audio = element('chapter-audio'); if (!audio || !state.chapter) throw new Error('请先选择章节。');
  if (state.tts.playing) { audio.pause(); state.tts.playing = false; element('tts-play').textContent = '▶'; ttsSetStatus('已暂停'); return; }
  if (!audio.src) return prepareAndPlayTTS(); await audio.play(); state.tts.playing = true; element('tts-play').textContent = 'Ⅱ'; ttsSetStatus('播放中');
}
async function loadTTSVoices() {
  const fallback = [{id:'zh-CN-YunxiNeural',label:'云希 · 男声'},{id:'zh-CN-XiaoxiaoNeural',label:'晓晓 · 女声'},{id:'zh-CN-YunjianNeural',label:'云健 · 男声'}];
  try { state.ttsVoices = (await api('/api/tts/voices')).voices || fallback; } catch (_) { state.ttsVoices = fallback; }
  const select = element('tts-voice'); if (!select) return; select.replaceChildren(); for (const voice of state.ttsVoices) { const option = document.createElement('option'); option.value = voice.id; option.textContent = voice.label; select.append(option); }
}
function empty(container, title, description, symbol = '◇') { container.replaceChildren(); const box = node('div', undefined, 'empty-state'); box.append(node('span', symbol, 'empty-symbol'), node('h3', title), node('p', description)); container.append(box); }
async function api(path, body) {
  const controller = new AbortController(); const timer = setTimeout(() => controller.abort(), 30000);
  try {
    const response = await fetch(path, { signal: controller.signal, ...(body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }) });
    const result = await response.json();
    if (!response.ok) { const error = new Error(typeof result.detail === 'object' ? result.detail.message || display(result.detail) : result.detail || '请求失败'); error.code = result.detail?.code; throw error; }
    return result;
  } catch (error) {
    if (error.name === 'AbortError' || error instanceof TypeError) throw new Error('无法连接本地服务。请确认服务已启动，然后刷新重试。');
    throw error;
  } finally { clearTimeout(timer); }
}
async function apiDelete(path) {
  const response = await fetch(path, { method: 'DELETE' }); const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === 'object' ? result.detail.message : result.detail || '请求失败');
  return result;
}
function selectedQualityLabel(chapter) {
  const q = chapter?.quality || {};
  const release = q.release_reason === 'review_limit' ? '达到3次审稿上限自动通过（仍有建议）' : q.release_reason ? 'AI基础审稿完成' : chapter?.candidate ? '候选稿 · ' + (chapter.workflow_label || '待审') : chapter?.status === 'committed' ? '已保存正文' : '待审';
  return release + (q.author_status === 'pending' ? ' · 等待你确认' : q.author_status === 'approved' ? ' · 作者已确认' : q.author_status === 'assumed' ? ' · 连写默认无异议' : '');
}
function renderSelectedChapter() {
  const ch = state.chapter; const run = state.status?.run;
  element('selected-chapter-title').textContent = ch ? `第 ${ch.chapter_number} 章 · ${ch.title}` : '请选择章节';
  element('selected-chapter-status').textContent = ch ? selectedQualityLabel(ch) : '';
  const blocked = ['running','paused','needs_attention','awaiting_author'].includes(run?.status);
  element('review-chapter').disabled = !ch || !!ch.candidate || blocked || state.busy.has('review-chapter');
  element('approve-chapter').hidden = ch?.quality?.author_status !== 'pending';
  element('approve-chapter').disabled = state.busy.has('approve-chapter');
  element('selected-action-hint').textContent = blocked && run?.status !== 'awaiting_author' ? `全书任务停留在第 ${run.chapter_number} 章；暂停可原位续写，结束本轮后可单独审核其他章节。` : '审核与修改作用于上方所选章节；作者确认只绑定此正文版本。';
}
function controls() {
  const run = state.status?.run; const active = run && ['running', 'paused', 'needs_attention'].includes(run.status); const archived = state.book?.book_kind === 'archived';
  const revisionUnlocked = !active || needsAuthorRevision(run);
  element('get-task').hidden = Boolean(state.worker);
  const conditions = { continue: !state.book || archived || state.status?.status === 'complete' || state.status?.worker_running, pause: !active || run.status === 'paused', cancel: !active, export: state.status?.status !== 'complete', 'save-revision': !state.chapter || !state.dirty || !revisionUnlocked, 'request-revision': !state.chapter || !revisionUnlocked, 'get-task': !active || run.status !== 'running', 'submit-task': !state.task?.task_id, 'submit-human-feedback': !state.chapter || state.chapter.status !== 'committed', 'import-chapters': Boolean(active) || archived, 'edit-story-bible': !state.book?.brief || !state.book?.plan };
  for (const [id, disabled] of Object.entries(conditions)) element(id).disabled = disabled || state.busy.has(id);
  element('revision-feedback').disabled = !revisionUnlocked;
  element('batch-size').disabled = Boolean(active);
  renderSelectedChapter();
}
function action(id, handler) {
  element(id).addEventListener('click', async () => {
    if (state.busy.has(id)) return;
    state.busy.add(id); element(id).disabled = true; notice('');
    try { await handler(); } catch (error) { notice(error.message); }
    finally { state.busy.delete(id); element(id).disabled = false; controls(); }
  });
}
function switchTab(id) {
  if (!['overview', 'manuscript', 'planning', 'memory', 'activity'].includes(id)) id = 'overview';
  state.activeView = id;
  document.querySelectorAll('[data-tab]').forEach(button => { const active = button.dataset.tab === id; button.classList.toggle('active', active); button.setAttribute('aria-selected', String(active)); button.tabIndex = active ? 0 : -1; });
  document.querySelectorAll('.tab-panel').forEach(panel => { panel.hidden = panel.id !== id; });
  element('workspace').dataset.view = id;
  element('focus-writing').hidden = id !== 'manuscript';
  element('app-shell').classList.toggle('writing-focused', state.focused && id === 'manuscript');
  if (state.book) storage('setItem', `hulk-view-${state.book.book_id}`, id);
}
function switchReviewView(id) {
  if (!['reviews', 'versions', 'operations'].includes(id)) id = 'reviews';
  state.reviewView = id;
  document.querySelectorAll('[data-review-view]').forEach(button => { const active = button.dataset.reviewView === id; button.classList.toggle('active', active); button.setAttribute('aria-selected', String(active)); button.tabIndex = active ? 0 : -1; });
  document.querySelectorAll('[data-review-panel]').forEach(panel => { panel.hidden = panel.dataset.reviewPanel !== id; });
}
function readingChapter() {
  return candidateChapter() || [...(state.book?.chapters || [])].sort((a, b) => b.chapter_number - a.chapter_number)[0];
}
function localDraftKey() { return state.book && state.chapter ? `hulk-draft-${state.book.book_id}-${state.chapter.chapter_number}` : null; }
function readLocalDraft() {
  try {
    const draft = JSON.parse(storage('getItem', localDraftKey()) || 'null');
    return draft && ['title','body','feedback','baseTitle','baseBody'].every(key => typeof draft[key] === 'string') ? draft : null;
  } catch { return null; }
}
function localDraftMatches(draft) {
  return Boolean(draft && draft.revision === state.book?.revision && draft.baseTitle === state.chapter?.title && draft.baseBody === state.chapter?.body);
}
function saveLocalDraft() {
  const key = localDraftKey(); if (!key) return;
  const draft = {title: element('chapter-title').value, body: element('chapter-body').value, feedback: element('revision-feedback').value,
    baseTitle: state.chapter.title, baseBody: state.chapter.body, revision: state.editRevision, savedAt: Date.now()};
  try { localStorage.setItem(key, JSON.stringify(draft)); element('save-state').textContent = '编辑已暂存本浏览器 · 尚未送审'; }
  catch { element('save-state').textContent = '本地暂存失败，请复制正文后保存'; }
}
function showLocalDraft() {
  const box = element('local-draft-recovery'); box.replaceChildren();
  const draft = readLocalDraft(); box.hidden = !draft; if (!draft) return;
  const matches = localDraftMatches(draft);
  box.append(node('strong', matches ? '发现未提交的本地编辑' : '发现旧版本本地编辑'));
  box.append(node('p', matches ? '可恢复正文与返修意见，恢复不会自动提交。' : '当前稿件或作品版本已变化，仅提供旧编辑预览，避免覆盖新稿。', 'hint'));
  const preview = node('details'); preview.append(node('summary', '查看暂存内容'));
  const text = node('textarea'); text.value = `${draft.title}\n\n${draft.body}\n\n返修意见：${draft.feedback}`; text.readOnly = true; text.setAttribute('aria-label', '暂存内容预览'); preview.append(text); box.append(preview);
  if (matches) {
    const restore = node('button', '恢复本地编辑', 'primary small'); restore.type = 'button';
    restore.addEventListener('click', () => {
      if (!localDraftMatches(draft)) { showLocalDraft(); return; }
      element('chapter-title').value = draft.title; element('chapter-body').value = draft.body; element('revision-feedback').value = draft.feedback;
      state.dirty = true; element('editor-word-count').textContent = `${countWords(draft.body)} 字 · 本地编辑中`;
      element('save-state').textContent = '本地编辑已恢复 · 尚未送审'; box.hidden = true; controls();
    }); box.append(restore);
  }
  const discard = node('button', '丢弃暂存', 'small'); discard.type = 'button';
  discard.addEventListener('click', () => { if (confirm('确定丢弃此章节的本地暂存？')) { storage('removeItem', localDraftKey()); box.hidden = true; } }); box.append(discard);
}
function renderUsage() {
  const box = element('usage-breakdown'); box.replaceChildren();
  const usage = state.status?.token_usage;
  if (!usage?.call_count && !usage?.usage_records) { empty(box, '尚无已报告用量', '开始写作后在这里查看章节与任务阶段用量。没有记录不代表没有费用。'); return; }
  const table = node('table', undefined, 'usage-table');
  const caption = node('caption', `本书已报告 ${Number(usage.book_total).toLocaleString()} Token · ${usage.call_count || usage.usage_records} 次调用`); table.append(caption);
  const head = node('tr'); for (const name of ['范围', '已报告 Token', '调用次数']) head.append(node('th', name));
  const thead = node('thead'); thead.append(head); table.append(thead);
  const body = node('tbody');
  for (const row of usage.by_chapter || []) { const tr = node('tr'); tr.append(node('td', `第 ${row.chapter_number} 章`), node('td', row.reported_tokens.toLocaleString()), node('td', String(row.task_count))); body.append(tr); }
  table.append(body); box.append(table);
  const details = node('details'); details.append(node('summary', '按任务阶段查看'));
  for (const row of usage.by_stage || []) details.append(node('p', `${stages[row.stage] || row.stage} · ${row.reported_tokens.toLocaleString()} Token · ${row.task_count} 次调用`));
  box.append(details);
  box.append(node('p', `${usage.unknown_calls || 0} 次调用用量未知；已报告合计不等于最终账单。`, 'hint'));
  const history = node('details'); history.append(node('summary', '逐次调用记录（最近 100 次）'));
  const outcomes = {succeeded: '已提交', failed: '失败', cancelled: '已取消，未提交', unconfirmed: '结果未确认'};
  for (const call of usage.calls || []) {
    const scope = call.chapter_number ? `第 ${call.chapter_number} 章` : '整书规划';
    const tokens = call.reported_tokens == null ? '用量未知' : `${call.reported_tokens.toLocaleString()} Token`;
    const record = node('div', undefined, 'detail-block');
    record.append(node('strong', `${scope} · ${stages[call.stage] || call.stage || '任务'} · ${outcomes[call.status] || call.status}`));
    record.append(node('p', `${tokens} · ${call.error_code || '无已记录错误'} · ${new Date(call.started_at * 1000).toLocaleString()}`));
    if (call.diagnosis) record.append(node('p', diagnosisText(call.diagnosis)));
    record.append(node('small', `调用编号：${call.call_id}`, 'hint')); history.append(record);
  }
  box.append(history);
}
function closeShelfMenus(except = null, restoreFocus = false) {
  document.querySelectorAll('.shelf-menu[open]').forEach(menu => {
    if (menu === except) return;
    menu.open = false;
    if (restoreFocus && menu.contains(document.activeElement)) menu.querySelector('summary').focus();
  });
}
function renderShelf() {
  const search = element('book-search').value.trim().toLowerCase(); element('books').replaceChildren();
  for (const book of state.books.filter(item => item.title.toLowerCase().includes(search))) {
    const button = node('button', undefined, `book-item ${state.book?.book_id === book.book_id ? 'selected' : ''}`);
    button.title = book.title; const detail = node('span', undefined, 'book-item-text');
    const name = node('span', book.title, 'book-name'); if (book.book_kind === 'sample') name.append(node('em', '样例', 'sample-badge')); if (book.book_kind === 'archived') name.append(node('em', '归档', 'archive-badge'));
    detail.append(name, node('small', `${book.book_kind === 'sample' ? '测试样例 · ' : book.book_kind === 'archived' ? '已归档 · ' : ''}${statuses[book.status] || book.status} · ${book.settings.chapter_count} 章计划`));
    button.append(node('span', book.title.slice(0, 1), 'book-glyph'), detail);
    button.addEventListener('click', () => selectBook(book.book_id).catch(error => notice(error.message)));
    const row = node('div', undefined, 'shelf-row'); const menu = node('details', undefined, 'shelf-menu');
    const trigger = node('summary', '⋮'); trigger.setAttribute('aria-label', `${book.title}的更多操作`); menu.append(trigger);
    trigger.addEventListener('click', () => closeShelfMenus(menu));
    const actions = node('div', undefined, 'shelf-menu-actions');
    const addAction = (label, callback, danger = false) => { const action = node('button', label, danger ? 'danger' : ''); action.type = 'button'; action.addEventListener('click', async () => { menu.open = false; try { await callback(); } catch (error) { notice(error.message); } }); actions.append(action); };
    addAction('打开作品', () => selectBook(book.book_id));
    addAction(book.book_kind === 'archived' ? '恢复到我的作品' : '归档作品', async () => { await api(`/api/books/${book.book_id}/kind`, {kind: book.book_kind === 'archived' ? 'user' : 'archived'}); await shelf(); });
    addAction('移入回收站', async () => {
      if (state.book?.book_id === book.book_id && state.dirty && !confirm('有未保存编辑，仍移入回收站？')) return;
      await api(`/api/books/${book.book_id}/trash`, {});
      if (state.book?.book_id === book.book_id) { state.dirty = false; resetTTSForChapter(null); element('new-book').click(); }
      await shelf(); notice(`“${book.title}”已移入回收站，可随时还原。`);
    }, true);
    menu.append(actions); row.append(button, menu); element('books').append(row);
  }
  if (!element('books').children.length) element('books').append(node('p', search ? '没有找到这本作品' : '你的第一本书，等你落笔。', 'hint'));
}
async function shelf() { state.books = await api(`/api/books?kind=${encodeURIComponent(element('book-filter').value)}`); renderShelf(); }
async function showTrash() {
  openDialog('trash-dialog'); const box = element('trash-list'); box.replaceChildren(node('p', '正在加载…', 'hint'));
  const items = await api('/api/trash'); box.replaceChildren();
  if (!items.length) { empty(box, '回收站是空的', '移入回收站的作品会出现在这里。'); return; }
  for (const book of items) {
    const row = node('div', undefined, 'trash-row'); const info = node('div'); info.append(node('strong', book.title), node('p', `移入时间 ${new Date(book.deleted_at * 1000).toLocaleString()}`, 'hint'));
    const restore = node('button', '还原', 'primary small'); const remove = node('button', '彻底删除', 'danger small');
    restore.addEventListener('click', async () => { restore.disabled = true; try { await api(`/api/books/${book.book_id}/restore`, {}); await showTrash(); await shelf(); notice(`“${book.title}”已还原。`); } catch (e) { notice(e.message); restore.disabled = false; } });
    remove.addEventListener('click', async () => { if (!confirm(`彻底删除“${book.title}”及全部正文、版本、审稿记录？此操作无法撤销。`)) return; remove.disabled = true; try { await apiDelete(`/api/trash/${book.book_id}`); await showTrash(); notice('作品已彻底删除。'); } catch (e) { notice(e.message); remove.disabled = false; } });
    row.append(info, restore, remove); box.append(row);
  }
}
function renderPlanning() {
  const brief = state.book.brief; element('brief').replaceChildren(); element('plan').replaceChildren();
  if (!brief) empty(element('brief'), '故事约定还在等待建立', '开始任务后，Agent 会整理角色、叙述方式与终局方向。');
  else {
    for (const field of ['premise', 'audience', 'pov', 'style', 'ending']) { const card = node('div', undefined, 'detail-block'); card.append(node('span', labels[field], 'field-label'), node('p', brief[field])); element('brief').append(card); }
    element('brief').append(node('h3', '故事里的人', 'subheading')); const grid = node('div', undefined, 'character-grid');
    for (const character of brief.characters || []) { const card = node('div', undefined, 'detail-block'); card.append(node('h3', character.name)); for (const field of ['desire', 'fear', 'boundary', 'voice']) card.append(node('p', `${labels[field]}：${character[field]}`)); grid.append(card); } element('brief').append(grid);
  }
  if (!state.book.plan) return;
  element('plan').append(node('h3', '章节骨架', 'subheading'));
  for (const chapter of state.book.plan.chapters) { const card = node('details', undefined, 'outline-card'); const summary = node('summary'); summary.append(node('span', `第 ${chapter.number} 章`, 'outline-label'), node('span', chapter.title)); card.append(summary); if (chapter.pov) card.append(node('p', `本章视角：${chapter.pov}`)); for (const field of ['goal', 'conflict', 'change', 'payoff', 'emotion']) card.append(node('p', `${labels[field]}：${chapter[field]}`)); element('plan').append(card); }
  if (state.book.plan.promises.length) { element('plan').append(node('h3', '计划中的伏笔（尚非回收事实）', 'subheading')); for (const promise of state.book.plan.promises) { const card = node('div', undefined, 'detail-block'); card.append(node('strong', promise.key), node('p', `第 ${promise.setup_chapter} 章铺设 → 第 ${promise.due_chapter} 章计划回收`), node('p', promise.resolution)); element('plan').append(card); } }
}
function candidateChapter(run = state.status?.run) {
  if (!run?.candidate || !['running', 'paused', 'needs_attention'].includes(run.status)) return null;
  const reviewStages = ['extract', 'continuity', 'reader', 'arc', 'ending'];
  return { ...run.candidate, chapter_number: Number(run.chapter_number), word_count: countWords(run.candidate.body || ''), status: 'candidate', candidate: true, workflow_label: reviewStages.includes(run.stage) ? '审校中' : '草稿待审' };
}
function renderChapters() {
  element('chapters').replaceChildren();
  const visible = new Map(state.book.chapters.map(chapter => [chapter.chapter_number, chapter])); const candidate = candidateChapter();
  if (candidate) visible.set(candidate.chapter_number, candidate);
  const chapters = [...visible.values()].sort((left, right) => left.chapter_number - right.chapter_number);
  element('chapter-count-label').textContent = `${chapters.length} 章`; element('chapter-total').textContent = chapters.length;
  for (const chapter of chapters) {
    const button = node('button', undefined, `chapter-item ${state.chapter?.chapter_number === chapter.chapter_number ? 'selected' : ''}`);
    const workflow = chapter.candidate ? chapter.workflow_label : chapter.status === 'committed' ? '已定稿' : chapter.status === 'imported' ? '导入待审' : '待复查';
    const tokens = chapterTokenUsage(chapter.chapter_number);
    button.append(node('span', `第 ${String(chapter.chapter_number).padStart(2, '0')} 章`, 'chapter-index'), node('span', chapter.title), node('small', `${chapter.word_count.toLocaleString()} 字 · ${workflow} · ${formatTokens(tokens.reported_tokens)} tokens`));
    if (chapter.quality?.release_reason) button.append(node('span', selectedQualityLabel(chapter), 'chapter-state amber'));
    if (chapter.candidate) button.append(node('span', workflow, `chapter-state ${workflow === '审校中' ? 'amber' : ''}`));
    button.addEventListener('click', () => selectChapter(chapter)); element('chapters').append(button);
  }
}
function projectTags(value) { return value.split(/[，,、\n]/).map(item => item.trim()).filter(Boolean).slice(0, 16); }
function renderGenreLibrary() {
  const box = element('genre-library'); if (!box) return;
  const query = element('genre-library-search').value.trim().toLowerCase(); const selected = new Set(projectTags(element('project-genres').value)); box.replaceChildren();
  for (const [group, tags] of GENRE_LIBRARY) {
    const visible = tags.filter(tag => !query || tag.toLowerCase().includes(query)); if (!visible.length) continue;
    const section = node('section', undefined, 'genre-group'); section.append(node('strong', group)); const tagsBox = node('div', undefined, 'genre-tags');
    for (const tag of visible) { const button = node('button', tag, selected.has(tag) ? 'selected' : ''); button.type = 'button'; button.setAttribute('aria-pressed', String(selected.has(tag))); button.addEventListener('click', () => { const values = projectTags(element('project-genres').value); const index = values.indexOf(tag); if (index >= 0) values.splice(index, 1); else if (values.length < 16) values.push(tag); else { notice('题材标签最多选择 16 项。'); return; } element('project-genres').value = values.join('、'); renderGenreLibrary(); }); tagsBox.append(button); }
    section.append(tagsBox); box.append(section);
  }
  if (!box.children.length) box.append(node('p', '没有匹配题材，可以直接在上方填写自定义标签。', 'hint'));
}
function projectLines(value, expected, label) {
  return value.split('\n').map(line => line.trim()).filter(Boolean).map(line => {
    const parts = line.split(/[|｜]/).map(part => part.trim());
    if (parts.length !== expected || parts.some(part => !part)) throw new Error(`${label}每行请用“｜”分隔 ${expected} 项，且不要留空。`);
    return parts;
  });
}
function projectPayload(prefix = 'project-') {
  const characters = projectLines(element(`${prefix}characters`).value, 3, '人物种子').map(([name, role, note]) => ({ name, role, note }));
  const rules = projectLines(element(`${prefix}rules`).value, 2, '世界规则').map(([name, rule]) => ({ name, rule }));
  return {
    platform: element(`${prefix}platform`).value, length_band: element(`${prefix}length`).value,
    narrative_mode: element(`${prefix}narrative`).value, genre_tags: projectTags(element(`${prefix}genres`).value),
    target_reader: element(`${prefix}reader`).value.trim(), content_boundaries: element(`${prefix}boundaries`).value.trim(),
    seed_characters: characters, world_rules: rules, custom_notes: element(`${prefix}notes`).value.trim(), reference_tags: [],
  };
}
function projectIntake() { return projectPayload(); }
function bibleRows(value, required, label) {
  return value.split('\n').map(line => line.trim()).filter(Boolean).map(line => {
    const parts = line.split(/[|｜]/).map(part => part.trim());
    if (parts.length !== required || parts.slice(0, required - 1).some(part => !part)) throw new Error(`${label}每行请用“｜”分隔 ${required} 项，前 ${required - 1} 项不能为空。`);
    return parts;
  });
}
function fillStoryBibleEditor(book = state.book) {
  const brief = book?.brief || {}; const plan = book?.plan || {};
  const values = {
    title: brief.title || book?.title || '', premise: brief.premise || '', audience: brief.audience || '', pov: brief.pov || '', style: brief.style || '', ending: brief.ending || '',
    characters: (brief.characters || []).map(item => `${item.name}｜${item.desire}｜${item.fear}｜${item.boundary}｜${item.voice}`).join('\n'),
    chapters: (plan.chapters || []).map(item => `${item.number}｜${item.title}｜${item.goal}｜${item.conflict}｜${item.change}｜${item.payoff}｜${item.emotion}｜${item.pov || ''}`).join('\n'),
    promises: (plan.promises || []).map(item => `${item.key}｜${item.setup_chapter}｜${item.due_chapter}｜${item.resolution}｜${item.mandatory ? '是' : '否'}`).join('\n'),
  };
  for (const [name, value] of Object.entries(values)) element(`bible-${name}`).value = value;
}
function storyBiblePayload() {
  const characters = bibleRows(element('bible-characters').value, 5, '人物').map(([name, desire, fear, boundary, voice]) => ({ name, desire, fear, boundary, voice }));
  const chapters = bibleRows(element('bible-chapters').value, 8, '章节骨架').map(([number, title, goal, conflict, change, payoff, emotion, pov]) => ({
    number: Number(number), title, goal, conflict, change, payoff, emotion, ...(pov ? { pov } : {}),
  }));
  const promises = element('bible-promises').value.trim() ? bibleRows(element('bible-promises').value, 5, '伏笔').map(([key, setup, due, resolution, mandatory]) => {
    if (!['是', '否'].includes(mandatory)) throw new Error('伏笔最后一项请填写“是”或“否”。');
    return { key, setup_chapter: Number(setup), due_chapter: Number(due), resolution, mandatory: mandatory === '是' };
  }) : [];
  return {
    brief: { title: element('bible-title').value.trim(), premise: element('bible-premise').value.trim(), audience: element('bible-audience').value.trim(), pov: element('bible-pov').value.trim(), style: element('bible-style').value.trim(), ending: element('bible-ending').value.trim(), characters },
    plan: { chapters, promises },
  };
}
function openStoryBibleEditor() {
  if (!state.book?.brief || !state.book?.plan) { notice('请先完成创作约定和章节骨架，再编辑故事圣经。'); return; }
  fillStoryBibleEditor(); openDialog('story-bible-dialog'); notice('故事约定与章节骨架编辑器已打开。');
}
function fillProjectEditor(project = {}) {
  const values = {
    platform: project.platform || 'general', length: project.length_band || 'long', narrative: project.narrative_mode || 'limited_third',
    genres: (project.genre_tags || []).join('、'), reader: project.target_reader || '', boundaries: project.content_boundaries || '',
    characters: (project.seed_characters || []).map(item => `${item.name}｜${item.role}｜${item.note}`).join('\n'),
    rules: (project.world_rules || []).map(item => `${item.name}｜${item.rule}`).join('\n'), notes: project.custom_notes || '',
  };
  for (const [name, value] of Object.entries(values)) element(`edit-project-${name}`).value = value;
}
function openProjectEditor() {
  if (!state.book) return;
  fillProjectEditor(state.book.project);
  openDialog('project-dialog');
}
function renderOverview() {
  if (!state.book) return;
  const book = state.book; const project = book.project || {}; const status = state.status || {};
  const committed = status.chapters?.committed || book.chapters.filter(chapter => chapter.status === 'committed').length;
  const totalWords = book.chapters.reduce((sum, chapter) => sum + chapter.word_count, 0);
  element('overview-title').textContent = '故事摘要';
  element('overview-logline').textContent = book.brief?.premise || project.custom_notes || '补充创作资料，或开始规划，让 Agent 建立这本书的创作约定。';
  const metrics = [
    ['写作进度', `${committed} / ${book.settings.chapter_count}`, '章已定稿'],
    ['已保存正文', totalWords.toLocaleString(), '字'],
    ['审校记录', state.reviews.length, '条可追溯意见'],
    ['累计 Token', formatTokens(status.token_usage?.book_total), `${status.token_usage?.call_count || status.token_usage?.usage_records || 0} 次调用 · ${status.token_usage?.unknown_calls || 0} 次用量未知`],
  ];
  const metricBox = element('overview-metrics'); metricBox.replaceChildren();
  for (const [label, value, note] of metrics) { const card = node('div', undefined, 'metric-card'); card.append(node('span', label), node('strong', String(value)), node('small', note)); metricBox.append(card); }
  const reading = readingChapter();
  element('overview-reading-title').textContent = reading ? `第 ${reading.chapter_number} 章 · ${reading.title}` : '还没有正文';
  element('overview-reading-detail').textContent = reading ? `${reading.candidate ? '候选稿，尚未定稿' : reading.status === 'committed' ? '已定稿' : '待审稿'} · ${reading.word_count.toLocaleString()} 字` : '在右侧开始规划；有了候选稿，就可以进入正文阅读。';
  element('open-current-chapter').disabled = !reading;
  const health = element('overview-health'); health.replaceChildren();
  const healthRows = [['创作约定', book.brief ? '已建立' : '待建立'], ['章节骨架', book.plan ? '已规划' : '待规划'], ['必收伏笔', book.plan?.promises?.filter(item => item.mandatory).length || 0], ['当前状态', statuses[status.status || book.status] || status.status || book.status]];
  for (const [label, value] of healthRows) { const row = node('div', undefined, 'health-row'); row.append(node('span', label), node('strong', String(value))); health.append(row); }

  const materials = element('overview-materials'); materials.replaceChildren();
  const chips = node('div', undefined, 'material-groups');
  for (const tag of project.genre_tags || []) chips.append(node('span', tag, 'material-chip'));
  chips.append(node('span', { general: '通用电子书', fanqie: '番茄阅读', qidian: '起点中文网', jinjiang: '晋江文学城', changpei: '长佩文学', zhihu: '知乎盐言', other: '其他平台' }[project.platform] || '通用电子书', 'material-chip'));
  chips.append(node('span', { short: '短篇', medium: '中篇', long: '长篇', serial: '长连载' }[project.length_band] || '长篇', 'material-chip'));
  materials.append(chips);
  const list = node('div', undefined, 'material-list');
  const entries = [['人物种子', project.seed_characters || [], item => `${item.name}${item.role ? ` · ${item.role}` : ''}：${item.note}`], ['世界规则', project.world_rules || [], item => `${item.name}：${item.rule}`]];
  for (const [label, values, format] of entries) { if (!values.length) continue; const details = node('details'); details.append(node('summary', `${label} · ${values.length}`)); for (const value of values) details.append(node('p', format(value))); list.append(details); }
  if (project.target_reader) { const details = node('details'); details.append(node('summary', '目标读者'), node('p', project.target_reader)); list.append(details); }
  if (project.content_boundaries) { const details = node('details'); details.append(node('summary', '创作边界'), node('p', project.content_boundaries)); list.append(details); }
  if (!list.children.length) list.append(node('p', '还没有项目资料。开书时填写的人物、规则和创作边界会显示在这里。', 'project-empty'));
  materials.append(list);
}
function renderBook() {
  element('heading').textContent = state.book.title; element('breadcrumb-title').textContent = state.book.title;
  element('book-genre').textContent = state.book.settings.genre || '长篇小说'; element('chapter-total').textContent = state.book.chapters.length;
  element('chapter-count-label').textContent = `${state.book.chapters.length} 章`;
  const words = state.book.chapters.reduce((total, chapter) => total + chapter.word_count, 0);
  element('book-summary').textContent = `${words.toLocaleString()} 字已保存 · ${state.book.chapters.length} / ${state.book.settings.chapter_count} 章 · 累计 ${formatTokens(state.status?.token_usage?.book_total)} tokens · 版本 ${state.book.revision}`;
  element('book-kind').value = state.book.book_kind || 'user'; element('delete-sample').hidden = state.book.book_kind !== 'sample';
  renderPlanning(); renderChapters(); renderOverview();
  if (!state.chapter && state.book.chapters.length) selectChapter(state.book.chapters[0]);
  element('empty-chapters').hidden = !!state.chapter; element('editor').hidden = !state.chapter;
}
function selectChapter(chapter) {
  if (state.dirty && !confirm('当前修改尚未保存，切换章节会丢失编辑内容。继续？')) return;
  state.chapter = chapter; state.dirty = false; state.editRevision = state.book.revision;
  resetTTSForChapter(chapter);
  element('chapter-title').value = chapter.title; element('chapter-body').value = chapter.body;
  element('version-scope').textContent = `当前对比：第 ${chapter.chapter_number} 章 · ${chapter.title}。在写作页切换章节。`;
  const stage = state.status?.run?.stage;
  const tokenUsage = chapterTokenUsage(chapter.chapter_number);
  const tokenLabel = `累计 ${formatTokens(tokenUsage.reported_tokens)} tokens / ${tokenUsage.task_count} 次调用`;
  element('chapter-meta').textContent = chapter.candidate ? `第 ${chapter.chapter_number} 章 · 候选正文 · ${stages[stage] || '等待审校'} · ${tokenLabel} · 尚未定稿` : `第 ${chapter.chapter_number} 章 · ${chapter.status === 'committed' ? selectedQualityLabel(chapter) : chapter.status === 'imported' ? '原始导入稿，尚未审查' : '待重新审查'} · ${tokenLabel}`;
  element('feedback-chapter').textContent = chapter.status === 'committed' ? `当前绑定：第 ${chapter.chapter_number} 章 · 已定稿版本。反馈不会自动改写正文。` : `第 ${chapter.chapter_number} 章尚未定稿，暂不能录入真人反馈。`;
  element('editor-word-count').textContent = `${chapter.word_count.toLocaleString()} 字 · 标点不计`;
  element('revision-feedback').value = ''; element('save-state').textContent = '正文已保存';
  element('editor').hidden = false; element('empty-chapters').hidden = true; showLocalDraft(); renderChapters(); state.versions = []; renderVersions(); renderCandidateHistory(); if (!chapter.candidate) loadVersions(chapter.chapter_number); renderReviewAvailability(); controls();
}
async function selectBook(bookId) {
  if (state.dirty && !confirm('当前修改尚未保存，切换作品会丢失编辑内容。继续？')) return;
  const selection = ++state.selection; const book = await api(`/api/books/${bookId}`);
  if (selection !== state.selection) return;
  state.book = book; state.chapter = null; state.task = null; state.dirty = false; state.status = null; state.reviews = []; state.candidateVersions = []; state.facets = {}; state.versions = []; renderVersions(); renderCandidateHistory();
  element('downloads').hidden = true; element('task').textContent = ''; element('task-result').value = ''; renderContextInspector(null);
  empty(element('hits'), '从一个细节开始检索', '只有已通过审查的正文与记忆参与检索。', '⌕');
  storage('setItem', 'hulk-book', bookId); element('onboarding').hidden = true; element('workspace').hidden = false;
  switchTab(storage('getItem', `hulk-view-${bookId}`) || 'overview'); renderBook(); renderShelf(); memoryOffset = 0; await Promise.all([refreshStatus(), loadFacets(), loadMemoryReview()]);
}
async function loadMemoryReview() {
  if (!state.book) return;
  const bookId = state.book.book_id, offset = memoryOffset, filter = element('memory-review-filter').value;
  const base = `/api/books/${bookId}/memory`;
  const [proposals, maintenance] = await Promise.all([api(`${base}/proposals?status=${filter}&limit=20&offset=${offset}`), api(`${base}/maintenance?limit=5`)]);
  if (state.book?.book_id !== bookId || offset !== memoryOffset || filter !== element('memory-review-filter').value) return;
  const source = element('memory-source'), selected = source.value;
  source.replaceChildren();
  for (const chapter of state.book.chapters.filter(item => item.status === 'committed')) {
    const option = node('option', `第 ${chapter.chapter_number} 章 · ${chapter.title}`); option.value = chapter.version_id; source.append(option);
  }
  if ([...source.options].some(option => option.value === selected)) source.value = selected;
  const box = element('memory-proposals'); box.replaceChildren();
  if (!proposals.items.length) box.append(node('p', '这一页没有记录。', 'hint'));
  for (const proposal of proposals.items) {
    const data = proposal.candidate || {}, row = node('article', undefined, 'hit');
    row.append(node('strong', `${proposal.subject_display_name || data.subject_alias || data.subject_entity_id || '未绑定实体'} · ${data.predicate || '无法识别属性'}：${typeof data.value === 'string' ? data.value : JSON.stringify(data.value)}`));
    row.append(node('small', `第 ${data.source_chapter || '?'} 章 · 故事时间 ${data.story_valid_from ?? '未指定'} · ${data.visibility === 'author' ? '仅作者' : '读者可见'} · ${proposal.source_current ? '来源版本有效' : '来源失效或待检查'}`), node('blockquote', data.evidence || '无有效证据'));
    const scopeName = { objective: '客观状态', character_belief: '角色信念', author_plan: '作者计划' };
    row.append(node('small', `${scopeName[data.scope || 'objective'] || data.scope}${data.owner_entity_id ? ` · 所属角色：${proposal.owner_display_name || data.owner_entity_id}` : ''}${data.story_valid_to != null ? ` · 有效至故事时间 ${data.story_valid_to}` : ''}${data.subject_entity_id ? ` · 实体 ${data.subject_entity_id}` : ''}`));
    if (proposal.reason) row.append(node('p', `隔离原因：${proposal.reason}`, 'hint'));
    const act = (label, suffix, payload) => {
      const button = node('button', label); button.type = 'button';
      const requestId = crypto.randomUUID();
      button.addEventListener('click', async () => {
        button.disabled = true;
        try {
          await api(`${base}/proposals/${proposal.id}/${suffix}`, { ...payload(), expected_revision: proposals.revision, request_id: requestId });
          if (state.book?.book_id === bookId) { await refreshStatus(); await loadMemoryReview(); }
        } catch (error) { notice(error.message); button.disabled = false; }
      });
      row.append(button); return button;
    };
    if (proposal.status === 'pending' && proposal.source_current) {
      if (!data.subject_entity_id) {
        const select = node('select'); select.setAttribute('aria-label', '明确选择对应实体');
        for (const identity of proposal.identity_candidates) { const option = node('option', `${identity.display_name} · ${identity.id}`); option.value = identity.id; select.append(option); }
        row.append(select);
        const binding = act('绑定所选实体', 'bind', () => ({ entity_id: select.value })); binding.disabled = !select.options.length;
      } else {
        const trusted = node('input'); trusted.type = 'checkbox';
        const trustLabel = node('label', '我已核实原文与这条说法的含义', 'memory-check'); trustLabel.prepend(trusted); row.append(trustLabel);
        const conflict = node('input'); conflict.type = 'checkbox';
        if (proposal.conflicts.length) {
          row.append(node('p', `当前状态存在不同说法：${proposal.conflicts.map(item => JSON.stringify(item.value)).join('；')}`, 'hint'));
          const label = node('label', '确认这条新说法替代该时间的冲突状态', 'memory-check'); label.prepend(conflict); row.append(label);
        }
        const accept = act('接受事实', 'decision', () => ({ decision: 'accept', trust: trusted.checked, allow_conflict: conflict.checked }));
        const enable = () => { accept.disabled = !trusted.checked || (proposal.conflicts.length > 0 && !conflict.checked); };
        trusted.addEventListener('change', enable); conflict.addEventListener('change', enable); enable();
      }
    }
    if (proposal.status === 'pending') act('拒绝这条说法', 'decision', () => ({ decision: 'reject' }));
    box.append(row);
  }
  element('memory-page').textContent = `共 ${proposals.total} 条 · 第 ${Math.floor(offset / 20) + 1} 页`;
  element('memory-previous').disabled = offset === 0;
  element('memory-next').disabled = offset + 20 >= proposals.total;
  const counts = maintenance.counts;
  element('memory-maintenance-status').textContent = `待整理 ${counts.pending || 0} · 已整理 ${counts.ready || 0} · 仍待核实 ${counts.needs_verification || 0} · 已替换 ${counts.superseded || 0} · 失败 ${counts.failed || 0}`;
}
function renderMemoryFacets() {
  const box = element('memory-facets'); box.replaceChildren();
  const groups = ['全部', ...Object.keys(state.facets)];
  const tabs = node('div', undefined, 'facet-groups');
  for (const group of groups) { const button = node('button', group, state.memoryFacet === group ? 'active' : ''); button.type = 'button'; button.addEventListener('click', () => { state.memoryFacet = group; renderMemoryFacets(); }); tabs.append(button); }
  box.append(tabs);
  const values = state.memoryFacet === '全部' ? Object.values(state.facets).flat() : state.facets[state.memoryFacet] || [];
  if (!values.length) { box.append(node('p', '尚无已定稿记忆。完成章节审查后，人物、物品和约定会出现在这里。', 'hint')); return; }
  const chips = node('div', undefined, 'facet-values');
  for (const value of values) { const button = node('button', value); button.type = 'button'; button.addEventListener('click', () => { element('query').value = value; element('query').focus(); }); chips.append(button); }
  box.append(chips);
}
function renderContextInspector(task = state.task) {
  const inspector = element('context-inspector'); const sources = element('context-sources');
  inspector.hidden = !task?.task_id; sources.replaceChildren();
  if (!task?.task_id) return;
  const context = task.input?.context_diagnostics;
  if (context) {
    const group = node('section', undefined, 'context-group');
    group.append(node('span', `上下文用量 · ${context.mode === 'shadow' ? '影子测量' : context.mode === 'off' ? '旧策略' : '自适应装箱'}`, 'field-label'));
    group.append(node('p', `${context.estimated ? '保守估算' : 'Token 计数'} ${formatTokens(context.final_tokens)} · 软目标 ${formatTokens(context.soft_target)} · 输出预留 ${formatTokens(context.output_reserve)} · 其他开销预留 ${formatTokens(context.overhead_reserve)}`, 'hint'));
    group.append(node('p', `模型窗口：${context.context_window ? formatTokens(context.context_window) : '尚未配置'} · 整理后 ${formatTokens(context.organized_tokens)} · ${context.mode === 'shadow' ? '仅测量，当前请求未裁剪' : '各层可借用空余额度'}`, 'hint'));
    const layerNames = { core: '核心约定', plan: '本章规划', state: '状态', obligations: '义务', recent: '近期承接', history: '历史证据', stage: '阶段资料' };
    group.append(node('p', Object.entries(context.layer_tokens || {}).map(([key, value]) => `${layerNames[key] || key} ${formatTokens(value)}`).join(' · '), 'hint'));
    if (context.missing_hard_ids?.length) group.append(node('p', `缺少明确依赖：${context.missing_hard_ids.join('、')}`, 'hint'));
    sources.append(group);
  }
  const manifest = task.input?.context_manifest;
  if (!manifest) { element('context-summary').textContent = '当前任务没有可用上下文清单'; sources.append(node('p', '这不会授予额外检索权限。', 'hint')); return; }
  const role = manifest.role === 'reader' ? '读者任务' : '作者任务';
  element('context-summary').textContent = `${role} · 截至第 ${manifest.through_chapter} 章 · 作品版本 ${manifest.book_revision}`;
  const readerProfile = task.input?.reader_profile;
  if (readerProfile) sources.append(node('p', `审稿画像：${readerProfile.label || readerProfiles[readerProfile.id] || '独立读者'} · ${readerProfile.focus || ''}`, 'hint'));
  const project = task.input?.project;
  if (project) {
    const material = node('section', undefined, 'context-group');
    material.append(node('span', '作者资料（尚非正史）', 'field-label'));
    const descriptors = [
      project.platform && `平台：${project.platform}`,
      project.narrative_mode && `叙事：${project.narrative_mode}`,
      project.genre_tags?.length && `题材：${project.genre_tags.join(' / ')}`,
      project.target_reader && `读者：${project.target_reader}`,
      project.content_boundaries && '已设内容边界',
      project.seed_characters?.length && `${project.seed_characters.length} 个人物种子`,
      project.world_rules?.length && `${project.world_rules.length} 条世界规则`,
    ].filter(Boolean);
    material.append(node('p', descriptors.join(' · ') || '尚未填写作者资料。', 'hint'));
    material.append(node('p', '这些是本任务的创作约定；须由已确认骨架或正文建立后，才能进入正史。', 'hint'));
    sources.append(material);
  }
  const obligations = task.input?.promise_obligations || [];
  if (obligations.length) {
    const obligationGroup = node('section', undefined, 'context-group'); obligationGroup.append(node('span', `到期承诺 · ${obligations.length}`, 'field-label'));
    for (const obligation of obligations) obligationGroup.append(node('p', `${obligation.urgency === 'due' ? '本章到期' : `第 ${obligation.due_chapter} 章临近`}：${obligation.key} · ${obligation.resolution}`));
    sources.append(obligationGroup);
  }
  const arcChapters = task.input?.arc_chapters || [];
  if (arcChapters.length) sources.append(node('p', `故事弧审校范围：第 ${arcChapters[0].chapter_number}–${arcChapters[arcChapters.length - 1].chapter_number} 章 · ${arcChapters.length} 章已定稿正文`, 'hint'));
  const pov = manifest.pov_context;
  if (pov?.pov) sources.append(node('p', `本章 POV：${pov.pov} · 已屏蔽 ${pov.withheld_knowledge_count || 0} 条其他角色认知`, 'hint'));
  else if (pov) sources.append(node('p', '本章未设置 POV，未启用角色认知过滤。', 'hint'));
  const groups = [
    ['必须带入', manifest.canonical_sources?.filter(source => source.tier === 'required') || []],
    ['补充召回', manifest.canonical_sources?.filter(source => source.tier === 'supplementary') || []],
    ['未直接带入的正史索引', manifest.canonical_sources?.filter(source => source.tier === 'canonical') || []],
    ['近期正文', manifest.recent_chapter_sources || []],
  ];
  for (const [label, entries] of groups) {
    const group = node('section', undefined, 'context-group'); group.append(node('span', `${label} · ${entries.length}`, 'field-label'));
    if (!entries.length) group.append(node('p', '本任务没有这一层来源。', 'hint'));
    for (const entry of entries) {
      const meta = entry.key ? `${entry.kind} · ${entry.key}` : entry.title || '章节正文';
      const source = entry.chapter_number ? `第 ${entry.chapter_number} 章${entry.reason ? ` · ${entry.reason}` : ''}` : '无历史章节来源';
      const row = node('div', undefined, 'context-source'); row.append(node('strong', meta), node('small', source)); group.append(row);
    }
    sources.append(group);
  }
}
async function loadFacets() {
  if (!state.book) return; const bookId = state.book.book_id; const result = await api(`/api/books/${bookId}/memory-facets`);
  if (state.book?.book_id !== bookId) return; state.facets = result.facets; renderMemoryFacets();
}
function renderVersions() {
  const box = element('version-history'); box.replaceChildren();
  if (!state.chapter) { empty(box, '选择章节查看版本', '版本变化会按章节保存。', '↺'); return; }
  if (state.chapter.candidate) { empty(box, '候选稿还没有正文版本', '它正在审校，尚未替换任何已定稿正文。', '↺'); return; }
  if (!state.versions.length) { empty(box, '这章还没有定稿版本', '候选稿通过审稿后才会写入版本历史。', '↺'); return; }
  const no_text_change = version => version.version_number > 1 && !version.change.details?.length && !version.change.title_changed;
  const visibleVersions = state.versions.filter(version => !no_text_change(version));
  if (!visibleVersions.length) { empty(box, '暂无正文改版', '重新审查记录已保留在“审稿意见”中。', '↺'); return; }
  for (const version of [...visibleVersions].reverse()) {
    const card = node('section', undefined, 'version-card'); const row = node('div', undefined, 'version-row');
    row.append(node('strong', `版本 ${version.version_number} · ${version.title}`), node('span', version.change.summary), node('small', `${version.word_count.toLocaleString()} 字 · ${new Date(version.created_at * 1000).toLocaleString()}`)); card.append(row);
    if (version.change.details?.length) {
      const diff = node('section', undefined, 'version-diff'); diff.append(node('p', `与版本 ${version.version_number - 1} 对比`, 'diff-heading'));
      for (const item of version.change.details) {
        const block = node('div', undefined, 'diff-block'); block.append(node('strong', `${item.type}的段落`)); const columns = node('div', undefined, 'diff-columns');
        if (item.before) { const before = node('article', undefined, 'diff-before'); before.append(node('small', '修改前', 'diff-label'), node('blockquote', item.before)); columns.append(before); }
        if (item.after) { const after = node('article', undefined, 'diff-after'); after.append(node('small', '修改后', 'diff-label'), node('blockquote', item.after)); columns.append(after); }
        block.append(columns);
        diff.append(block);
      }
      card.append(diff);
    }
    box.append(card);
  }
}
function renderCandidateHistory() {
  const box = element('candidate-history'); box.replaceChildren();
  const number = state.chapter?.chapter_number;
  const history = number ? state.candidateVersions.filter(item => item.chapter_number === number) : state.candidateVersions;
  if (!history.length) { empty(box, '还没有候选稿改版记录', '候选正文写出后，AI 初稿、返修稿和可比较的修改会先保留在这里。', '↺'); return; }
  for (const candidate of [...history].reverse()) {
    const card = node('section', undefined, 'version-card candidate-version-card'); const row = node('div', undefined, 'version-row');
    const source = candidate.current ? `${candidate.source} · 当前` : candidate.source;
    row.append(node('strong', `第 ${candidate.chapter_number} 章 · 候选稿 ${candidate.version_number} · ${candidate.title}`), node('span', candidate.change.summary), node('small', `${candidate.word_count.toLocaleString()} 字 · ${source} · ${new Date(candidate.created_at * 1000).toLocaleString()}`)); card.append(row);
    if (candidate.supplement_warning) card.append(node('p', `返修正文已保留；附加说明未采信：${candidate.supplement_warning.message}`, 'hint'));
    if (candidate.stage === 'revise') {
      const response = node('details'); response.append(node('summary', 'AI 逐项返修说明'));
      response.append(node('p', '以下是 AI 的修改说明，仍需对照正文和后续审校验证。', 'hint'));
      if (!candidate.revision_response) response.append(node('p', '此版本未提供逐项说明，不能据此判断意见已解决。'));
      for (const item of candidate.revision_response || []) {
        const request = (candidate.feedback_items || []).find(value => value.feedback_id === item.feedback_id);
        response.append(node('strong', `${item.feedback_id} · ${item.status === 'changed' ? '报告已修改' : '未修改'}`), node('p', request?.request || '原始要求未记录'), node('p', item.explanation));
        if (item.evidence) response.append(node('blockquote', item.evidence));
      }
      card.append(response);
    }
    if (candidate.change.details?.length) {
      const diff = node('section', undefined, 'version-diff'); diff.append(node('p', `与候选稿 ${candidate.version_number - 1} 对比`, 'diff-heading'));
      for (const item of candidate.change.details) {
        const block = node('div', undefined, 'diff-block'); block.append(node('strong', `${item.type}的段落`)); const columns = node('div', undefined, 'diff-columns');
        if (item.before) { const before = node('article', undefined, 'diff-before'); before.append(node('small', '修改前', 'diff-label'), node('blockquote', item.before)); columns.append(before); }
        if (item.after) { const after = node('article', undefined, 'diff-after'); after.append(node('small', '修改后', 'diff-label'), node('blockquote', item.after)); columns.append(after); }
        block.append(columns); diff.append(block);
      }
      card.append(diff);
    }
    box.append(card);
  }
}
async function loadVersions(number) {
  if (!state.book) return; const bookId = state.book.book_id;
  try { const result = await api(`/api/books/${bookId}/chapters/${number}/versions`); if (state.book?.book_id === bookId && state.chapter?.chapter_number === number) { state.versions = result.versions; renderVersions(); } }
  catch (error) { if (state.book?.book_id === bookId) notice(error.message); }
}
function renderReviews() {
  element('reviews').replaceChildren(); element('review-total').textContent = state.reviews.length;
  if (!state.reviews.length) { empty(element('reviews'), '还没有已完成的审稿', '稿件保存不等于质量通过。执行连续性审查与读者审稿后，意见会保留在这里。', '✓'); return; }
  for (const review of [...state.reviews].reverse()) {
    const result = review.result; const passed = result.verdict === 'pass' && !result.issues.some(issue => ['blocker', 'major'].includes(issue.severity));
    const card = node('div', undefined, 'review-card'); const header = node('div', undefined, 'review-header');
    const profileAttempt = review.profile_attempt ? ` · 画像第 ${review.profile_attempt} 次` : '';
    const candidateRevision = review.candidate_revision ? `候选稿第 ${review.candidate_revision} 稿 · ` : '';
    const source = review.source === 'human' ? '真人反馈' : 'AI 自动审稿';
    header.append(node('strong', `第 ${review.chapter_number} 章 · ${candidateRevision}${review.reviewer} · 本阶段第 ${review.attempt} 次${profileAttempt}`), node('span', source, `pill ${review.source === 'human' ? 'human' : 'ai'}`), node('span', passed ? '通过' : '需要修改', `pill ${passed ? 'green' : 'amber'}`));
    const feedbackSignals = review.source === 'human' ? ` · ${review.rating ? `${review.rating} / 5 分` : '未评分'} · ${review.would_continue === null ? '未说明是否续读' : review.would_continue ? '愿意继续读' : '暂不愿继续读'}` : '';
    card.append(header, node('div', `${stages[review.stage]} · ${review.version_label}${feedbackSignals} · ${new Date(review.created_at * 1000).toLocaleString()}`, 'review-meta'), node('p', result.notes || '无补充意见'));
    if (review.supplement_warning) card.append(node('p', `附加核验未采信：${review.supplement_warning.message} 常规审稿问题仍按原标准处理。`, 'hint'));
    if (review.revision_check) {
      const verification = node('details'); verification.append(node('summary', '返修效果核验 · AI 连续性审校'));
      verification.append(node('p', '这是审校模型的判断，原文引文已做存在性检查；不等于作者认可或文学质量保证。', 'hint'));
      if (!result.revision_verification) verification.append(node('p', '本次审校未提供逐项核验，不能视为修改要求已全部解决。'));
      for (const item of result.revision_verification || []) {
        const request = review.revision_check.feedback_items.find(value => value.feedback_id === item.feedback_id);
        verification.append(node('strong', `${item.feedback_id} · ${{verified:'已核验',unresolved:'仍未满足',uncertain:'无法确认'}[item.status]}`), node('p', request?.request || '要求未记录'), node('p', item.explanation));
        if (item.evidence) verification.append(node('blockquote', item.evidence));
      }
      card.append(verification);
    }
    const tracking = review.issue_tracking;
    if (tracking) {
      const labels = {new: '首次记录', repeated: '再次提出', reappeared: '重新出现'};
      const details = node('details', undefined, 'review-issue-tracking');
      details.append(node('summary', `问题追踪 · ${tracking.current.length} 项本轮意见 · ${tracking.not_repeated.length} 项本轮未再提出`));
      details.append(node('p', tracking.previous_task_id ? '与同一审稿角色、同一规划版本的上次审查比较。未再提出不等于已解决；表述变化也可能被记为新问题。' : '这是当前审稿角色与规划版本下的首份记录。', 'hint'));
      for (const issue of tracking.current) details.append(node('p', `${issue.issue_id} · ${labels[issue.state]} · ${issue.dimension}`));
      for (const issue of tracking.not_repeated) details.append(node('p', `${issue.issue_id} · 本轮未再提出 · ${issue.dimension}`));
      card.append(details);
    }
    for (const issue of result.issues) { const item = node('div', undefined, 'review-issue'); item.append(node('strong', `${{ blocker: '阻断问题', major: '重要问题', medium: '中等建议', minor: '小问题'  }[issue.severity]} · ${issue.dimension}`), node('blockquote', issue.evidence), node('p', issue.explanation), node('p', `修改建议：${issue.suggestion}`)); card.append(item); }
    element('reviews').append(card);
  }
}
function stageTaskLabel(run, execution) {
  if (run?.quality_policy?.mode !== 'bounded' && run?.reviews?.at(-1)?.adjudication_pending && execution.stage === 'continuity') return `第 ${execution.chapter_number} 章 · 独立审稿裁决`;
  const chapter = `第 ${execution.chapter_number} 章`;
  if (execution.stage === 'continuity' && run?.quality_policy?.mode === 'bounded') return `${chapter} · 综合AI审稿（最多3次）`;
  if (execution.stage === 'revise') return `${chapter} · 第 ${Number(run?.attempts || 0) + 1} 次候选稿返修`;
  if (['continuity', 'reader', 'arc', 'ending'].includes(execution.stage)) return `${chapter} · 第 ${Number(run?.attempts || 0) + 1} 轮 ${stages[execution.stage]}`;
  return `${chapter} · ${stages[execution.stage] || execution.stage}`;
}
function diagnosisText(details) {
  if (!details) return '';
  if (Number.isFinite(details.count) && Number.isFinite(details.target) && Number.isFinite(details.min) && Number.isFinite(details.max)) {
    return `正文字数校验：本次返回 ${details.count} 字，目标 ${details.target} 字，当次范围 ${details.min}–${details.max} 字。需要${details.count < details.min ? '补足场景与行动过程' : '压缩冗余内容'}；重试会带入字数反馈，不是修复协议或原样重放。`;
  }
  const phases = {manuscript_length: '正文字数校验', result_validation: '任务结果字段校验', decode_envelope: '服务商响应结构', memory_validation: '记忆来源校验', model_output: '模型输出长度限制', http_response: '服务端响应', connect: '建立连接', waiting_response: '等待模型响应', transport: '传输请求', decode_result: '解析结果', waiting_for_structured_result: '等待宿主结构化结果'};
  const fields = (details.errors || []).slice(0, 3).map(error => `${(error.path || []).join('.') || '根对象'}：${error.message}`).join('；');
  return `${fields ? `错误字段：${fields}。` : ''}${details.phase ? `失败环节：${phases[details.phase] || details.phase}。` : ''}${details.line ? `解析位置：第 ${details.line} 行、第 ${details.column} 列。` : ''}${details.next_action ? `下一步：${details.next_action}` : ''}`;
}
function renderExecutionSummary() {
  const box = element('execution-summary'); const title = element('execution-outcome'); const detail = element('execution-detail');
  const execution = state.status?.execution; const run = state.status?.run;
  box.className = 'execution-summary';
  if (run?.status === 'awaiting_author') { title.textContent = '本章已保存 · 等待作者确认'; detail.textContent = '在所选章节面板确认通过，或提出修改意见。审稿上限放行的章节会保留提示标签。'; return; }
  const session = state.status?.worker_session;
  if (session?.status === 'interrupted' || (session?.status === 'failed' && !execution?.failure)) {
    box.classList.add('is-failed'); title.textContent = session.status === 'interrupted' ? '工作台执行已中断' : '执行器未完成';
    detail.textContent = `${session.error?.code || 'WORKER_ERROR'} · ${session.error?.message || '请检查连接后恢复。'} 当前保留在第 ${run?.chapter_number || 1} 章 · ${stages[run?.stage] || '等待开始'}。点击“恢复当前阶段”继续。`;
    return;
  }
  const blocker = state.status?.blocker;
  if (blocker?.code === 'CONTEXT_CAPACITY') {
    const context = blocker.context || {};
    box.classList.add('is-failed'); title.textContent = `尚未启动：第 ${blocker.chapter_number} 章 · ${stages[blocker.stage]}`;
    const missing = context.missing_hard_ids?.length ? `待补齐依赖：${context.missing_hard_ids.join('、')}。` : '';
    detail.textContent = `必要资料 ${formatTokens(context.hard_tokens)} · 输出预留 ${formatTokens(context.output_reserve)} · 其他开销预留 ${formatTokens(context.overhead_reserve)} · 模型窗口 ${context.context_window ? formatTokens(context.context_window) : '尚未配置'}。${missing}请核对模型容量或调整本章规划，已保存正文保留。`;
    return;
  }
  if (blocker?.code === 'CONTEXT_LIMIT') {
    box.classList.add('is-failed'); title.textContent = `尚未启动：第 ${blocker.chapter_number} 章 · ${stages[blocker.stage]}`;
    detail.textContent = `本地上下文检查拦截，尚未调用模型。当前请求 ${(blocker.input_bytes / 1024).toFixed(1)} KB，上限 ${(blocker.limit_bytes / 1024).toFixed(1)} KB。已保存正文和记忆均保留；这不是上一任务失败，也不是 Token 预算不足。`;
    return;
  }
  if (blocker?.code === 'BUDGET_LIMIT') {
    box.classList.add('is-failed'); title.textContent = `尚未启动：第 ${blocker.chapter_number} 章 · ${stages[blocker.stage]}`;
    detail.textContent = `本轮预算已拦截下一任务，未调用模型。累计预留 ${blocker.reserved_tokens.toLocaleString()} / ${blocker.budget_tokens.toLocaleString()} Token；下一步需预留 ${blocker.next_reservation.toLocaleString()} Token。步骤 ${blocker.steps} / ${blocker.max_steps}。请调整预算后继续，已完成章节不受影响。`;
    return;
  }
  if (!execution) { title.textContent = '暂无任务结果'; detail.textContent = '开始后会显示任务、阶段和执行结果。'; return; }
  const task = stageTaskLabel(run, execution);
  if (execution.outcome === 'running') {
    box.classList.add('is-running'); title.textContent = `AI 正在执行：${task}`;
    detail.textContent = `已运行 ${formatElapsed(execution.started_at)}。正在等待当前模型返回处理结果；可暂停，完成后会自动进入下一阶段。`;
  } else if (execution.outcome === 'succeeded' && run?.status === 'needs_attention') {
    box.classList.add('is-failed'); title.textContent = `结果已收到，需要处理：${task}`;
    detail.textContent = run.reason || '当前阶段尚未通过，请查看审稿意见。';
  } else if (execution.outcome === 'succeeded') {
    box.classList.add('is-success'); title.textContent = `已完成：${task}`;
    detail.textContent = execution.next_stage ? `已收到结构化结果，下一步：${stages[execution.next_stage] || execution.next_stage}。` : '已收到结构化结果。';
  } else if (execution.outcome === 'failed') {
    box.classList.add('is-failed'); title.textContent = `未完成：${task}`;
    const failure = execution.failure || {}; const timeout = execution.failure_details?.timeout_seconds;
    const duration = timeout ? `等待 ${formatSeconds(execution.elapsed_seconds)}，已达到 ${formatSeconds(timeout)} 的完整结果超时上限。` : `已运行 ${formatSeconds(execution.elapsed_seconds)}。`;
    detail.textContent = `${failure.code || 'EXECUTION_FAILED'} · ${failure.message || '任务未完成。'} ${duration} 候选稿与当前正文均已保留。${diagnosisText(execution.failure_details)}${execution.failure_details?.max_output_tokens ? ` 输出上限 ${execution.failure_details.max_output_tokens} Token；已报告输出 ${execution.failure_details.completion_tokens ?? '未知'}，其中推理 ${execution.failure_details.reasoning_tokens ?? '未知'}。` : ''}`;
  } else {
    box.classList.add('is-failed'); title.textContent = `已中止：${task}`;
    detail.textContent = `任务状态：${statuses[execution.outcome] || execution.outcome}。可检查原因后恢复或新开返修。`;
  }
}
function renderReviewAvailability() {
  const run = state.status?.run; const candidate = candidateChapter(run); const committed = state.chapter?.status === 'committed';
  const reviewStages = ['extract', 'continuity', 'reader', 'arc', 'ending']; const isAiReview = reviewStages.includes(run?.stage);
  const stageTitle = element('review-stage-title'); const stageDetail = element('review-stage-detail');
  if (committed) {
    stageTitle.textContent = '真人反馈已解锁'; stageDetail.textContent = `第 ${state.chapter.chapter_number} 章已定稿。你可以录入读者、编辑或作者的反馈；反馈会绑定本次正文版本。`;
  } else if (needsAuthorRevision(run)) {
    stageTitle.textContent = '等待作者决定如何返修'; stageDetail.textContent = `第 ${run.chapter_number} 章已完成本轮 AI 审校，但未通过且自动返修达到上限。先查看下方审稿意见和候选稿差异，再提交明确的修改要求。`;
  } else if (candidate && isAiReview) {
    stageTitle.textContent = `AI 自动审校中 · ${stages[run.stage]}`; stageDetail.textContent = `第 ${run.chapter_number} 章候选稿已保存。AI 审校完成并定稿前，不录入真人反馈，避免意见绑到尚未确定的正文。`;
  } else if (candidate) {
    stageTitle.textContent = 'AI 正在准备候选稿'; stageDetail.textContent = `第 ${run.chapter_number} 章显示为草稿待审。完成正文与 AI 审校后，才会进入真人反馈。`;
  } else {
    stageTitle.textContent = '等待候选正文'; stageDetail.textContent = 'Agent 完成候选正文后会进入 AI 自动审校。';
  }
  element('human-feedback-form').hidden = !committed;
  element('human-feedback-locked').hidden = committed;
  element('human-feedback-locked-detail').textContent = committed ? '' : candidate ? `当前是第 ${candidate.chapter_number} 章候选稿，AI 自动审校尚未完成。真人反馈将在本章定稿后解锁。` : '请先完成候选正文与 AI 自动审校；真人反馈将在本章定稿后解锁。';
}
function needsAuthorRevision(run) {
  if (run?.status !== 'needs_attention') return false;
  return /审稿未通过|返修上限|全书结构问题/.test(run.reason || '');
}
function budgetOptions(tokens, steps) {
  const options = {};
  if (tokens.trim()) options.budget_tokens = Number(tokens);
  if (steps.trim()) options.max_steps = Number(steps);
  return options;
}
function workflowPresentation(status) {
  const run = status.run; const execution = status.execution;
  const existing = !!run && ['running', 'paused', 'needs_attention', 'awaiting_author'].includes(run.status);
  const chapter = run?.chapter_number;
  const stage = stages[run?.stage] || '写作';
  const range = existing ? (chapter === run.end_chapter ? `继续完成第 ${chapter} 章` : `从第 ${chapter} 章继续至第 ${run.end_chapter} 章`) : '';
  const failed = existing && execution?.failure && execution.chapter_number === chapter && execution.stage === run.stage;
  let label = '开始本轮写作';
  if (status.status === 'complete') label = '全书已完成';
  else if (run?.status === 'awaiting_author') label = `等待作者确认第 ${chapter} 章`;
  else if (status.worker_running) label = run.stage === 'revise' ? `第 ${chapter} 章 · 第 ${Number(run.attempts || 0) + 1} 次候选稿返修中…` : `第 ${chapter} 章 · ${stage}中…`;
  else if (status.active_task) label = `等待宿主提交第 ${chapter} 章`;
  else if (run?.status === 'needs_attention' && /审稿未通过|返修上限|全书结构问题/.test(run.reason || '')) label = '查看返修意见';
  else if (status.blocker?.code === 'BUDGET_LIMIT') label = '调整预算后继续';
  else if (failed) label = `重试第 ${chapter} 章 · ${stage}`;
  else if (existing) label = run.stage === 'draft' && !run.candidate ? `开始写第 ${chapter} 章` : `继续第 ${chapter} 章 · ${stage}`;
  return {existing, range, label, notice: existing ? `已提交${failed ? '重试' : '续写'}：第 ${chapter} 章 · ${stage}。${range}；可随时暂停。` : '本轮写作已启动，进度与结果会自动刷新。'};
}
function renderStatus() {
  const status = state.status; const run = status.run; const count = status.chapters.committed || 0;
  element('book-status').textContent = statuses[status.status] || status.status;
  element('book-status').className = `pill ${status.status === 'complete' ? 'green' : ''}`;
  element('agent-mode').textContent = state.executor === 'claude' ? 'Claude Code 执行器' : state.worker ? 'API 执行器' : '宿主协作模式';
  element('agent-dot').classList.toggle('running', !!status.worker_running);
  const reviewStages = ['extract', 'continuity', 'reader', 'arc', 'ending']; const isAiReview = reviewStages.includes(run?.stage);
  element('progress-title').textContent = run ? `第 ${run.chapter_number} 章 · ${isAiReview ? 'AI 自动审校 · ' : ''}${stages[run.stage] || run.stage}` : '等待开始';
  element('progress-detail').textContent = `${count} / ${status.target_chapters} 章已审查${run ? ` · ${run.steps} 次任务` : ''}`;
  element('progress').max = status.target_chapters; element('progress').value = count;
  const leaseExpired = status.execution?.failure?.code === 'TASK_LEASE_EXPIRED' && !status.worker_running;
  const pausedReason = run?.status === 'paused' && (status.worker_error?.message || run.reason);
  const revisionGate = needsAuthorRevision(run);
  const workflowActive = run && ['running', 'paused', 'needs_attention'].includes(run.status);
  element('revision-panel').hidden = Boolean(workflowActive && !revisionGate);
  const hostTimeout = run?.status === 'paused' && /宿主.*超时/.test(pausedReason || '');
  const activeElapsed = status.active_task?.created_at ? `已运行 ${formatElapsed(status.active_task.created_at)}` : '刚刚开始';
  const timeoutLimit = status.execution?.failure_details?.timeout_seconds ?? status.execution?.elapsed_seconds;
  const timeoutElapsed = status.execution?.elapsed_seconds;
  element('reason').textContent = leaseExpired ? `第 ${run.chapter_number} 章 · ${stages[run.stage]} 的执行租约已到期，未收到有效结果。点击“恢复当前阶段”重新接续，已保存的稿件仍在。` : revisionGate ? `第 ${run.chapter_number} 章候选稿未通过 AI 审校，且已达到自动返修上限。候选稿与审稿意见都已保留；请查看返修意见，填写具体修改要求后提交“生成返修稿”。` : hostTimeout ? `Claude Code 在“${stages[run.stage]}”阶段等待完整结构化结果 ${formatSeconds(timeoutElapsed)}，达到 ${formatSeconds(timeoutLimit)} 的上限后已安全暂停。候选稿和反馈均已保留；点击“${workflowPresentation(status).label}”会从本阶段重新执行。` : pausedReason ? `AI 自动${isAiReview ? '审校' : '处理'}在${stages[run.stage]}阶段暂停：${pausedReason}。候选稿已保留；点击“${workflowPresentation(status).label}”会从此阶段继续。` : status.worker_error?.message || run?.reason || (status.worker_running ? `AI 正在${isAiReview ? '自动审校' : '处理'}当前任务，${activeElapsed}。处理中，请等待或暂停；完成后会自动刷新。` : status.active_task ? `当前任务已由 ${status.active_task.worker_id} 领取，${activeElapsed}，等待提交。` : status.status === 'complete' ? '本书已完成审查，可导出或按章节返修。' : state.worker ? '执行器已配置，可按本轮范围继续创作。' : '未配置自动执行器。可由 Claude Code、Codex 或 DSH 领取任务接续。');
  element('pipeline').replaceChildren();
  const groups = [['规划', ['brief', 'outline']], ['写作', ['draft', 'revise']], ['审查', ['extract', 'continuity', 'reader', 'arc']], ['归档', ['ending']]];
  groups.forEach(([label, values], index) => { const step = node('span', undefined, values.includes(run?.stage) ? 'current' : ''); step.append(node('i', String(index + 1)), node('span', label)); element('pipeline').append(step); });
  const presentation = workflowPresentation(status);
  const continueLabel = presentation.label;
  element('continue').textContent = continueLabel;
  element('batch-size').hidden = presentation.existing;
  element('batch-range-label').textContent = presentation.existing ? '当前断点与续写范围' : '新一轮写作范围';
  element('resume-range').hidden = !presentation.existing;
  element('resume-range').textContent = presentation.range;
  element('resume-range-hint').hidden = !presentation.existing;
  if (presentation.existing && !status.worker_running && !status.active_task && !run.reason && !status.execution?.failure) element('reason').textContent = `${presentation.range}，将从“${stages[run.stage]}”阶段接续，已完成章节不会重写。`;
  const visibleCandidate = candidateChapter(run);
  if (visibleCandidate && (!state.chapter || (state.chapter.chapter_number === visibleCandidate.chapter_number && !state.chapter.candidate)) && !state.dirty) selectChapter(visibleCandidate);
  else renderChapters();
  renderReviewAvailability();
  renderExecutionSummary();
  renderEvents(); renderUsage();
  if (run) { element('budget-tokens').placeholder = String(run.budget_tokens); element('max-steps').placeholder = String(run.max_steps); }
  controls();
}
function eventCategory(event) { return ['revision_requested', 'chapter_committed', 'human_feedback_submitted', 'needs_attention'].includes(event.kind) || event.payload.stage === 'reader' || event.payload.stage === 'continuity' ? 'review' : 'execution'; }
function eventDetail(event) {
  const payload = event.payload || {}; const parts = [];
  if (payload.chapter_number) parts.push(`第 ${payload.chapter_number} 章`);
  if (payload.stage) parts.push(stages[payload.stage] || payload.stage);
  if (payload.kind) parts.push(payload.kind === 'sample' ? '测试样例' : payload.kind);
  if (payload.reason) parts.push(payload.reason);
  if (payload.outcome) parts.push(statuses[payload.outcome] || ({failed: '执行失败', waiting: '等待宿主提交', interrupted: '服务中断'})[payload.outcome] || payload.outcome);
  if (payload.message) parts.push(payload.message);
  if (payload.error?.message) parts.push(payload.error.message);
  return parts.join(' · ');
}
function renderEvents() {
  const box = element('events'); box.replaceChildren(); const filter = element('activity-filter').value;
  const rows = (state.status?.events || []).filter(event => filter === 'all' || eventCategory(event) === filter);
  if (!rows.length) { empty(box, '没有这类记录', '切换筛选器，或等待下一次处理。', '·'); return; }
  for (const event of rows) { const row = node('div', undefined, 'event'); const text = node('div'); text.append(node('strong', events[event.kind] || event.kind)); const detail = eventDetail(event); if (detail) text.append(node('small', detail)); row.append(node('time', new Date(event.created_at * 1000).toLocaleString()), text); box.append(row); }
}
async function refreshStatus() {
  if (!state.book || state.polling) return;
  state.polling = true; const bookId = state.book.book_id; const selection = state.selection;
  try {
    const includeHistory = element('review-scope').value === 'history';
    const [status, history, candidates] = await Promise.all([api(`/api/books/${bookId}/status`), api(`/api/books/${bookId}/reviews?include_history=${includeHistory}`), api(`/api/books/${bookId}/candidates`)]);
    if (state.book?.book_id !== bookId || state.selection !== selection) return;
    state.status = status; state.reviews = history.reviews; state.candidateVersions = candidates.candidates; renderStatus(); renderReviews(); renderCandidateHistory(); renderOverview();
    const summary = state.books.find(book => book.book_id === bookId); if (summary) { summary.status = status.status; renderShelf(); }
    if (status.revision !== state.book.revision) {
      if (state.dirty) { element('save-state').textContent = '作品已更新 · 本地编辑保留'; return; }
      const book = await api(`/api/books/${bookId}`);
      if (state.book?.book_id !== bookId || state.selection !== selection || state.dirty) return;
      const chapterNumber = state.chapter?.chapter_number; state.book = book; state.chapter = null; renderBook();
      const chapter = book.chapters.find(item => item.chapter_number === chapterNumber); if (chapter) selectChapter(chapter);
    }
  } finally { state.polling = false; }
}
async function continueWriting() {
  if (!state.book) return;
  const capabilities = await api('/api/capabilities');
  state.api = capabilities.api_configured; state.worker = capabilities.worker_configured; state.executor = capabilities.executor;
  if (!state.worker) { notice(capabilities.configuration_error?.message || '尚未配置自动执行器，请在 AI 配置中选择本机 Claude Code 或 API。配置后点击继续写作即可自动推进。'); openAISettings(); return; }
  const bookId = state.book.book_id; const status = await api(`/api/books/${bookId}/status`); const run = status.run; const presentation = workflowPresentation(status);
  if (status.active_task && status.active_task.worker_id !== 'gui' && !status.worker_running) {
    notice(`第 ${status.active_task.chapter_number} 章 · ${stages[status.active_task.stage]} 已由宿主领取，等待提交。租约到期时间：${new Date(status.active_task.lease_until * 1000).toLocaleTimeString()}。`);
    await refreshStatus(); return;
  }
  if (run?.status === 'awaiting_author') { notice('本章已完成 AI 审稿，请在所选章节面板确认通过，或填写修改意见。'); return; }
  if (status.status === 'complete') { notice('全书已完成，可导出或按章节返修。'); return; }
  if (needsAuthorRevision(run)) { switchTab('activity'); switchReviewView('reviews'); notice('自动返修已到上限。请先查看审稿意见与候选稿差异，再在右侧填写要求并点击“生成返修稿”。'); return; }
  const options = budgetOptions(element('budget-tokens').value, element('max-steps').value);
  if (status.blocker?.code === 'BUDGET_LIMIT' && ((options.budget_tokens ?? run.budget_tokens) < status.blocker.minimum_budget_tokens || (options.max_steps ?? run.max_steps) < status.blocker.minimum_max_steps)) {
    element('budget-tokens').closest('details').open = true;
    notice(`第 ${run.chapter_number} 章尚未启动。下一步要求预留总上限至少 ${status.blocker.minimum_budget_tokens.toLocaleString()} Token、步骤总上限至少 ${status.blocker.minimum_max_steps}；请调整下方预算后继续。这是预留量，不是实际费用。`);
    element('budget-tokens').focus(); return;
  }
  if (!run || ['complete', 'cancelled', 'batch_complete'].includes(run.status)) {
    const batch = element('batch-size').value;
    if (batch !== 'all') options.chapter_limit = Math.min(Number(batch), state.book.settings.chapter_count);
    await api(`/api/books/${bookId}/control`, { action: 'start', options });
  } else if (run.status !== 'running') await api(`/api/books/${bookId}/control`, { action: 'resume', options });
  if (state.worker) { await api(`/api/books/${bookId}/worker`, {}); notice(presentation.notice); }
  else notice('自动执行器未配置，请打开 AI 配置。');
  await refreshStatus();
}
function automaticRevisionFeedback() {
  if (state.chapter?.chapter_number !== state.status?.run?.chapter_number) return '';
  const reviews = state.status?.run?.reviews || [];
  const latest = [...reviews].reverse().find(review => Array.isArray(review.issues) && review.issues.length);
  if (!latest) return '请根据当前已完成的 AI 审校意见，优先修复 blocker 与 major 问题；保留成立的情绪、人物声线与本章剧情结果。';
  const issues = latest.issues.filter(issue => ['blocker', 'major'].includes(issue.severity));
  const selected = issues.length ? issues : latest.issues;
  const details = selected.map((issue, index) => `${index + 1}. ${issue.dimension}：${issue.explanation}。建议：${issue.suggestion}`).join('\n');
  return `请按以下已完成的 AI 审校意见返修第 ${state.chapter?.chapter_number || latest.chapter_number || ''} 章。优先修复 blocker 与 major；保留已成立的情绪、人物声线与剧情结果，不要为迎合审稿而新增全知说明。\n${details}`;
}
async function revise(useFeedback) {
  if (!state.chapter) throw new Error('请先选择章节。');
  let feedback = useFeedback ? element('revision-feedback').value.trim() : '';
  if (useFeedback && !feedback && state.status?.run?.reviews?.some(review => review.issues?.length)) feedback = automaticRevisionFeedback();
  if (useFeedback && !feedback) throw new Error('当前没有可用于返修的审稿意见，请填写希望本章怎么修改。');
  const bookId = state.book.book_id;
  const draftKey = localDraftKey(); const sentDraft = storage('getItem', draftKey);
  await api(`/api/books/${bookId}/control`, { action: 'revise', options: { chapter_number: state.chapter.chapter_number, title: element('chapter-title').value, body: element('chapter-body').value, feedback, expected_revision: state.editRevision } });
  if (storage('getItem', draftKey) === sentDraft) storage('removeItem', draftKey);
  state.dirty = false; element('save-state').textContent = '候选稿已保存';
  notice(useFeedback ? '修改意见已交给 Agent，将保留原文并重新审查候选稿。' : '修改已保存为候选稿，审查通过后才替换原文。');
  if (state.worker) await api(`/api/books/${bookId}/worker`, {});
  await refreshStatus();
}
function updateMarketSelection() {
  const count = state.marketSources.size;
  element('market-selected-count').textContent = count ? `已选 ${count} 个来源` : '尚未选择来源';
}
function toggleMarketSource(sourceId, button) {
  if (state.marketSources.has(sourceId)) state.marketSources.delete(sourceId);
  else state.marketSources.add(sourceId);
  button.classList.toggle('selected', state.marketSources.has(sourceId));
  button.setAttribute('aria-pressed', String(state.marketSources.has(sourceId)));
  updateMarketSelection();
}
function addMarketSignal(label) {
  const field = element('market-idea-genres'); const tags = projectTags(field.value);
  if (!tags.includes(label)) tags.push(label);
  field.value = tags.join('、'); field.focus();
}
function applyMarketIdea(idea) {
  if (state.dirty && !confirm('当前编辑尚未保存，带入新选题会离开本作品。继续？')) return;
  const project = idea.project || {};
  state.selection++; state.book = null; state.chapter = null; state.status = null; state.dirty = false;
  storage('removeItem', 'hulk-book'); element('workspace').hidden = true; element('onboarding').hidden = false;
  element('breadcrumb-title').textContent = '从灵感开书'; element('save-state').textContent = '灵感已带入';
  element('title').value = idea.title || ''; element('request').value = idea.hook || '';
  element('project-platform').value = project.platform || 'general';
  element('project-genres').value = (project.genre_tags || []).join('、');
  element('project-reader').value = project.target_reader || '';
  element('project-boundaries').value = project.content_boundaries || '';
  const sources = (idea.source_observations || []).map(item => `${item.source_id} / ${item.label}（${item.count} 条）`).join('；');
  element('project-notes').value = `${project.custom_notes || ''}${sources ? `\n\n公开观察来源：${sources}` : ''}`.trim();
  closeDialog('market-dialog'); element('request').focus();
  notice('选题已带入开书向导。请按你的判断修改后再创建；它尚未成为故事设定。');
}
function renderMarketIdeas(result) {
  const box = element('market-idea-cards'); box.replaceChildren();
  element('market-idea-notice').textContent = result.notice || '以下是可编辑的作者灵感。';
  for (const idea of result.ideas || []) {
    const card = node('article', undefined, 'market-idea-card');
    card.append(node('span', idea.structure, 'eyebrow'), node('h3', idea.title), node('p', idea.hook, 'idea-hook'));
    const promise = node('p', undefined, 'idea-promise'); promise.append(node('strong', '读者承诺：'), document.createTextNode(idea.reader_promise)); card.append(promise);
    const provenance = node('small', `观察：${(idea.source_observations || []).map(item => `${item.source_id}/${item.label}×${item.count}`).join('；') || '作者偏好'}`); card.append(provenance);
    const use = node('button', '带入开书向导 →', 'primary small'); use.type = 'button'; use.addEventListener('click', () => applyMarketIdea(idea)); card.append(use); box.append(card);
  }
}
function applyOpeningProposal(proposal) {
  const project = proposal.project || {};
  element('title').value = proposal.title || '';
  element('request').value = proposal.logline || '';
  element('project-platform').value = project.platform || 'general';
  element('project-length').value = project.length_band || 'long';
  element('project-narrative').value = project.narrative_mode || 'limited_third';
  element('project-genres').value = (project.genre_tags || []).join('、');
  element('project-reader').value = project.target_reader || '';
  element('project-boundaries').value = project.content_boundaries || '';
  element('project-characters').value = (project.seed_characters || []).map(item => `${item.name}｜${item.role}｜${item.note}`).join('\n');
  element('project-rules').value = (project.world_rules || []).map(item => `${item.name}｜${item.rule}`).join('\n');
  element('project-notes').value = `${project.custom_notes || ''}\n\n开书方向：${proposal.structure || ''}。${proposal.story_core || ''}`.trim();
  element('opening-proposals').hidden = true; element('request').focus();
  notice('已带入开书资料。你可以继续修改，再决定是否创建并开始规划。');
}
function renderOpeningProposals(result) {
  const box = element('opening-proposals'); box.replaceChildren(); box.hidden = false;
  box.append(node('p', result.notice || '以下是可编辑的开书方向。', 'hint'));
  const cards = node('div', undefined, 'opening-proposal-cards');
  for (const proposal of result.proposals || []) {
    const card = node('article', undefined, 'opening-proposal-card');
    card.append(node('span', proposal.structure, 'eyebrow'), node('h3', proposal.title), node('p', proposal.story_core, 'proposal-core'));
    const promise = node('p'); promise.append(node('strong', '读者承诺：'), document.createTextNode(proposal.reader_promise)); card.append(promise);
    const button = node('button', '带入并编辑 →', 'small'); button.type = 'button'; button.addEventListener('click', () => applyOpeningProposal(proposal)); card.append(button); cards.append(card);
  }
  box.append(cards);
}
function renderIdeaJob(job) {
  const status = element('idea-job-status'); status.hidden = false;
  if (job.status === 'complete') {
    status.textContent = 'AI 灵感已生成。选择一个方向后仍可继续编辑，尚未创建作品。';
    renderOpeningProposals({ notice: '以下方向由当前配置的 Agent 生成，均为非正史建议。', proposals: job.ideas.map((idea, index) => ({ ...idea, structure: `AI 方向 ${index + 1}` })) });
  } else if (job.status === 'failed') status.textContent = `AI 灵感未生成：${job.error || '执行器没有返回可用结果。'}`;
  else status.textContent = 'Agent 正在生成 3 个开书方向…';
}
async function pollIdeaJob(jobId) {
  const job = await api(`/api/ideas/${jobId}`); renderIdeaJob(job);
  if (job.status === 'queued' || job.status === 'running') setTimeout(() => pollIdeaJob(jobId).catch(error => notice(error.message)), 900);
}
async function generateOpeningIdeas(button) {
  const request = button.dataset.idea || element('request').value.trim();
  if (!request) throw new Error('先写下一句话设想，再生成 AI 灵感。');
  element('request').value = request; element('opening-proposals').hidden = true;
  const job = await api('/api/ideas', { request, genre_hint: button.textContent.trim(), project: projectIntake() });
  await pollIdeaJob(job.job_id);
}
async function loadMarket() {
  const box = element('market-sources'); box.replaceChildren(node('p', '正在加载榜单目录…', 'hint'));
  const response = await api('/api/market/sources');
  const layout = node('div', undefined, 'market-browser');
  const navigation = node('nav', undefined, 'market-navigation'); navigation.setAttribute('aria-label', '选择榜单');
  const content = node('section', undefined, 'market-results'); layout.append(navigation, content); box.replaceChildren(layout);
  let generation = 0;
  const load = async (source, refreshNow = false) => {
    const ticket = ++generation;
    navigation.querySelectorAll('button').forEach(b => { b.classList.toggle('selected', b.dataset.source === source.source_id); b.setAttribute('aria-pressed', String(b.dataset.source === source.source_id)); });
    const header = node('header', undefined, 'market-results-header'); header.append(node('h3', source.label));
    const refresh = node('button', '更新榜单', 'small'); refresh.disabled = true; header.append(refresh);
    const status = node('p', '正在读取榜单并核对书名，首次加载可能需要数十秒…', 'hint');
    content.replaceChildren(header, status);
    refresh.addEventListener('click', () => load(source, true));
    try {
      const controller = new AbortController(); const timer = setTimeout(() => controller.abort(), 90000);
      let snapshot;
      try {
        const r = await fetch(`/api/market/${source.source_id}${refreshNow ? '/refresh' : ''}`, {signal:controller.signal, ...(refreshNow ? {method:'POST',headers:{'Content-Type':'application/json'},body:'{}'} : {})});
        snapshot = await r.json(); if (!r.ok) throw new Error(snapshot.detail?.message || '榜单请求失败');
      } finally { clearTimeout(timer); }
      if (ticket !== generation) return;
      status.textContent = `${snapshot.items.length} 本 · ${snapshot.status === 'fresh' ? '采集于' : snapshot.status === 'stale' ? '旧快照采集于' : '尝试于'} ${new Date(snapshot.collected_at * 1000).toLocaleString()}${snapshot.error ? ' · '+snapshot.error : ''}`;
      const link = node('a','官方榜单 ↗'); link.href=source.url; link.target='_blank'; link.rel='noreferrer'; header.append(link);
      if (!snapshot.items.length) { content.append(node('p', '当前没有获取到书籍。可打开官方榜单查看，或切换其他榜单。', 'market-empty')); return; }
      const tools = node('div',undefined,'market-result-tools');
      const search = node('input'); search.placeholder='搜索书名、作者或简介'; search.setAttribute('aria-label','搜索榜单书籍');
      const use = node('button',state.marketSources.has(source.source_id)?'已纳入选题':'纳入选题', 'small');
      use.addEventListener('click',()=>{toggleMarketSource(source.source_id,use);use.textContent=state.marketSources.has(source.source_id)?'已纳入选题':'纳入选题';});
      tools.append(search,use);content.append(tools);
      const signals=node('div',undefined,'market-signals');
      for(const signal of snapshot.signals.slice(0,10)){const chip=node('button',`${signal.label} · ${signal.count}`,'small');chip.title='仅统计本榜标题与简介中的出现次数';chip.addEventListener('click',()=>addMarketSignal(signal.label));signals.append(chip);}
      content.append(signals);
      const list=node('div',undefined,'market-book-list');content.append(list);
      const render=()=>{
        list.replaceChildren();const query=search.value.trim().toLowerCase();
        for(const book of snapshot.items.filter(b=>`${b.title} ${b.author} ${b.summary}`.toLowerCase().includes(query))){
          const row=node('article',undefined,'market-book');row.append(node('span',String(book.rank).padStart(2,'0'),'market-rank'));
          const info=node('div');const title=node('a',book.title);title.href=book.url || source.url;title.target='_blank';title.rel='noreferrer';info.append(title);
          info.append(node('p',`${book.author || '作者未提供'} · ${book.word_count ? Number(book.word_count).toLocaleString()+' 字' : '字数未提供'}${book.read_count ? ' · '+Number(book.read_count).toLocaleString()+' 在读' : ''}`,'hint'));
          const detail=node('details');detail.append(node('summary','查看简介'),node('p',book.summary || '来源未提供简介'));info.append(detail);row.append(info);list.append(row);
        }
        if(!list.children.length)list.append(node('p','没有符合搜索条件的作品。','hint'));
      };search.addEventListener('input',render);render();
    } catch(error) {if(ticket===generation)status.textContent=error.name==='AbortError'?'榜单读取超时，请稍后重试。':error.message;}
    finally {refresh.disabled=false;}
  };
  for(const source of response.sources){const button=node('button',source.label);button.dataset.source=source.source_id;button.addEventListener('click',()=>load(source));navigation.append(button);}
  if(response.sources.length)await load(response.sources[0]);
  updateMarketSelection();
}
function modelPickerOptions(providerId, selected = '') {
  const provider = state.aiConfig?.providers?.find(item => item.id === providerId);
  const picker = element('ai-model-picker'); picker.replaceChildren(node('option', '选择已知模型…'));
  picker.options[0].value = '';
  for (const model of provider?.models || []) {
    const option = node('option', model); option.value = model; picker.append(option);
  }
  picker.value = (provider?.models || []).includes(selected) ? selected : '';
}
function applyAIProvider(providerId, preserve = false) {
  const provider = state.aiConfig?.providers?.find(item => item.id === providerId); if (!provider) return;
  const isClaude = provider.mode === 'claude';
  element('ai-api-fields').hidden = isClaude;
  element('ai-claude-note').hidden = !isClaude;
  if (!preserve) { element('ai-base-url').value = provider.base_url || ''; element('ai-model').value = provider.model || ''; }
  const selected = element('ai-model').value || provider.model || '';
  modelPickerOptions(providerId, selected);
  if (isClaude && state.aiConfig?.local_claude?.available) {
    const local = state.aiConfig.local_claude.model;
    element('ai-claude-note').textContent = `已发现本机 Claude Code 模型：${local}。可选择或手填模型；登录和密钥仍由 Claude Code 本机配置管理。`;
    if (!element('ai-model').value) { element('ai-model').value = local; modelPickerOptions(providerId, local); }
  }
  if (isClaude && state.aiConfig?.host_options?.transport === 'native_deepseek') element('ai-claude-note').textContent += ' 当前使用 DeepSeek 原生 JSON 接口（复用本机连接），思考模式：' + (state.aiConfig.host_options.thinking === 'off' ? '关闭' : '默认') + '；连接参数已持久保存。';
  element('ai-api-key').value = '';
  element('ai-key-state').textContent = isClaude ? '本机登录' : state.aiConfig?.provider === providerId && state.aiConfig?.key_configured ? '已保存于系统钥匙串' : '尚未保存';
}
function updateAISettingsLabel() {
  const provider = state.aiConfig?.providers?.find(item => item.id === state.aiConfig?.provider);
  const label = !state.aiConfig?.configured ? (state.executor === 'claude' ? 'AI 配置 · Claude Code' : 'AI 配置') : state.aiConfig.mode === 'claude' ? `AI 配置 · Claude Code${state.aiConfig.model ? ` · ${state.aiConfig.model}` : ''}` : `AI 配置 · ${provider?.label || '已连接'}`;
  element('ai-settings-label').textContent = label;
}
async function loadAIConfig() {
  state.aiConfig = await api('/api/ai-config');
  const active = state.aiConfig.provider || (state.executor === 'claude' ? 'claude_code' : state.aiConfig.providers?.[0]?.id) || 'openai';
  element('ai-provider').value = active;
  if (state.aiConfig.configured) {
    element('ai-base-url').value = state.aiConfig.base_url || '';
    element('ai-model').value = state.aiConfig.model || '';
  }
  applyAIProvider(active, Boolean(state.aiConfig.configured)); updateAISettingsLabel();
}
function openAISettings() { loadAIConfig().then(() => openDialog('ai-config-dialog')).catch(error => notice(error.message)); }

function bind() {
  for (const view of ['ranks','ideas']) action(`market-tab-${view}`, () => { element('market-sources').hidden = view !== 'ranks'; element('market-ideas-panel').hidden = view !== 'ideas'; for (const name of ['ranks','ideas']) element(`market-tab-${name}`).classList.toggle('selected', name === view); });
  document.addEventListener('pointerdown', event => closeShelfMenus(event.target.closest?.('.shelf-menu')));
  document.addEventListener('keydown', event => { if (event.key === 'Escape') closeShelfMenus(null, true); });
  action('open-trash', showTrash); action('home-trash', showTrash); action('close-trash', () => closeDialog('trash-dialog'));
  element('dismiss-notice').addEventListener('click', () => notice(''));
  element('create-form').addEventListener('submit', async event => {
    event.preventDefault(); const button = event.submitter; button.disabled = true; notice('');
    try { const book = await api('/api/books', { request: element('request').value, title: element('title').value, chapter_count: Number(element('chapter-count').value), target_words: Number(element('target-words').value), mode: state.api ? 'api' : 'host', project: projectIntake() }); await shelf(); await selectBook(book.book_id); if (element('start-on-create').checked) await continueWriting(); }
    catch (error) { notice(error.message); } finally { button.disabled = false; }
  });
  action('new-book', async () => { if (state.dirty && !confirm('编辑尚未保存，继续开书会丢失本地修改。继续？')) return; state.selection++; state.book = null; state.chapter = null; state.status = null; state.dirty = false; element('app-shell').classList.remove('writing-focused'); storage('removeItem', 'hulk-book'); element('workspace').hidden = true; element('onboarding').hidden = false; element('breadcrumb-title').textContent = '新建作品'; element('save-state').textContent = '本地存储'; renderShelf(); });
  action('preview-proposals', async () => {
    const request = element('request').value.trim(); if (!request) throw new Error('先写下一句话设想，再生成开书方向。');
    const result = await api('/api/proposals', { request, project: projectIntake() }); renderOpeningProposals(result);
  });
  action('refresh', async () => { await shelf(); await refreshStatus(); });
  action('approve-chapter', async () => {
    const ch=state.chapter; const bookId=state.book.book_id;
    await api(`/api/books/${bookId}/control`, {action:'approve_chapter',options:{chapter_number:ch.chapter_number,version_id:ch.version_id}});
    await selectBook(bookId); notice(`已确认第 ${ch.chapter_number} 章。`);
  });
  action('review-chapter', async () => {
    const bookId=state.book.book_id; const number=state.chapter.chapter_number;
    await api(`/api/books/${bookId}/control`, {action:'review_chapter',options:{chapter_number:number,expected_revision:state.book.revision}});
    if(state.worker) await api(`/api/books/${bookId}/worker`,{});
    await refreshStatus(); notice(`已开始单独审核第 ${number} 章，最多3次，之后由你确认。`);
  });
  action('continue', continueWriting); action('save-revision', () => revise(false)); action('request-revision', () => revise(true));
  action('edit-project-material', async () => openProjectEditor());
  action('edit-story-bible', async () => openStoryBibleEditor());
  element('story-bible-form').addEventListener('submit', async event => {
    event.preventDefault(); if (!state.book) return;
    const button = element('save-story-bible'); if (state.busy.has(button.id)) return;
    state.busy.add(button.id); controls(); notice('');
    try {
      const payload = storyBiblePayload(); const bookId = state.book.book_id;
      const saved = await api(`/api/books/${bookId}/story-bible`, { ...payload, expected_revision: state.book.revision });
      closeDialog('story-bible-dialog'); state.task = null; renderContextInspector(null);
      await selectBook(bookId); const renames = Object.entries(saved.renamed_characters || {}).map(([oldName, newName]) => `${oldName} → ${newName}`).join('、'); notice(renames ? `已同步改名：${renames}。更新 ${saved.renamed_chapters} 章正文、记忆与续写资料，历史版本保留。请继续写作。` : '故事约定与章节骨架已保存。旧任务已停止，请基于新骨架继续写作。');
    } catch (error) { notice(error.message); }
    finally { state.busy.delete(button.id); controls(); }
  });
  element('project-editor-form').addEventListener('submit', async event => {
    event.preventDefault(); if (!state.book) return;
    const button = element('save-project-material'); if (state.busy.has(button.id)) return;
    state.busy.add(button.id); controls(); notice('');
    try {
      const saved = await api(`/api/books/${state.book.book_id}/project`, {
        metadata: projectPayload('edit-project-'), expected_revision: state.book.revision,
      });
      state.book.project = saved.project; state.book.revision = saved.revision;
      state.editRevision = saved.revision; renderBook(); element('project-dialog').close();
      notice('作者资料已保存。它会在后续创作约定中明确取舍，尚未自动成为正史。');
    } catch (error) { notice(error.message); }
    finally { state.busy.delete(button.id); controls(); }
  });
  for (const name of ['pause', 'cancel']) action(name, async () => { await api(`/api/books/${state.book.book_id}/control`, { action: name }); state.task = null; renderContextInspector(null); await refreshStatus(); });
  element('book-search').addEventListener('input', renderShelf);
  element('genre-library-search').addEventListener('input', renderGenreLibrary);
  element('project-genres').addEventListener('input', renderGenreLibrary);
  element('book-filter').addEventListener('change', () => shelf().catch(error => notice(error.message)));
  element('book-kind').addEventListener('change', async () => { if (!state.book) return; try { const book = await api(`/api/books/${state.book.book_id}/kind`, { kind: element('book-kind').value }); state.book = book; await shelf(); renderBook(); notice(book.book_kind === 'sample' ? '已标记为测试样例，可在书库筛选和清理。' : book.book_kind === 'archived' ? '作品已归档，不会显示在活跃书库，也不能继续写作。' : '作品已恢复到我的作品。'); } catch (error) { notice(error.message); renderBook(); } });
  action('delete-sample', async () => { if (!state.book || state.book.book_kind !== 'sample') return; if (!confirm(`删除测试样例《${state.book.title}》及其本地记录？此操作不可恢复。`)) return; await apiDelete(`/api/books/${state.book.book_id}`); state.selection++; state.book = null; state.chapter = null; state.status = null; state.reviews = []; element('workspace').hidden = true; element('onboarding').hidden = false; await shelf(); notice('测试样例已删除。'); });
  element('activity-filter').addEventListener('change', renderEvents);
  element('review-scope').addEventListener('change', () => refreshStatus().catch(error => notice(error.message)));
  element('revision-feedback').addEventListener('input', () => { state.dirty = true; saveLocalDraft(); controls(); });
  for (const field of ['chapter-title', 'chapter-body']) element(field).addEventListener('input', () => { state.dirty = true; saveLocalDraft(); element('editor-word-count').textContent = `${countWords(element('chapter-body').value).toLocaleString()} 字 · 本地编辑中`; controls(); });
  action('tts-play', toggleTTS);
  element('tts-auto-next').addEventListener('change', () => { if (!element('tts-auto-next').checked) clearTTSPreload(); else maybePreloadTTS(); });
  element('tts-progress').addEventListener('input', event => { const audio = element('chapter-audio'); if (audio.duration) audio.currentTime = audio.duration * Number(event.target.value) / 100; });
  element('tts-volume').addEventListener('input', event => { element('chapter-audio').volume = Number(event.target.value); });
  element('tts-voice').addEventListener('change', async () => { if (!state.chapter) return; const wasPlaying = state.tts.playing; resetTTSForChapter(state.chapter); if (wasPlaying) { try { await prepareAndPlayTTS(); } catch (error) { notice(error.message); } } });
  element('tts-rate').addEventListener('change', async () => { if (!state.chapter) return; const wasPlaying = state.tts.playing; resetTTSForChapter(state.chapter); if (wasPlaying) { try { await prepareAndPlayTTS(); } catch (error) { notice(error.message); } } });
  element('chapter-audio').addEventListener('loadedmetadata', () => { element('tts-time').textContent = `0:00 / ${formatAudioTime(element('chapter-audio').duration)}`; });
  element('chapter-audio').addEventListener('timeupdate', () => { maybePreloadTTS(); const audio = element('chapter-audio'); element('tts-progress').value = audio.duration ? String(audio.currentTime / audio.duration * 100) : '0'; element('tts-time').textContent = `${formatAudioTime(audio.currentTime)} / ${formatAudioTime(audio.duration)}`; });
  element('chapter-audio').addEventListener('pause', () => { if (!element('chapter-audio').ended) { state.tts.playing = false; element('tts-play').textContent = '▶'; } });
  element('chapter-audio').addEventListener('error', () => { state.tts.playing = false; element('tts-play').textContent = '▶'; ttsSetStatus('音频无法播放，请重试或检查 Edge TTS 网络连接。'); });
  element('chapter-audio').addEventListener('ended', async () => { state.tts.playing = false; element('tts-play').textContent = '▶'; if (!element('tts-auto-next').checked || state.dirty) { ttsSetStatus(state.dirty ? '本章播放完成；有未保存编辑，未自动切换。' : '本章播放完成'); return; } const next = nextPlayableChapter(state.book?.chapters, state.chapter?.chapter_number); if (!next) { ttsSetStatus('全书已播放完成'); return; } selectChapter(next); try { await prepareAndPlayTTS(); } catch (error) { notice(error.message); } });
  document.querySelectorAll('[data-tab]').forEach(button => button.addEventListener('click', () => switchTab(button.dataset.tab)));
  document.querySelectorAll('[data-idea]').forEach(button => button.addEventListener('click', () => generateOpeningIdeas(button).catch(error => notice(error.message))));
  document.querySelectorAll('[data-close]').forEach(button => button.addEventListener('click', () => closeDialog(button.dataset.close)));
  action('ai-settings', async () => openAISettings());
  element('ai-provider').addEventListener('change', () => applyAIProvider(element('ai-provider').value));
  element('ai-model-picker').addEventListener('change', () => { if (element('ai-model-picker').value) element('ai-model').value = element('ai-model-picker').value; });
  element('ai-config-form').addEventListener('submit', async event => {
    event.preventDefault(); const button = element('save-ai-config'); if (state.busy.has(button.id)) return;
    state.busy.add(button.id); button.disabled = true;
    try {
      const saved = await api('/api/ai-config', { provider: element('ai-provider').value, base_url: element('ai-base-url').value.trim(), model: element('ai-model').value.trim(), api_key: element('ai-api-key').value });
      state.aiConfig = saved; element('ai-api-key').value = ''; applyAIProvider(saved.provider, true);
      const capabilities = await api('/api/capabilities'); state.api = capabilities.api_configured; state.worker = capabilities.worker_configured; state.executor = capabilities.executor;
      updateAISettingsLabel();
      notice('AI 配置已保存到本机；新任务会使用该连接。'); closeDialog('ai-config-dialog');
    } catch (error) { notice(error.message); }
    finally { state.busy.delete(button.id); button.disabled = false; }
  });
  const openMarket = async () => { element('market-idea-platform').value = state.book?.project?.platform || element('project-platform').value; openDialog('market-dialog'); await loadMarket(); };
  action('open-market-sidebar', openMarket);
  element('market-idea-form').addEventListener('submit', async event => {
    event.preventDefault();
    if (!state.marketSources.size) { notice('请先在有可验证快照的来源中选择至少一项。'); return; }
    const button = element('generate-market-ideas'); if (state.busy.has(button.id)) return;
    state.busy.add(button.id); button.disabled = true; element('market-idea-notice').textContent = '正在组合作者偏好与可验证观察…';
    try {
      const result = await api('/api/market/ideas', { source_ids: [...state.marketSources], preferences: {
        platform: element('market-idea-platform').value, genre_tags: projectTags(element('market-idea-genres').value),
        request: element('market-idea-request').value.trim(), target_reader: element('market-idea-reader').value.trim(),
        content_boundaries: element('market-idea-boundaries').value.trim(),
      } });
      renderMarketIdeas(result);
    } catch (error) { element('market-idea-notice').textContent = error.message; }
    finally { state.busy.delete(button.id); button.disabled = false; }
  });
  action('open-import', async () => { element('project-menu').open = false; openDialog('import-dialog'); });
  document.addEventListener('click', event => { const menu = element('project-menu'); if (menu.open && !menu.contains(event.target)) menu.open = false; });
  document.addEventListener('keydown', event => { if (event.key === 'Escape') element('project-menu').open = false; });
  action('open-export', async () => { openDialog('export-dialog'); controls(); });
  action('open-current-chapter', async () => { const chapter = readingChapter(); if (chapter) selectChapter(chapter); switchTab('manuscript'); });
  action('focus-writing', async () => { state.focused = !state.focused; element('focus-writing').textContent = state.focused ? '退出专注' : '专注写作'; element('focus-writing').setAttribute('aria-pressed', String(state.focused)); switchTab('manuscript'); });
  document.querySelectorAll('[data-review-view]').forEach(button => button.addEventListener('click', () => switchReviewView(button.dataset.reviewView)));
  for (const selector of ['[data-tab]', '[data-review-view]']) {
    const buttons = [...document.querySelectorAll(selector)];
    buttons.forEach((button, index) => button.addEventListener('keydown', event => {
      if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
      event.preventDefault(); const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length;
      buttons[next].click(); buttons[next].focus();
    }));
  }
  switchReviewView('reviews');
  action('import-chapters', async () => {
    const files = Array.from(element('import-files').files); if (!files.length) throw new Error('请先选择章节文件。'); if (state.dirty) throw new Error('请先保存当前编辑内容。');
    const chapters = [];
    for (const file of files) {
      if (file.size > 2000000) throw new Error(`${file.name} 超过 2 MB。`);
      const text = await file.text();
      if (file.name.toLowerCase().endsWith('.json')) { const parsed = JSON.parse(text); if (!Array.isArray(parsed)) throw new Error('JSON 应为章节数组。'); chapters.push(...parsed); }
      else { const match = file.name.match(/^第(\d+)章[_\s]*(.*?)\.(md|txt)$/i); if (!match || !match[2]) throw new Error(`请将 ${file.name} 命名为“第001章_章名.md”。`); chapters.push({ chapter_number: Number(match[1]), title: match[2], body: text.replace(/^\uFEFF/, '') }); }
    }
    const result = await api(`/api/books/${state.book.book_id}/import`, { chapters, expected_revision: state.book.revision }); closeDialog('import-dialog'); element('import-files').value = ''; await selectBook(state.book.book_id); notice(`已导入 ${result.imported} 章待审原稿，可开始逆向建档与逐章审查。`);
  });
  for (const id of ['export', 'export-partial']) action(id, async () => { const manifest = await api(`/api/books/${state.book.book_id}/export`, { allow_partial: id === 'export-partial' }); const box = element('downloads'); box.replaceChildren(); box.hidden = false; box.append(node('p', manifest.complete ? '成稿已准备好，EPUB 可用于阅读器预览；发布前请完成作者审阅。' : '草稿已导出，文件保留未完稿标记，不提供 EPUB。')); const available = new Set(manifest.files.map(file => file.name)); for (const filename of ['manuscript.txt', 'manuscript.md', 'book.epub', 'manifest.json']) { if (!available.has(filename)) continue; const link = node('a', filename); link.href = `/api/exports/${manifest.export_id}/${filename}`; link.download = filename; box.append(link); } });
  action('memory-refresh', loadMemoryReview);
  action('memory-previous', async () => { memoryOffset = Math.max(0, memoryOffset - 20); await loadMemoryReview(); });
  action('memory-next', async () => { memoryOffset += 20; await loadMemoryReview(); });
  element('memory-review-filter').addEventListener('change', () => { memoryOffset = 0; loadMemoryReview().catch(error => notice(error.message)); });
  action('memory-maintain', async () => {
    const bookId = state.book.book_id;
    const result = await api(`/api/books/${bookId}/memory/maintenance`, { limit: 5, backfill: true });
    if (state.book?.book_id === bookId) { await loadMemoryReview(); notice(`已处理 ${result.processed} 项，尚有 ${result.backfill_remaining} 章未加入整理队列。`); }
  });
  element('memory-proposal-form').addEventListener('submit', async event => {
    event.preventDefault(); const button = event.submitter, bookId = state.book.book_id;
    const source = state.book.chapters.find(item => item.version_id === element('memory-source').value && item.status === 'committed');
    if (!source) { notice('请先选择一章有效定稿。'); return; }
    const candidate = { subject_alias: element('memory-subject').value.trim(), predicate: element('memory-predicate').value, value: element('memory-value').value,
      source_version: source.version_id, source_chapter: source.chapter_number, story_valid_from: Number(element('memory-time').value),
      visibility: element('memory-visibility').value, evidence: element('memory-evidence').value };
    button.disabled = true;
    try {
      try { await api(`/api/books/${bookId}/memory/entities`, { name: candidate.subject_alias }); }
      catch (error) { if (error.code !== 'AMBIGUOUS_ENTITY') throw error; }
      const result = await api(`/api/books/${bookId}/memory/proposals`, { candidates: [candidate], request_id: crypto.randomUUID() });
      if (state.book?.book_id === bookId) { memoryOffset = 0; element('memory-review-filter').value = result.quarantined.length ? 'quarantined' : 'pending'; await loadMemoryReview(); }
      notice(result.quarantined.length ? '来源或字段需要检查，已保留在隔离列表。' : '已加入待核实列表。');
    } catch (error) { notice(error.message); } finally { button.disabled = false; }
  });
  element('human-feedback-form').addEventListener('submit', async event => {
    event.preventDefault(); const form = event.currentTarget; const chapter = state.chapter; const bookId = state.book?.book_id;
    if (!chapter || chapter.status !== 'committed') { notice('请先选择已定稿章节。'); return; }
    const button = element('submit-human-feedback'); if (state.busy.has(button.id)) return;
    state.busy.add(button.id); controls();
    try {
      const value = element('human-rating').value;
      await api(`/api/books/${bookId}/feedback`, {
        chapter_number: chapter.chapter_number, version_id: chapter.version_id,
        reviewer_type: element('human-reviewer-type').value, reviewer_name: element('human-reviewer-name').value.trim(),
        verdict: element('human-verdict').value, rating: value ? Number(value) : null,
        would_continue: element('human-would-continue').checked, notes: element('human-notes').value.trim(),
      });
      const confirmation = `已保存第 ${chapter.chapter_number} 章当前定稿版本的真人反馈。`;
      if (state.book?.book_id === bookId && state.chapter?.version_id === chapter.version_id) form.reset();
      try {
        if (state.book?.book_id === bookId) await refreshStatus();
        notice(confirmation);
      } catch (_) { notice(`${confirmation}列表刷新失败，请刷新页面查看，无需重复提交。`); }
    } catch (error) { notice(error.message); }
    finally { state.busy.delete(button.id); controls(); }
  });
  element('query-form').addEventListener('submit', async event => {
    event.preventDefault(); const bookId = state.book.book_id; const button = event.submitter; button.disabled = true;
    try { const role = element('query-role').value; const result = await api(`/api/books/${bookId}/query`, { query: element('query').value, strategy: element('query-strategy').value, role, through_chapter: role === 'reader' ? Number(element('query-boundary').value) : null }); if (bookId !== state.book?.book_id) return; element('hits').replaceChildren(); element('hits').className = ''; if (result.index_complete === false) element('hits').append(node('p', '记忆索引尚未补齐；没有命中表示未知。可在下方运行本地整理。', 'hint')); if (!result.hits.length) element('hits').append(node('p', '未找到相关记忆；没有命中表示未知，可尝试其他关键词。', 'hint')); for (const hit of result.hits) { const row = node('div', undefined, 'hit'); row.append(node('small', `第 ${hit.chapter_number} 章 · ${hit.kind} · 有原文证据`), node('p', hit.text), node('blockquote', hit.source.quote)); element('hits').append(row); } }
    catch (error) { notice(error.message); } finally { button.disabled = false; }
  });
  action('get-task', async () => { if (state.worker) { await continueWriting(); return; } state.task = await api(`/api/books/${state.book.book_id}/next`, {}); renderContextInspector(); element('task').textContent = display(state.task); });
  action('submit-task', async () => { if (!state.task?.task_id) throw new Error('请先领取任务。'); await api('/api/tasks/submit', { task_id: state.task.task_id, lease_id: state.task.lease_id, worker_id: 'gui', result: JSON.parse(element('task-result').value) }); state.task = null; renderContextInspector(null); element('task-result').value = ''; element('task').textContent = '已提交。'; await refreshStatus(); });
  window.addEventListener('beforeunload', event => { if (state.dirty) { event.preventDefault(); event.returnValue = ''; } });
  renderGenreLibrary();
}
async function initialize() {
  if (location.protocol === 'file:') { element('app-shell').hidden = true; element('file-entry').hidden = false; return; }
  bind();
  try {
    const capabilities = await api('/api/capabilities'); state.api = capabilities.api_configured; state.worker = capabilities.worker_configured; state.executor = capabilities.executor;
    await loadTTSVoices();
    await loadAIConfig();
    await shelf(); const last = storage('getItem', 'hulk-book'); const preferred = state.books.find(book => book.book_id === last) || state.books.find(book => book.title.endsWith('（三章待审）'));
    if (preferred) await selectBook(preferred.book_id);
  } catch (error) { element('ai-settings-label').textContent = 'AI 配置 · 服务未连接'; notice(error.message); }
  controls(); setInterval(() => { if (!document.hidden) refreshStatus().catch(error => notice(error.message)); }, 5000);
}
initialize();
