from pathlib import Path
import json
import shutil
import subprocess
from urllib.parse import urljoin

import pytest
from fastapi.testclient import TestClient

from story_core.errors import StoryError
from story_core.http import create_app
from test_workflow import make, finish, result_for, to_stage


def test_assets_resolve_for_http_and_adjacent_file_entry(tmp_path):
    web = Path(__file__).parents[1] / 'story_core' / 'web'
    html = (web / 'index.html').read_text()
    assert 'href="./style.css?v=' in html
    assert 'src="./app.js?v=' in html
    assert (web / 'style.css').is_file()
    assert (web / 'app.js').is_file()
    with TestClient(create_app(tmp_path)) as client:
        for asset in ('style.css', 'app.js'):
            response = client.get('/' + asset)
            assert response.status_code == 200
            assert response.text == (web / asset).read_text()
    assert urljoin(web.as_uri() + '/index.html', './style.css') == (web / 'style.css').as_uri()


def test_workbench_shell_assets_do_not_serve_stale_ui(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        for path in ('/', '/style.css', '/app.js'):
            response = client.get(path)
            assert response.status_code == 200
            assert response.headers['cache-control'] == 'no-store'


def test_workbench_exposes_precise_review_memory_and_sample_controls():
    web = Path(__file__).parents[1] / 'story_core' / 'web'
    html = (web / 'index.html').read_text()
    for control in ('book-filter', 'book-kind', 'delete-sample', 'memory-facets', 'version-history', 'activity-filter'):
        assert f'id="{control}"' in html


def test_workbench_exposes_project_archiving_without_user_project_deletion():
    web = Path(__file__).parents[1] / 'story_core' / 'web'
    html = (web / 'index.html').read_text()
    script = (web / 'app.js').read_text()
    assert '<option value="archived">已归档</option>' in html
    assert "book_kind_changed" in script
    assert "'archived'" in script


def test_workbench_is_project_first_and_collects_optional_author_material():
    web = Path(__file__).parents[1] / 'story_core' / 'web'
    html = (web / 'index.html').read_text()
    script = (web / 'app.js').read_text()
    for control in ('overview', 'overview-metrics', 'project-platform', 'project-genres', 'project-characters', 'project-rules'):
        assert f'id="{control}"' in html
    assert "hulk-view-${bookId}" in script
    assert 'projectIntake' in script
    assert '作者资料' in html
    assert 'id="market-dialog"' in html
    assert 'loadMarket' in script


def test_workbench_uses_dark_embeddable_design_primitives():
    root = Path(__file__).parents[1] / 'story_core' / 'web'
    html = (root / 'index.html').read_text(encoding='utf-8')
    css = (root / 'style.css').read_text(encoding='utf-8')
    assert 'data-theme="dark"' in html
    assert 'class="agent-rail"' in html
    assert '.ui-select' in css
    assert 'appearance:none' in css.replace(' ', '')
    assert 'src="https://' not in html
    assert 'href="https://' not in html


def test_workbench_can_edit_author_material_without_creating_canon():
    root = Path(__file__).parents[1] / 'story_core' / 'web'
    html = (root / 'index.html').read_text(encoding='utf-8')
    script = (root / 'app.js').read_text(encoding='utf-8')
    assert 'id="project-dialog"' in html
    assert 'id="edit-project-material"' in html
    assert '/project' in script
    assert "metadata: projectPayload('edit-project-')" in script
    assert 'expected_revision' in script


def test_workbench_exposes_market_idea_studio_without_static_rank_seed_data():
    root = Path(__file__).parents[1] / 'story_core' / 'web'
    html = (root / 'index.html').read_text(encoding='utf-8')
    script = (root / 'app.js').read_text(encoding='utf-8')
    assert 'id="market-idea-form"' in html
    assert 'id="market-idea-cards"' in html
    assert 'id="market-idea-genres"' in html
    assert '/api/market/ideas' in script
    assert 'applyMarketIdea' in script


def test_workbench_can_preview_opening_proposals_before_creating_a_project():
    root = Path(__file__).parents[1] / 'story_core' / 'web'
    html = (root / 'index.html').read_text(encoding='utf-8')
    script = (root / 'app.js').read_text(encoding='utf-8')
    assert 'id="preview-proposals"' in html
    assert 'id="opening-proposals"' in html
    assert '/api/proposals' in script
    assert 'applyOpeningProposal' in script


def test_workbench_has_searchable_multi_genre_library_for_opening_a_project():
    root = Path(__file__).parents[1] / 'story_core' / 'web'
    html = (root / 'index.html').read_text(encoding='utf-8')
    script = (root / 'app.js').read_text(encoding='utf-8')
    assert 'id="genre-library-search"' in html
    assert 'id="genre-library"' in html
    assert 'GENRE_LIBRARY' in script
    assert 'renderGenreLibrary' in script


def test_workbench_exposes_context_inspector_controls():
    web = Path(__file__).parents[1] / 'story_core' / 'web'
    html = (web / 'index.html').read_text()
    script = (web / 'app.js').read_text()
    assert 'id="context-inspector"' in html
    assert 'id="context-sources"' in html
    assert '本章 POV' in script
    assert '其他角色认知' in script


def test_workbench_marks_author_material_as_non_canon_in_task_inspector():
    script = (Path(__file__).parents[1] / 'story_core' / 'web' / 'app.js').read_text()
    assert '作者资料（尚非正史）' in script


def test_workbench_clears_versions_and_explains_paused_candidate_recovery():
    script = (Path(__file__).parents[1] / 'story_core' / 'web' / 'app.js').read_text()
    assert 'state.versions = []; renderVersions();' in script
    assert '点击“${workflowPresentation(status).label}”会从此阶段继续' in script


def test_workbench_renders_visible_before_after_version_diff():
    script = (Path(__file__).parents[1] / 'story_core' / 'web' / 'app.js').read_text()
    assert "node('article', undefined, 'diff-before')" in script
    assert "node('article', undefined, 'diff-after')" in script
    assert 'no_text_change' in script
    assert '重新审查记录已保留在“审稿意见”中' in script


def test_workbench_explains_author_revision_gate_and_renders_candidate_diffs():
    root = Path(__file__).parents[1] / 'story_core' / 'web'
    html = (root / 'index.html').read_text(encoding='utf-8')
    script = (root / 'app.js').read_text(encoding='utf-8')
    assert 'id="candidate-history"' in html
    assert '候选稿修改轨迹' in html
    assert 'renderCandidateHistory' in script
    assert '/candidates' in script
    assert '查看返修意见' in script
    assert 'needsAuthorRevision' in script


def test_workbench_keeps_review_counts_in_the_current_run_and_locks_duplicate_revision_requests():
    root = Path(__file__).parents[1] / 'story_core' / 'web'
    html = (root / 'index.html').read_text(encoding='utf-8')
    script = (root / 'app.js').read_text(encoding='utf-8')
    assert 'id="review-scope"' in html
    assert 'candidate_revision' in script
    assert "'request-revision': !state.chapter || !revisionUnlocked" in script
    assert 'include_history=' in script


def test_workbench_exposes_specific_task_activity_and_outcome_in_the_agent_rail():
    root = Path(__file__).parents[1] / 'story_core' / 'web'
    html = (root / 'index.html').read_text(encoding='utf-8')
    script = (root / 'app.js').read_text(encoding='utf-8')
    css = (root / 'style.css').read_text(encoding='utf-8')
    assert 'id="execution-summary"' in html
    assert 'id="execution-outcome"' in html
    assert 'revision-action' in html
    assert 'renderExecutionSummary' in script
    assert 'execution.outcome' in script
    assert '?? status.execution?.elapsed_seconds' in script
    assert '候选稿返修中…' in script
    assert '.revision-action' in css


def test_workbench_can_edit_story_bible_with_structured_author_fields():
    root = Path(__file__).parents[1] / 'story_core' / 'web'
    html = (root / 'index.html').read_text()
    script = (root / 'app.js').read_text()
    for control in ('edit-story-bible', 'story-bible-dialog', 'bible-characters', 'bible-chapters', 'bible-promises'):
        assert f'id="{control}"' in html
    assert '/story-bible' in script
    assert 'storyBiblePayload' in script


def test_workbench_story_bible_dialog_has_a_reliable_fallback_open_path():
    script = (Path(__file__).parents[1] / 'story_core' / 'web' / 'app.js').read_text()
    assert 'function openDialog' in script
    assert "openDialog('story-bible-dialog')" in script
    assert "dialog.setAttribute('open', '')" in script
    assert "notice('故事约定与章节骨架编辑器已打开。')" in script


def test_workbench_uses_async_ai_idea_jobs_for_genre_shortcuts():
    root = Path(__file__).parents[1] / 'story_core' / 'web'
    html = (root / 'index.html').read_text()
    script = (root / 'app.js').read_text()
    assert 'id="idea-job-status"' in html
    assert "api('/api/ideas'" in script
    assert '/api/ideas/${jobId}' in script
    assert 'Agent 正在生成 3 个开书方向' in script


def test_workbench_renders_reader_profile_labels():
    script = (Path(__file__).parents[1] / 'story_core' / 'web' / 'app.js').read_text()
    assert 'reader_profile' in script
    assert '目标题材读者' in script


def test_workbench_renders_promise_obligations_in_task_inspector():
    script = (Path(__file__).parents[1] / 'story_core' / 'web' / 'app.js').read_text()
    assert 'promise_obligations' in script


def test_workbench_has_version_bound_human_feedback_intake():
    from pathlib import Path

    root = Path(__file__).parents[1] / 'story_core' / 'web'
    html = (root / 'index.html').read_text(encoding='utf-8')
    script = (root / 'app.js').read_text(encoding='utf-8')
    assert 'id="human-feedback-form"' in html
    assert '保存并绑定当前章节版本' in html
    assert '/feedback`' in script
    assert 'source === \'human\'' in script
    assert '到期承诺' in script


def test_workbench_drives_candidate_review_and_human_feedback_by_stage():
    root = Path(__file__).parents[1] / 'story_core' / 'web'
    html = (root / 'index.html').read_text(encoding='utf-8')
    script = (root / 'app.js').read_text(encoding='utf-8')
    css = (root / 'style.css').read_text(encoding='utf-8')
    for control in ('notice-message', 'dismiss-notice', 'human-feedback-locked', 'review-stage-state'):
        assert f'id="{control}"' in html
    assert 'candidateChapter' in script
    assert '草稿待审' in script
    assert 'AI 自动审校中' in script
    assert '真人反馈将在本章定稿后解锁' in script
    assert 'no_text_change' in script
    assert "element('continue').textContent = continueLabel" in script
    assert 'white-space:nowrap' in css
    assert 'story-bible-button' in html


def test_workbench_keeps_primary_progress_action_on_one_line_and_explains_live_work():
    root = Path(__file__).parents[1] / 'story_core' / 'web'
    script = (root / 'app.js').read_text(encoding='utf-8')
    css = (root / 'style.css').read_text(encoding='utf-8')
    assert '已运行 ${formatElapsed' in script
    assert '处理中，请等待或暂停' in script
    assert '#continue{white-space:nowrap!important' in css
    assert 'overflow:hidden' in css
    assert '.reading-card button{white-space:nowrap' in css


def test_workbench_has_one_story_bible_editing_entry_in_planning():
    script = (Path(__file__).parents[1] / 'story_core' / 'web' / 'app.js').read_text(encoding='utf-8')
    assert "'overview-edit-story-bible'" not in script
    assert "action('edit-story-bible', async () => openStoryBibleEditor())" in script


def test_workbench_renders_arc_checkpoint_scope():
    script = (Path(__file__).parents[1] / 'story_core' / 'web' / 'app.js').read_text()
    assert 'arc_chapters' in script
    assert '故事弧审校' in script


def test_review_history_survives_new_run_and_is_book_scoped(tmp_path):
    service, book = make(tmp_path)
    finish(service, book)
    history = service.review_history(book, chapter_number=1)
    assert [entry['stage'] for entry in history] == ['continuity', 'reader', 'reader']
    assert all(entry['chapter_number'] == 1 for entry in history)
    assert all(entry['task_id'] and entry['base_revision'] >= 0 for entry in history)
    other = service.open_book('别的作品')
    assert service.review_history(other['book_id']) == []
    service.control(book, 'revise', chapter_number=1, body=result_for({'stage': 'draft'})['body'])
    assert service.review_history(book, chapter_number=1) == []
    assert service.review_history(book, chapter_number=1, include_history=True) == history


def test_status_exposes_current_execution_stage_and_terminal_outcome(tmp_path):
    service, book = make(tmp_path)
    task = service.next_task(book, worker_id='diagnostic-host')
    running = service.status(book)['execution']
    assert {key: running[key] for key in ('task_id', 'stage', 'chapter_number', 'outcome', 'next_stage', 'failure')} == {
        'task_id': task['task_id'], 'stage': 'brief', 'chapter_number': 1,
        'outcome': 'running', 'next_stage': None, 'failure': None,
    }
    assert running['started_at'] > 0 and running['elapsed_seconds'] >= 0

    service.fail_task(task['task_id'], task['lease_id'], StoryError('HOST_TIMEOUT', '宿主执行超时。', {'timeout_seconds': 1200}))
    failed = service.status(book)['execution']
    assert failed['outcome'] == 'failed'
    assert failed['failure'] == {'code': 'HOST_TIMEOUT', 'message': '宿主执行超时。'}
    assert failed['failure_details'] == {'timeout_seconds': 1200}
    assert failed['elapsed_seconds'] >= 0


def test_revision_feedback_is_persisted_and_stale_edits_rejected(tmp_path):
    service, book = make(tmp_path)
    finish(service, book)
    revision = service.get_book(book)['revision']
    body = result_for({'stage': 'draft'})['body']
    service.control(book, 'revise', chapter_number=1, body=body,
                    feedback='让妹妹通过行动表达担心，删去旁白解释', expected_revision=revision)
    run = service.status(book)['run']
    assert run['stage'] == 'revise'
    task = service.next_task(book)
    assert '通过行动表达担心' in str(task['input']['reviews'])
    with pytest.raises(StoryError) as caught:
        service.control(book, 'revise', chapter_number=1, body=body, expected_revision=revision)
    assert caught.value.code == 'STALE_REVISION'
    assert service.status(book)['run']['run_id'] == run['run_id']


def test_duplicate_author_revision_does_not_cancel_the_active_candidate_run(tmp_path):
    service, book = make(tmp_path)
    finish(service, book)
    body = service.get_book(book)['chapters'][0]['body']
    first = service.control(book, 'revise', chapter_number=1, body=body, feedback='保留主角的犹豫，补足代价。')
    with pytest.raises(StoryError) as caught:
        service.control(book, 'revise', chapter_number=1, body=body, feedback='不要重复触发新的返修。')
    assert caught.value.code == 'RUN_ACTIVE'
    assert service.status(book)['run']['run_id'] == first['run_id']
    assert service.status(book)['run']['status'] == 'running'


def test_invalid_feedback_rolls_back_and_history_http(tmp_path):
    service, book = make(tmp_path)
    finish(service, book)
    with pytest.raises(StoryError):
        service.control(book, 'revise', chapter_number=1, body=result_for({'stage': 'draft'})['body'], feedback={'bad': True})
    assert service.get_book(book)['status'] == 'complete'
    with TestClient(create_app(tmp_path)) as client:
        response = client.get(f'/api/books/{book}/reviews?chapter_number=1')
        assert response.status_code == 200
        assert len(response.json()['reviews']) == 3


def test_file_entry_never_calls_api_or_starts_polling():
    executable = shutil.which('node')
    if not executable:
        pytest.skip('Node.js is required for the file-entry regression test')
    script = Path(__file__).parents[1] / 'story_core' / 'web' / 'app.js'
    probe = """
const fs = require('node:fs');
const vm = require('node:vm');
const nodes = {'app-shell': {hidden:false}, 'file-entry': {hidden:true}};
const context = {document:{getElementById:id=>nodes[id]}, location:{protocol:'file:'},
  fetch:()=>{throw Error('File mode must not fetch');},
  setInterval:()=>{throw Error('File mode must not poll');}};
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'),context);
if (!nodes['app-shell'].hidden || nodes['file-entry'].hidden) process.exit(1);
"""
    result = subprocess.run([executable, '-e', probe, str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_feedback_revision_reenters_both_reviews_before_commit(tmp_path):
    service, book = make(tmp_path, chapters=1)
    finish(service, book)
    original = service.get_book(book)['chapters'][0]['version_id']
    service.control(book, 'revise', chapter_number=1,
                    body=result_for({'stage': 'draft'})['body'], feedback='减少重复旁白')
    stages_seen = []
    for _ in range(10):
        task = service.next_task(book)
        if not task.get('task_id'):
            break
        stages_seen.append(task['stage'])
        if task['stage'] != 'ending':
            assert service.get_book(book)['chapters'][0]['version_id'] == original
        service.submit_task(task['task_id'], task['lease_id'], result_for(task))
    assert stages_seen == ['revise', 'extract', 'continuity', 'reader', 'reader', 'ending']
    assert service.get_book(book)['chapters'][0]['version_id'] != original
    assert len(service.review_history(book)) == 4
    assert len(service.review_history(book, include_history=True)) == 8


def test_revision_can_recover_an_uncommitted_candidate_at_attempt_limit(tmp_path):
    service, book = make(tmp_path, chapters=1, max_revisions=0)
    task = None
    for _ in range(10):
        task = service.next_task(book)
        assert task.get('task_id')
        service.submit_task(task['task_id'], task['lease_id'], result_for(task, block=task['stage'] == 'continuity'))
        if service.status(book)['run']['status'] == 'needs_attention':
            break
    assert service.status(book)['run']['candidate']['body']
    body = result_for({'stage': 'draft'})['body'].replace('冷掉', '温热')
    recovered = service.control(book, 'revise', chapter_number=1, body=body,
                                feedback='根据独立审稿压缩核账场景，增强主角主动布局。')
    assert recovered['status'] == 'running'
    assert recovered['stage'] == 'revise'
    assert recovered['candidate']['body'] == body


def test_candidate_history_exposes_precommit_ai_revisions_and_diffs(tmp_path):
    service, book = make(tmp_path, chapters=1, max_revisions=0)
    continuity = to_stage(service, book, 'continuity')
    service.submit_task(continuity['task_id'], continuity['lease_id'], result_for(continuity, block=True))
    decision = service.next_task(book)
    service.submit_task(decision['task_id'], decision['lease_id'], result_for(decision))
    assert service.status(book)['run']['status'] == 'needs_attention'

    service.control(book, 'revise', chapter_number=1,
                    body=result_for({'stage': 'draft'})['body'].replace('冷掉的面', '温热的面'),
                    feedback='把妹妹的担心落到动作上，再压缩解释。')
    revise = service.next_task(book)
    revised = result_for(revise)
    revised['body'] = revised['body'].replace('冷掉的面', '温热的面').replace('收音机忽然响了。', '收音机忽然响了，妹妹先按住他的手。')
    service.submit_task(revise['task_id'], revise['lease_id'], revised)

    history = service.candidate_versions(book, chapter_number=1)
    assert [item['source'] for item in history] == ['AI 初稿', 'AI 返修']
    assert history[0]['change']['summary'] == '首次候选稿'
    assert '候选稿修订：' in history[-1]['change']['summary']
    assert any(item['type'] == '替换' and '冷掉的面' in item['before'] and '温热的面' in item['after']
               for item in history[-1]['change']['details'])
    assert service.get_book(book)['chapters'] == []

    with TestClient(create_app(tmp_path)) as client:
        response = client.get(f'/api/books/{book}/candidates?chapter_number=1')
        assert response.status_code == 200
        assert [item['source'] for item in response.json()['candidates']] == ['AI 初稿', 'AI 返修']


def test_review_history_names_reviewer_attempt_and_chapter_versions(tmp_path):
    service, book = make(tmp_path, chapters=1)
    finish(service, book)
    reviews = service.review_history(book, chapter_number=1)
    assert [(item['reviewer'], item['attempt']) for item in reviews] == [('连续性审校', 1), ('目标题材读者', 1), ('逻辑敏感读者', 1), ('完结审校', 1)]
    versions = service.chapter_versions(book, 1)
    assert len(versions) == 1
    assert versions[0]['version_number'] == 1
    assert versions[0]['change']['summary'] == '首次定稿'


def test_version_history_does_not_claim_a_text_change_when_only_reapproved(tmp_path):
    service, book = make(tmp_path, chapters=1)
    finish(service, book)
    original = service.get_book(book)['chapters'][0]
    service.control(book, 'revise', chapter_number=1, body=original['body'])
    finish(service, book)
    assert service.chapter_versions(book, 1)[-1]['change']['summary'] == '重新审查定稿，正文无文字变化'


def test_version_history_exposes_paragraph_level_change_preview(tmp_path):
    service, book = make(tmp_path, chapters=1)
    finish(service, book)
    original = service.get_book(book)['chapters'][0]
    edited = original['body'].replace('冷掉的面', '温热的面')
    service.control(book, 'revise', chapter_number=1, body=edited)
    finish(service, book)
    change = service.chapter_versions(book, 1)[-1]['change']
    assert change['summary'].startswith('正文修订：')
    assert any(item['type'] == '替换' and '冷掉的面' in item['before'] and '温热的面' in item['after'] for item in change['details'])


def test_review_attempt_resets_for_a_new_candidate_run(tmp_path):
    service, book = make(tmp_path, chapters=1)
    finish(service, book)
    original = service.get_book(book)['chapters'][0]
    for _ in range(2):
        service.control(book, 'revise', chapter_number=1, body=original['body'])
        finish(service, book)
    newest = service.review_history(book, chapter_number=1, limit=1)
    assert newest[0]['stage'] == 'ending'
    assert newest[0]['attempt'] == 1


def test_author_revision_cannot_cancel_an_active_automatic_review(tmp_path):
    service, book = make(tmp_path, chapters=1)
    review = to_stage(service, book, 'continuity')
    before = service.status(book)['run']

    with pytest.raises(StoryError) as caught:
        service.control(book, 'revise', chapter_number=1,
                        body=before['candidate']['body'], feedback='压缩说明，保留主角行动。')

    assert caught.value.code == 'RUN_ACTIVE'
    after = service.status(book)['run']
    assert after['run_id'] == before['run_id']
    assert after['status'] == 'running'
    assert after['stage'] == 'continuity'
    assert service.task_active(review['task_id'], review['lease_id']) is True


def test_workbench_locks_revision_action_until_author_revision_is_required():
    script = (Path(__file__).parents[1] / 'story_core' / 'web' / 'app.js').read_text(encoding='utf-8')
    assert "const revisionUnlocked = !active || needsAuthorRevision(run);" in script
    assert "'request-revision': !state.chapter || !revisionUnlocked" in script


def test_workbench_offers_explicit_ai_revision_from_completed_review():
    root = Path(__file__).parents[1] / 'story_core' / 'web'
    html = (root / 'index.html').read_text(encoding='utf-8')
    script = (root / 'app.js').read_text(encoding='utf-8')
    assert 'id="request-revision"' in html and 'id="auto-revision"' not in html
    assert '生成返修稿' in html
    assert 'function automaticRevisionFeedback()' in script
    assert "feedback = automaticRevisionFeedback()" in script


def test_workbench_exposes_token_costs_and_keychain_backed_ai_configuration():
    root = Path(__file__).parents[1] / 'story_core' / 'web'
    html = (root / 'index.html').read_text(encoding='utf-8')
    script = (root / 'app.js').read_text(encoding='utf-8')
    css = (root / 'style.css').read_text(encoding='utf-8')
    assert 'id="ai-config-dialog"' in html
    assert 'id="ai-provider"' in html
    assert 'id="ai-model-picker"' in html
    assert 'id="connection-dialog"' not in html
    assert 'ChatGPT / OpenAI API' in html and 'Grok / xAI' in html
    assert 'function formatTokens' in script
    assert 'chapterTokenUsage' in script
    assert "api('/api/ai-config'" in script
    assert 'function modelPickerOptions' in script
    assert 'function updateAISettingsLabel' in script
    assert 'border:0!important' in css
