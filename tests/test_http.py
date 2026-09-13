import json
from fastapi.testclient import TestClient
import pytest
from test_workflow import result_for


def test_gui_api_host_flow_and_download(tmp_path):
    from story_core.http import create_app
    with TestClient(create_app(tmp_path)) as client:
        assert client.get('/').status_code==200
        book=client.post('/api/books',json={'request':'给我开本书','chapter_count':1,'target_words':80}).json()
        book_id=book['book_id']
        assert client.post(f'/api/books/{book_id}/control',json={'action':'start'}).status_code==200
        for _ in range(20):
            task=client.post(f'/api/books/{book_id}/next',json={}).json()
            if not task.get('task_id'):
                if task.get('status') == 'awaiting_author':
                    ch=client.get(f'/api/books/{book_id}').json()['chapters'][0]
                    assert client.post(f'/api/books/{book_id}/control',json={'action':'approve_chapter','options':{'chapter_number':1,'version_id':ch['version_id']}}).status_code==200
                    continue
                break
            response=client.post('/api/tasks/submit',json={'task_id':task['task_id'],'lease_id':task['lease_id'],'result':result_for(task),'worker_id':'gui'})
            assert response.status_code==200,response.text
        manifest=client.post(f'/api/books/{book_id}/export',json={}).json()
        download=client.get(f"/api/exports/{manifest['export_id']}/manuscript.txt")
        assert download.status_code==200
        assert '沈知秋' in download.text
        epub=client.get(f"/api/exports/{manifest['export_id']}/book.epub")
        assert epub.status_code==200
        assert epub.content[:2] == b'PK'
        assert client.get(f"/api/exports/{manifest['export_id']}/project.sqlite").status_code==404


def test_cross_origin_requests_rejected(tmp_path):
    from story_core.http import create_app
    with TestClient(create_app(tmp_path)) as client:
        response=client.post('/api/books',json={'request':'开书'},headers={'origin':'https://unrelated.example'})
        assert response.status_code==403


def test_workbench_can_opt_in_to_a_known_dsh_webview_origin(tmp_path):
    from story_core.http import create_app

    with TestClient(create_app(tmp_path, frame_ancestors='http://127.0.0.1:3080')) as client:
        response = client.get('/')
        assert "frame-ancestors 'self' http://127.0.0.1:3080" in response.headers['content-security-policy']
    with pytest.raises(ValueError):
        create_app(tmp_path, frame_ancestors='https://untrusted.example/path')


def test_http_import_and_candidate_visible(tmp_path):
    from story_core.http import create_app
    with TestClient(create_app(tmp_path)) as client:
        book = client.post('/api/books', json={'request': '开书', 'chapter_count': 5}).json()
        chapter = {'chapter_number': 1, 'title': '第一章', 'body': '待审原稿。'}
        response = client.post(f"/api/books/{book['book_id']}/import", json={'chapters': [chapter], 'expected_revision': 0})
        assert response.status_code == 200, response.text
        assert response.json()['reviewed'] is False
        shown = client.get(f"/api/books/{book['book_id']}").json()
        assert shown['chapters'][0]['status'] == 'imported'
        assert shown['chapters'][0]['body'] == '待审原稿。'


def test_unconfigured_worker_returns_clear_error(tmp_path,monkeypatch):
    from story_core.http import create_app
    monkeypatch.delenv('HULK_API_KEY',raising=False)
    with TestClient(create_app(tmp_path)) as client:
        book=client.post('/api/books',json={'request':'开书'}).json()
        response=client.post(f"/api/books/{book['book_id']}/worker")
        assert response.status_code==400
        assert response.json()['detail']['code']=='PROVIDER_NOT_CONFIGURED'


def test_http_exposes_review_attempts_memory_facets_and_sample_lifecycle(tmp_path):
    from story_core.http import create_app
    with TestClient(create_app(tmp_path)) as client:
        book = client.post('/api/books', json={'request': '开书', 'chapter_count': 1, 'target_words': 80}).json()
        book_id = book['book_id']
        assert client.post(f'/api/books/{book_id}/kind', json={'kind': 'sample'}).json()['book_kind'] == 'sample'
        listed = client.get('/api/books?kind=sample').json()
        assert [item['book_id'] for item in listed] == [book_id]
        assert client.delete(f'/api/books/{book_id}').status_code == 200
        assert client.get('/api/books?kind=sample').json() == []


def test_http_archives_inactive_projects_and_hides_them_from_active_library(tmp_path):
    from story_core.http import create_app

    with TestClient(create_app(tmp_path)) as client:
        book = client.post('/api/books', json={'request': '开书'}).json()
        book_id = book['book_id']
        client.post(f'/api/books/{book_id}/control', json={'action': 'start'})
        blocked = client.post(f'/api/books/{book_id}/kind', json={'kind': 'archived'})
        assert blocked.status_code == 409
        assert blocked.json()['detail']['code'] == 'RUN_ACTIVE'

        assert client.post(f'/api/books/{book_id}/control', json={'action': 'cancel'}).status_code == 200
        archived = client.post(f'/api/books/{book_id}/kind', json={'kind': 'archived'}).json()
        assert archived['book_kind'] == 'archived'
        assert client.get('/api/books?kind=all').json() == []
        assert [item['book_id'] for item in client.get('/api/books?kind=archived').json()] == [book_id]

        restored = client.post(f'/api/books/{book_id}/kind', json={'kind': 'user'}).json()
        assert restored['book_kind'] == 'user'
        assert [item['book_id'] for item in client.get('/api/books?kind=all').json()] == [book_id]


def test_http_updates_story_bible_with_revision_fence(tmp_path):
    from story_core.http import create_app
    from test_workflow import result_for

    with TestClient(create_app(tmp_path)) as client:
        opened = client.post('/api/books', json={'request': '开书', 'chapter_count': 1, 'target_words': 80}).json()
        book_id = opened['book_id']
        client.post(f'/api/books/{book_id}/control', json={'action': 'start'})
        brief_task = client.post(f'/api/books/{book_id}/next', json={}).json()
        client.post('/api/tasks/submit', json={'task_id': brief_task['task_id'], 'lease_id': brief_task['lease_id'], 'result': result_for(brief_task)}).raise_for_status()
        outline_task = client.post(f'/api/books/{book_id}/next', json={}).json()
        client.post('/api/tasks/submit', json={'task_id': outline_task['task_id'], 'lease_id': outline_task['lease_id'], 'result': result_for(outline_task)}).raise_for_status()
        book = client.get(f'/api/books/{book_id}').json()
        book['brief']['characters'][0]['name'] = '谢停舟'

        response = client.post(f'/api/books/{book_id}/story-bible', json={
            'brief': book['brief'], 'plan': book['plan'], 'expected_revision': book['revision'],
        })

        assert response.status_code == 200, response.text
        assert response.json()['brief']['characters'][0]['name'] == '谢停舟'


def test_http_creates_an_observable_idea_job(tmp_path, monkeypatch):
    from story_core.http import create_app

    class FakeProvider:
        def generate(self, task):
            return {'ideas': [{'title': f'《方向{i}》', 'logline': '一句钩子', 'story_core': '一个冲突', 'reader_promise': '一个承诺'} for i in range(1, 4)]}

    monkeypatch.setattr('story_core.http.configured_provider', lambda **_: FakeProvider())
    with TestClient(create_app(tmp_path)) as client:
        created = client.post('/api/ideas', json={'request': '写本修仙小说', 'genre_hint': '修仙逆袭'}).json()
        assert created['status'] in ('queued', 'running', 'complete')
        for _ in range(20):
            result = client.get(f"/api/ideas/{created['job_id']}").json()
            if result['status'] in ('complete', 'failed'):
                break
        assert result['status'] == 'complete'
        assert len(result['ideas']) == 3


def test_http_ai_configuration_returns_only_key_presence(tmp_path):
    from fastapi.testclient import TestClient
    from story_core.ai_config import AISettings
    from story_core.http import create_app

    class Vault:
        values = {}
        def get(self, account): return self.values.get(account)
        def set(self, account, value): self.values[account] = value

    settings = AISettings(tmp_path / 'ai.json', Vault())
    with TestClient(create_app(tmp_path / 'books', ai_settings=settings)) as client:
        saved = client.post('/api/ai-config', json={'provider': 'deepseek', 'model': 'deepseek-v4-flash', 'api_key': 'sk-not-in-response'}).json()
        assert saved['key_configured'] is True
        assert 'sk-not-in-response' not in json.dumps(saved)
        assert client.get('/api/ai-config').json()['provider'] == 'deepseek'


def test_http_ai_configuration_exposes_model_choices_but_not_secrets(tmp_path):
    from story_core.ai_config import AISettings
    from story_core.http import create_app

    class Vault:
        def get(self, account): return 'secret-only-in-vault'
        def set(self, account, value): pass

    settings = AISettings(tmp_path / 'ai.json', Vault())
    settings.save('deepseek', model='deepseek-v4-flash', api_key='unused')
    with TestClient(create_app(tmp_path, ai_settings=settings)) as client:
        response = client.get('/api/ai-config')
        assert response.status_code == 200
        body = response.json()
        assert 'deepseek-v4-pro' in next(item for item in body['providers'] if item['id'] == 'deepseek')['models']
        assert 'secret-only-in-vault' not in response.text
