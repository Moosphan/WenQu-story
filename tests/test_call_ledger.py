import httpx
import pytest
from story_core.errors import StoryError
from story_core.providers import OpenAICompatible, run_worker
from test_workflow import make, result_for


def test_truncated_usage_is_retained_and_next_failure_clears_it():
    responses = iter([
        httpx.Response(200, json={'usage': {'total_tokens': 83}, 'choices': [{'finish_reason': 'length'}]}),
        httpx.Response(503),
    ])
    provider = OpenAICompatible('http://localhost/v1', 'fixture', 'secret', client=httpx.Client(transport=httpx.MockTransport(lambda _: next(responses))))
    for expected in (83, None):
        with pytest.raises(StoryError):
            provider.generate({'input': {}, 'output_schema': {}})
        assert provider.last_usage == expected


def test_rejected_result_has_usage_and_unknown_retry_is_separate(tmp_path):
    service, book = make(tmp_path)
    class Invalid:
        def generate(self, task):
            self.last_usage = 50
            return {}
    with pytest.raises(StoryError):
        run_worker(service, book, Invalid())
    usage = service.status(book)['token_usage']
    assert usage['book_total'] == 50
    assert usage['calls'][0]['status'] == 'failed'
    service.control(book, 'resume')
    class Offline:
        def generate(self, task):
            raise StoryError('PROVIDER_ERROR', 'offline')
    with pytest.raises(StoryError):
        run_worker(service, book, Offline())
    usage = service.status(book)['token_usage']
    assert usage['call_count'] == 2
    assert usage['unknown_calls'] == 1
    assert usage['book_total'] == 50


def test_cancelled_response_is_costed_without_submission(tmp_path):
    service, book = make(tmp_path)
    class Cancel:
        def generate(self, task):
            self.last_usage = 20
            service.control(book, 'cancel')
            return result_for(task)
    run_worker(service, book, Cancel())
    usage = service.status(book)['token_usage']
    assert usage['book_total'] == 20
    assert usage['calls'][0]['status'] == 'cancelled'
    assert service.get_book(book)['brief'] is None


def test_unfinished_call_survives_reload_as_unknown(tmp_path):
    from story_core.service import StoryService
    service, book = make(tmp_path)
    task = service.next_task(book)
    with service.store.write(book) as conn:
        service.store.event(conn, book, 'provider_call_started', {
            'call_id': 'interrupted', 'task_id': task['task_id'], 'started_at': 1,
        }, task['run_id'])
    usage = StoryService(tmp_path).status(book)['token_usage']
    assert usage['unknown_calls'] == 1
    assert usage['call_count'] == 1
    assert usage['usage_records'] == 0
    assert usage['calls'][0]['status'] == 'unconfirmed'
    assert usage['calls'][0]['chapter_number'] == 1
