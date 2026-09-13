import pytest

from test_workflow import finish, make


def test_human_feedback_is_bound_to_a_committed_version_and_appears_in_history(tmp_path):
    service, book = make(tmp_path, chapters=1)
    finish(service, book)
    chapter = service.get_book(book)['chapters'][0]
    revision = service.get_book(book)['revision']

    saved = service.add_human_feedback(
        book, 1, reviewer_type='target_reader', reviewer_name='小林', verdict='revise',
        rating=3, would_continue=False, notes='开头的钩子清楚，但主角决定离开铺子的代价还不够具体。',
    )

    assert saved['version_id'] == chapter['version_id']
    assert service.get_book(book)['revision'] == revision
    record = next(item for item in service.review_history(book) if item['source'] == 'human')
    assert record['reviewer'] == '真人目标读者 · 小林'
    assert record['version_id'] == chapter['version_id']
    assert record['version_label'] == '绑定章节版本 1'
    assert record['result']['notes'].startswith('开头的钩子')
    assert record['rating'] == 3 and record['would_continue'] is False
    assert service.execution_report(book)['human_review_count'] == 1


def test_human_feedback_rejects_foreign_or_unknown_versions_without_writing(tmp_path):
    from story_core.errors import StoryError

    service, book = make(tmp_path, chapters=1)
    finish(service, book)
    other_service, other = make(tmp_path / 'other', chapters=1)
    finish(other_service, other)
    foreign = other_service.get_book(other)['chapters'][0]['version_id']

    with pytest.raises(StoryError) as error:
        service.add_human_feedback(book, 1, reviewer_type='editor', notes='需要补足代价。', version_id=foreign)
    assert error.value.code == 'NOT_FOUND'
    assert not [item for item in service.review_history(book) if item['source'] == 'human']


def test_human_feedback_is_available_over_http(tmp_path):
    from fastapi.testclient import TestClient
    from story_core.http import create_app
    from story_core.service import StoryService

    service, book = make(tmp_path, chapters=1)
    finish(service, book)
    with TestClient(create_app(tmp_path)) as client:
        response = client.post(f'/api/books/{book}/feedback', json={
            'chapter_number': 1, 'reviewer_type': 'logic_reader', 'verdict': 'pass',
            'rating': 4, 'would_continue': True, 'notes': '因果清楚，愿意继续读。',
        })
        assert response.status_code == 200, response.text
        history = client.get(f'/api/books/{book}/reviews').json()['reviews']
        assert any(item['source'] == 'human' and item['reviewer'] == '真人逻辑读者' for item in history)
        assert StoryService(tmp_path).execution_report(book)['human_review_count'] == 1
