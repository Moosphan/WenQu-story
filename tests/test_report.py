from test_workflow import make, step, finish


def test_report_uses_persisted_results_and_versions(tmp_path):
    from story_core.service import StoryService

    service, book = make(tmp_path, chapters=1)
    original = {'chapter_number': 1, 'title': '旧稿', 'body': '未审正文'}
    service.control(book, 'cancel')
    service.import_chapters(book, [original])
    service.start_run(book)
    for _ in range(2):
        step(service, book)
    report = StoryService(tmp_path).execution_report(book)
    assert report['review_count'] == 0
    assert report['chapters'][0]['versions'] == 1
    assert report['chapters'][0]['status'] == 'imported'
    assert report['tasks']['submitted'] == 2
    assert report['literary_quality'] == 'not-human-validated'


def test_report_separates_fixture_reviews_from_human_validation(tmp_path):
    service, book = make(tmp_path, chapters=1)
    finish(service, book)
    report = service.execution_report(book)
    assert report['review_count'] == 4
    assert report['chapters'][0]['status'] == 'committed'
    assert report['reported_tokens'] == 0
    assert report['usage_records'] == 0
    assert report['literary_quality'] == 'not-human-validated'


def test_report_available_over_cli_and_http(tmp_path):
    from fastapi.testclient import TestClient
    from story_core.http import create_app
    from test_cli import cli, output

    service, book = make(tmp_path)
    assert output(cli(tmp_path, 'report', book))['book_id'] == book
    with TestClient(create_app(tmp_path)) as client:
        response = client.get(f'/api/books/{book}/report')
        assert response.status_code == 200
        assert response.json()['review_count'] == 0


def test_report_and_status_aggregate_reported_tokens_by_chapter(tmp_path):
    from test_workflow import make, result_for

    service, book = make(tmp_path, chapters=1)
    task = service.next_task(book)
    service.submit_task(task['task_id'], task['lease_id'], result_for(task))
    with service.store.write(book) as conn:
        service.store.event(conn, book, 'provider_usage', {
            'task_id': task['task_id'], 'reported_tokens': 321,
            'execution': {'executor': 'fixture', 'models': ['test-model']},
        }, task['run_id'])

    report = service.execution_report(book)
    status = service.status(book)
    assert report['token_usage']['book_total'] == 321
    assert report['token_usage']['by_chapter'][0] == {'chapter_number': 1, 'reported_tokens': 321, 'task_count': 1}
    assert status['token_usage']['book_total'] == 321
