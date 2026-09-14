import json

import pytest

from story_core.errors import StoryError
from story_core.storage import dumps
from test_workflow import make, to_stage, result_for
from test_memory import seed


def writing(tmp_path, monkeypatch, stage='draft'):
    monkeypatch.setenv('HULK_CONTEXT_MODE', 'adaptive')
    monkeypatch.setenv('HULK_CONTEXT_WINDOW_TOKENS', '64000')
    service, book = make(tmp_path, chapters=3, review_mode='bounded')
    outline = to_stage(service, book, 'outline')
    service.submit_task(outline['task_id'], outline['lease_id'], result_for(outline))
    seed(service.store, book, 1, '铜牌借给沈知秋。苏晚知晓密道。', [
        dict(kind='fact', key='铜牌旧约', value='借给沈知秋', evidence='铜牌借给沈知秋', visibility='reader'),
        dict(kind='knowledge', key='秘密', value='密道', evidence='苏晚知晓密道', owner='苏晚', visibility='author')])
    with service.store.write(book) as conn:
        conn.execute("UPDATE runs SET chapter_number=2,stage=?,candidate=? WHERE book_id=?", (stage, dumps(result_for({'stage':'draft'})) if stage != 'draft' else None, book))
    return service, book, service.next_task(book)


def test_lookup_reissues_bounded_context_without_consuming_content_review(tmp_path, monkeypatch):
    service, book, task = writing(tmp_path, monkeypatch, stage='continuity')
    original = task['input']['candidate']
    result = {'context_lookup': {'query': '铜牌旧约', 'reason': '核对借物关系'}}
    assert task['input']['lookup_policy']['remaining'] == 2
    response = service.submit_task(task['task_id'], task['lease_id'], result)
    assert response['status'] == 'context_refreshed'
    assert service.submit_task(task['task_id'], task['lease_id'], result) == response
    fresh = service.next_task(book)
    assert fresh['task_id'] != task['task_id']
    assert fresh['input']['candidate'] == original
    assert fresh['input']['lookup_policy']['remaining'] == 1
    assert fresh['input']['historical_evidence'][0]['key'] == '铜牌旧约'
    assert '秘密' not in dumps(fresh['input']['historical_evidence'])
    assert service.status(book)['run']['quality_policy']['review_count'] == 0
    assert len(dumps(fresh['input'])) < len(dumps(task['input'])) + 2000


def test_two_lookups_persist_across_reload_and_third_is_not_another_model_loop(tmp_path, monkeypatch):
    from story_core.service import StoryService
    service, book, task = writing(tmp_path, monkeypatch)
    for _ in range(2):
        service.submit_task(task['task_id'], task['lease_id'], {'context_lookup': {'query': '铜牌', 'reason': '确认'}})
        service = StoryService(tmp_path)
        task = service.next_task(book)
    assert task['input']['lookup_policy']['remaining'] == 0
    assert 'context_lookup' not in dumps(task['output_schema'])
    with pytest.raises(StoryError) as caught:
        service.submit_task(task['task_id'], task['lease_id'], {'context_lookup': {'query': '铜牌', 'reason': '还想查'}})
    assert caught.value.code == 'LOOKUP_LIMIT'
    assert service.status(book)['run']['quality_policy']['review_count'] == 0


def test_lookup_enforces_lease_revision_and_reader_scope(tmp_path, monkeypatch):
    service, book, task = writing(tmp_path, monkeypatch, stage='reader')
    with pytest.raises(StoryError) as caught:
        service.lookup_task(task['task_id'], 'wrong', '铜牌', '查原文')
    assert caught.value.code == 'INVALID_LEASE'
    ack = service.lookup_task(task['task_id'], task['lease_id'], '秘密', '查原文')
    assert ack['hit_count'] == 0
    fresh = service.next_task(book)
    with service.store.write(book) as conn:
        conn.execute('UPDATE books SET revision=revision+1 WHERE id=?', (book,))
    with pytest.raises(StoryError) as caught:
        service.lookup_task(fresh['task_id'], fresh['lease_id'], '铜牌', '查原文')
    assert caught.value.code == 'STALE_REVISION'


def test_shadow_does_not_advertise_or_accept_automatic_lookup(tmp_path):
    service, book = make(tmp_path)
    task = to_stage(service, book, 'draft')
    assert 'lookup_policy' not in task['input']
    with pytest.raises(StoryError) as caught:
        service.submit_task(task['task_id'], task['lease_id'], {'context_lookup': {'query': '铜牌', 'reason': '查原文'}})
    assert caught.value.code == 'LOOKUP_DISABLED'


def test_worker_lookup_round_is_costed_and_then_resumes_content_work(tmp_path, monkeypatch):
    from story_core.providers import run_worker
    service, book, task = writing(tmp_path, monkeypatch)
    # Return the existing worker-owned lease to run_worker.
    class Provider:
        calls = 0
        def generate(self, current):
            self.calls += 1
            self.last_usage = 17
            if self.calls == 1:
                return {'context_lookup': {'query': '铜牌', 'reason': '核对'}}
            service.control(book, 'pause')
            return result_for(current)
    provider = Provider()
    run_worker(service, book, provider, worker_id='host')
    assert provider.calls == 2
    assert service.status(book)['token_usage']['book_total'] == 34


def test_cli_and_http_lookup_return_receipt_only(tmp_path, monkeypatch):
    from test_cli import cli, output
    from fastapi.testclient import TestClient
    from story_core.http import create_app
    service, book, task = writing(tmp_path, monkeypatch)
    receipt = cli(tmp_path, 'lookup', task['task_id'], '--lease', task['lease_id'], '--query', '铜牌', '--reason', '核对')
    assert receipt.returncode == 0, receipt.stderr
    assert output(receipt)['status'] == 'context_refreshed'
    assert 'evidence' not in output(receipt)
    fresh = service.next_task(book, worker_id='gui')
    with TestClient(create_app(tmp_path)) as client:
        response = client.post('/api/tasks/lookup', json={'task_id': fresh['task_id'], 'lease_id': fresh['lease_id'], 'query': '铜牌', 'reason': '核对'})
        assert response.status_code == 200, response.text
        assert response.json()['lookup_number'] == 2
        assert 'evidence' not in response.json()


def test_opt_in_index_query_keeps_legacy_default_and_source_shape(tmp_path, monkeypatch):
    service, book, task = writing(tmp_path, monkeypatch)
    assert service.query(book, '铜牌')['strategy'] == 'chinese-lexical-v1'
    found = service.query(book, '铜牌', strategy='bounded')
    assert found['index_complete'] is True
    assert found['hits'][0]['text'] == '铜牌旧约：借给沈知秋'
    assert found['hits'][0]['source']['quote'] == '铜牌借给沈知秋'
    assert service.query(book, '秘密', role='reader', through_chapter=1, strategy='bounded')['hits'] == []
    with pytest.raises(StoryError):
        service.query(book, '铜牌', role='reader', strategy='bounded')


def test_task_bound_search_inherits_pov_revision_and_boundary(tmp_path, monkeypatch):
    service, book, task = writing(tmp_path, monkeypatch)
    args = dict(task_id=task['task_id'], lease_id=task['lease_id'], strategy='bounded')
    assert service.query(book, '秘密', **args)['hits'] == []
    with pytest.raises(StoryError) as error:
        service.query(book, '铜牌', through_chapter=100, **args)
    assert error.value.code == 'INVALID_SCOPE'
    with service.store.write(book) as conn:
        conn.execute('UPDATE books SET revision=revision+1 WHERE id=?', (book,))
    with pytest.raises(StoryError) as error:
        service.query(book, '铜牌', **args)
    assert error.value.code == 'STALE_REVISION'


def test_lookup_hits_survive_soft_target_and_report_delivered_evidence(tmp_path, monkeypatch):
    service, book, task = writing(tmp_path, monkeypatch)
    service.lookup_task(task['task_id'], task['lease_id'], '铜牌', '核对')
    monkeypatch.setenv('HULK_CONTEXT_SOFT_TOKENS', '1')
    fresh = service.next_task(book)
    assert fresh['input']['historical_evidence'][0]['key'] == '铜牌旧约'
    assert fresh['input']['lookup_result']['delivered_count'] == 1
