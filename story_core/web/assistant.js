/* Book-scoped discussion. Only a separate, explicit action starts replanning. */
window.WenQuAssistant = {
  create({element, node, api, getContext, onApplied}) {
    let bookId = null, generation = 0, opened = false, loading = false, sending = false, applying = false;
    let data = {turns: [], protected_through: 0}, signature = '';
    const drafts = new Map();
    const applyButtons = new Map();
    const statusNames = {queued: '等待 AI 回复…', running: 'AI 正在思考…', failed: '这次回复未完成'};
    const error = message => { element('assistant-error').textContent = message || ''; };
    function contextChanged() {
      const context = getContext(), next = context.book?.book_id || null;
      element('open-assistant').disabled = !next;
      element('open-assistant').hidden = !next;
      if (bookId !== next) {
        if (bookId) drafts.set(bookId, element('assistant-message').value);
        bookId = next; generation++; loading = false; sending = false; applying = false;
        data = {turns: [], protected_through: 0}; signature = '';
        element('assistant-message').value = drafts.get(next) || '';
        error(''); element('assistant-history').replaceChildren(); applyButtons.clear();
      }
      element('assistant-book').textContent = context.book?.title || '请先选择作品';
      const selected = context.chapter?.chapter_number;
      element('assistant-chapter-label').textContent = selected ? `附带第 ${selected} 章已保存正文（可含候选稿）` : '在写作页选择章节后，可附带正文';
      element('assistant-include-chapter').disabled = !selected;
      if (!selected) element('assistant-include-chapter').checked = false;
      element('assistant-context').textContent = `参考故事约定、人物与规划摘要${data.protected_through ? `；重排保护前 ${data.protected_through} 章骨架` : ''}。${context.dirty ? '当前未保存的正文编辑不会发送。' : '规划与建议不当作已发生事实。'}`;
      renderControls();
    }
    function renderControls() {
      const context = getContext();
      const pending = data.turns.some(turn => ['queued', 'running'].includes(turn.status));
      const blocked = Boolean(context.status?.settings_planning) || context.status?.run?.status === 'running';
      element('assistant-send').disabled = !bookId || pending || sending || applying || blocked;
      element('assistant-send').textContent = sending || pending ? '等待回复…' : '发送给 AI';
      element('assistant-state').textContent = blocked ? '当前后台任务正在执行，结束或暂停后可继续讨论。' : pending ? '回复会保存在作品中，关闭窗口后仍继续。' : '对话只提供建议；确认调整方案后才会开始重排。';
      for (const [id, button] of applyButtons) button.disabled = !canApply(id);
    }
    function canApply(id) {
      const context = getContext(), turn = data.turns.find(item => item.id === id);
      return Boolean(turn?.can_apply && !applying && !sending && !context.status?.settings_planning && context.status?.run?.status !== 'running' && !data.turns.some(item => ['queued', 'running'].includes(item.status)));
    }
    function render() {
      contextChanged();
      const nextSignature = JSON.stringify([bookId, data, getContext().book?.revision, getContext().status?.settings_planning, applying]);
      if (signature === nextSignature) return;
      signature = nextSignature;
      const history = element('assistant-history');
      const atBottom = history.scrollHeight - history.scrollTop - (history.clientHeight || 0) < 80;
      const oldScroll = history.scrollTop;
      history.replaceChildren(); applyButtons.clear();
      if (!data.turns.length) {
        const welcome = node('div', undefined, 'assistant-welcome');
        welcome.append(node('h3', '一起把故事想清楚'), node('p', '可以讨论世界格局、卷纲节奏、人物动机，也可以附带当前章节，推敲具体段落。'));
        history.append(welcome);
      }
      for (const turn of data.turns) {
        const user = node('article', undefined, 'assistant-message assistant-user');
        user.append(node('strong', '你'), node('p', turn.message)); history.append(user);
        const response = node('article', undefined, 'assistant-message assistant-reply');
        response.append(node('strong', 'WenQu · 创作助手'));
        response.append(node('p', turn.reply || turn.error || statusNames[turn.status] || '等待回复'));
        if (turn.status === 'failed') {
          const retry = node('button', '编辑并重试'); retry.type = 'button';
          retry.addEventListener('click', () => { element('assistant-message').value = turn.message; element('assistant-message').focus(); });
          response.append(retry);
        }
        if (turn.proposal) {
          const proposal = node('section', undefined, 'assistant-proposal');
          proposal.append(node('strong', '待确认 · 规划调整方案'), node('p', turn.proposal.direction));
          if (turn.proposal.brief) {
            const details = node('details'); details.append(node('summary', '查看拟更新的故事约定'));
            const names = {title:'书名',premise:'故事核心',audience:'目标读者',pov:'叙述视角',style:'文字风格',ending:'终局方向'};
            for (const [key, label] of Object.entries(names)) details.append(node('strong', label), node('p', turn.proposal.brief[key] || ''));
            for (const character of turn.proposal.brief.characters || []) details.append(node('strong', character.name), node('p', ['desire','fear','boundary','voice'].map(key => character[key]).filter(Boolean).join('\n')));
            proposal.append(details);
          }
          const apply = node('button', '确认方案并重排后续章纲', 'primary'); apply.type = 'button';
          apply.disabled = !canApply(turn.id); applyButtons.set(turn.id, apply);
          apply.addEventListener('click', () => applyProposal(turn.id));
          proposal.append(node('p', turn.can_apply ? '保留已有正文与候选稿。后台分批重排，完成后应用；期间可取消。规划完成后不会自动写作。' : '该方案已应用、已被后续讨论替代，或作品版本已变化。可继续讨论后生成新方案。', 'hint'), apply);
          response.append(proposal);
        }
        history.append(response);
      }
      history.scrollTop = atBottom ? history.scrollHeight : oldScroll;
    }
    async function refresh() {
      contextChanged();
      if (!opened || !bookId || loading) return;
      const selected = bookId, request = generation; loading = true;
      try {
        const result = await api(`/api/books/${selected}/assistant`);
        if (bookId !== selected || generation !== request || getContext().book?.book_id !== selected) return;
        data = result; render();
      } catch (failure) { if (generation === request) error(failure.message); }
      finally { if (generation === request) loading = false; }
    }
    async function open() {
      contextChanged(); opened = true; element('assistant-panel').hidden = false;
      element('open-assistant').setAttribute('aria-expanded', 'true');
      render(); await refresh(); element('assistant-message').focus();
    }
    function close() {
      opened = false; element('assistant-panel').hidden = true;
      element('open-assistant').setAttribute('aria-expanded', 'false'); element('open-assistant').focus();
    }
    async function send() {
      contextChanged();
      const context = getContext(), message = element('assistant-message').value.trim();
      if (!bookId || !message || element('assistant-send').disabled) return;
      const selected = bookId, request = generation;
      sending = true; error(''); renderControls();
      const body = {message, expected_revision: context.book.revision};
      if (element('assistant-include-chapter').checked && context.chapter) body.chapter_number = context.chapter.chapter_number;
      try {
        const turn = await api(`/api/books/${selected}/assistant`, body);
        if (bookId !== selected || generation !== request) return;
        data.turns.push({...turn, message}); element('assistant-message').value = ''; drafts.delete(selected);
        render(); await refresh();
      } catch (failure) { if (generation === request) error(failure.message); }
      finally { if (generation === request) { sending = false; renderControls(); } }
    }
    async function applyProposal(turnId) {
      if (!bookId || !canApply(turnId)) return;
      if (getContext().dirty) { error('请先保存当前正文编辑，再确认调整方案。'); return; }
      const selected = bookId, request = generation; applying = true; error(''); render();
      try {
        await api(`/api/books/${selected}/assistant/${turnId}/apply`, {});
        if (generation !== request || bookId !== selected) return;
        await onApplied(); await refresh();
      } catch (failure) { if (generation === request) error(failure.message); }
      finally { if (generation === request) { applying = false; render(); } }
    }
    element('open-assistant').addEventListener('click', () => opened ? close() : open());
    element('close-assistant').addEventListener('click', close);
    element('assistant-form').addEventListener('submit', event => { event.preventDefault(); send(); });
    const prompts = {
      'assistant-world': '目前的世界观和卷纲太局限于单个宗门。请先评估问题，再提出从宗门走向地域、诸国与更大修仙世界的调整方案，说明各卷范围、主角成长、核心冲突和旧伏笔如何承接。保留已写正文，先讨论，不直接修改。',
      'assistant-character': '请分析主要人物的动机、关系变化与成长弧，指出单薄或重复的地方，给我可以讨论的调整建议。',
      'assistant-prose': '请结合我附带的章节，分析节奏、对话和人物行为是否自然。引用具体段落说明问题，给出改写示例，先不覆盖正文。'
    };
    for (const [id, prompt] of Object.entries(prompts)) element(id).addEventListener('click', () => { element('assistant-message').value = prompt; if (id === 'assistant-prose' && getContext().chapter) element('assistant-include-chapter').checked = true; element('assistant-message').focus(); });
    element('assistant-panel').addEventListener('keydown', event => { if (event.key === 'Escape') close(); });
    return {open, close, refresh, contextChanged, send};
  }
};
