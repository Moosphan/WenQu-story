import pytest
from story_core.errors import StoryError
from test_task_lookup import writing


def test_author_fact_acceptance_invalidates_old_lease_once_and_preserves_candidate(tmp_path, monkeypatch):
    service, book, task = writing(tmp_path, monkeypatch, stage='continuity')
    entity = service.memory_entity(book, '铜牌', actor='author')['entity_id']
    version = service.get_book(book)['chapters'][0]['version_id']
    proposal = service.memory_propose(book, [dict(subject_entity_id=entity, predicate='possession', value='沈知秋',
        source_chapter=1, source_version=version, evidence='铜牌借给沈知秋', story_valid_from=5)], request_id='p')['proposals'][0]['id']
    revision = service.get_book(book)['revision']
    with pytest.raises(StoryError):
        service.memory_decide(book, proposal, decision='accept', actor='author', expected_revision=revision, request_id='a')
    args = dict(decision='accept', actor='author', expected_revision=revision, request_id='a', trust=True)
    result = service.memory_decide(book, proposal, **args)
    assert result['canonical_changed']
    fresh = service.next_task(book)
    assert fresh['task_id'] != task['task_id']
    assert fresh['input']['candidate'] == task['input']['candidate']
    assert service.memory_decide(book, proposal, **args) == result
    assert service.next_task(book)['task_id'] == fresh['task_id']
    assert service.status(book)['run']['quality_policy']['review_count'] == 0


def test_http_author_review_and_local_backfill(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from story_core.http import create_app
    service, book, task = writing(tmp_path, monkeypatch)
    with TestClient(create_app(tmp_path)) as client:
        url = f'/api/books/{book}/memory'
        entity = client.post(url+'/entities', json={'name':'铜牌'}).json()['entity_id']
        version = service.get_book(book)['chapters'][0]['version_id']
        response = client.post(url+'/proposals', json={'request_id':'p','candidates':[dict(subject_entity_id=entity,
            predicate='possession', value='沈知秋', source_chapter=1, source_version=version, evidence='铜牌借给沈知秋', story_valid_from=5)]})
        assert response.status_code == 200, response.text
        proposal = response.json()['proposals'][0]['id']
        listed = client.get(url+'/proposals').json()
        assert listed['items'][0]['source_current']
        accepted = client.post(url+f'/proposals/{proposal}/decision', json={'decision':'accept',
            'expected_revision':service.get_book(book)['revision'], 'request_id':'a', 'trust':True})
        assert accepted.status_code == 200, accepted.text
        maintained = client.post(url+'/maintenance', json={'backfill':True,'limit':1})
        assert maintained.status_code == 200, maintained.text
        assert client.get(url+'/maintenance').json()['total'] == 1


def test_cli_author_decision_requires_trust_flag_and_reports_current_revision(tmp_path, monkeypatch):
    from test_cli import cli, output
    service, book, task = writing(tmp_path, monkeypatch)
    entity = service.memory_entity(book, '铜牌')['entity_id']
    version = service.get_book(book)['chapters'][0]['version_id']
    proposal = service.memory_propose(book, [dict(subject_entity_id=entity, predicate='possession', value='沈知秋',
        source_chapter=1, source_version=version, evidence='铜牌借给沈知秋', story_valid_from=5)], request_id='p')['proposals'][0]['id']
    revision = str(service.get_book(book)['revision'])
    args = ('memory', book, 'decide', proposal, '--decision', 'accept', '--actor', 'author', '--expected-revision', revision, '--request-id', 'a')
    assert cli(tmp_path, *args).returncode != 0
    accepted = cli(tmp_path, *args, '--verified-by-author')
    assert accepted.returncode == 0, accepted.stderr
    assert output(accepted)['status'] == 'accepted'
    listed = output(cli(tmp_path, 'memory', book, 'list'))
    assert listed['items'][0]['subject_display_name'] == '铜牌'
