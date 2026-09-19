import pytest

from story_core.errors import StoryError
from test_long_memory import chapter, world


def test_summary_requires_author_verification_and_current_complete_sources(world):
    from story_core.memory_summaries import submit_summary, decide_summary, select_summaries
    store, book = world
    with store.write(book) as conn:
        source = chapter(conn, book, 1)
        proposal = submit_summary(conn, book, level='chapter', chapter_start=1, chapter_end=1,
            text='主角花去七枚铜钱，伤势恢复。', source_refs=[{'chapter_number': 1, 'version_id': source, 'quote': '伤口愈合'}],
            visibility='reader', request_id='propose')
        assert select_summaries(conn, book, through_chapter=1, role='reader') == []
        with pytest.raises(StoryError):
            decide_summary(conn, book, proposal['summary_id'], decision='accept', actor='author',
                           expected_revision=0, request_id='decide', trust=False)
        result = decide_summary(conn, book, proposal['summary_id'], decision='accept', actor='author',
                               expected_revision=0, request_id='decide', trust=True)
        assert result['revision'] == 1
        assert len(select_summaries(conn, book, through_chapter=1, role='reader')) == 1
        assert select_summaries(conn, book, through_chapter=0, role='reader') == []
        assert select_summaries(conn, book, through_chapter=1, role='reader', pov_entity_id='unknown') == []
        chapter(conn, book, 1, '另一个版本。')
        assert select_summaries(conn, book, through_chapter=1, role='reader') == []
        assert conn.execute('SELECT text FROM lm_semantic_summaries').fetchone()[0] == '主角花去七枚铜钱，伤势恢复。'


def test_volume_summary_cannot_omit_source_chapter_or_reuse_request_id(world):
    from story_core.memory_summaries import submit_summary
    store, book = world
    with store.write(book) as conn:
        first = chapter(conn, book, 1)
        second = chapter(conn, book, 2)
        args = dict(level='volume', chapter_start=1, chapter_end=2, text='两章的语义概括。',
                    source_refs=[{'chapter_number': 1, 'version_id': first, 'quote': '铜钱'}],
                    visibility='author', request_id='summary')
        with pytest.raises(StoryError):
            submit_summary(conn, book, **args)
        args['source_refs'].append({'chapter_number': 2, 'version_id': second, 'quote': '伤口'})
        one = submit_summary(conn, book, **args)
        assert submit_summary(conn, book, **args) == one
        with pytest.raises(StoryError) as error:
            submit_summary(conn, book, **{**args, 'text': '不同摘要。'})
        assert error.value.code == 'IDEMPOTENCY_CONFLICT'


def test_summary_stale_dependencies_and_wrong_book_are_excluded(world):
    from story_core.memory_summaries import submit_summary, decide_summary, select_summaries
    store, book = world
    other = store.create_book('other', 'other', {})['book_id']
    with store.write(book) as conn:
        source = chapter(conn, book, 1)
        proposal = submit_summary(conn, book, level='chapter', chapter_start=1, chapter_end=1,
            text='恢复。', source_refs=[{'chapter_number': 1, 'version_id': source, 'quote': '伤口'}],
            visibility='reader', request_id='propose')
        with pytest.raises(StoryError):
            decide_summary(conn, other, proposal['summary_id'], decision='accept', actor='author',
                           expected_revision=0, request_id='wrong', trust=True)
        decide_summary(conn, book, proposal['summary_id'], decision='reject', actor='author',
                       expected_revision=0, request_id='reject')
        assert select_summaries(conn, book, through_chapter=1, role='author') == []


def test_summary_http_and_cli_workflow(world):
    from fastapi.testclient import TestClient
    from story_core.http import create_app
    from test_cli import cli, output
    store, book = world
    with store.write(book) as conn:
        source = chapter(conn, book, 1)
    with TestClient(create_app(store.root)) as client:
        url = f'/api/books/{book}/memory/summaries'
        response = client.post(url, json=dict(level='chapter', chapter_start=1, chapter_end=1,
            text='伤口已恢复。', source_refs=[{'chapter_number': 1, 'version_id': source, 'quote': '伤口愈合'}],
            visibility='reader', request_id='http-propose'))
        assert response.status_code == 200, response.text
        identity = response.json()['summary_id']
        accepted = client.post(url + f'/{identity}/decision', json=dict(decision='accept', actor='author',
            expected_revision=0, request_id='http-accept', trust=True))
        assert accepted.status_code == 200, accepted.text
        assert client.get(url).json()['items'][0]['status'] == 'accepted'
    result = cli(store.root, 'memory', book, 'summaries')
    assert result.returncode == 0, result.stderr
    assert output(result)['items'][0]['source_status'] == 'current'


def test_summary_is_optional_and_never_replaces_minimum_original_evidence():
    from story_core.context_compiler import ContextCompiler, ContextPolicy
    data = {'instruction': 'write', 'semantic_summaries': [{'text': 'summary ' * 10000}],
            'historical_evidence': [{'id': 'source', 'key': 'debt', 'evidence': 'original'}]}
    result = ContextCompiler(ContextPolicy(mode='adaptive', context_window=20000, soft_target=1000)).compile(data, {}, stage='draft')
    assert result.executable
    assert result.input['historical_evidence'][0]['id'] == 'source'
    assert not result.input.get('semantic_summaries')


def test_dependency_rewrite_invalidates_summary_even_when_own_text_is_unchanged(world):
    from story_core.memory_summaries import submit_summary, decide_summary, select_summaries
    store, book = world
    with store.write(book) as conn:
        parent = chapter(conn, book, 1)
        source = chapter(conn, book, 2)
        conn.execute('INSERT INTO lm_dependencies VALUES (?,?,?,?)', (book, 'main', source, parent))
        summary = submit_summary(conn, book, level='chapter', chapter_start=2, chapter_end=2,
            text='伤势恢复。', source_refs=[dict(chapter_number=2, version_id=source, quote='伤口愈合')],
            visibility='reader', request_id='proposal')
        decide_summary(conn, book, summary['summary_id'], decision='accept', actor='author',
                       expected_revision=0, request_id='accept', trust=True)
        assert select_summaries(conn, book, through_chapter=2, role='reader')
        chapter(conn, book, 1, '来源改写。')
        assert select_summaries(conn, book, through_chapter=2, role='reader') == []


def test_summary_owner_follows_entity_merge(world):
    from story_core.memory_summaries import submit_summary, decide_summary, select_summaries
    from story_core.long_memory import register_entity
    from story_core.memory_identity import merge_entities
    store, book = world
    with store.write(book) as conn:
        a, b = [register_entity(conn, book, name) for name in ('甲', '乙')]
        version = chapter(conn, book, 1)
        summary = submit_summary(conn, book, level='chapter', chapter_start=1, chapter_end=1, text='伤愈。',
            source_refs=[dict(chapter_number=1, version_id=version, quote='伤口愈合')],
            visibility='reader', owner_entity_id=a, request_id='p')
        decide_summary(conn, book, summary['summary_id'], decision='accept', actor='author',
                       expected_revision=0, request_id='a', trust=True)
        merge_entities(conn, book, a, b, conflict_resolutions={}, actor='author', expected_revision=1, request_id='merge')
        assert select_summaries(conn, book, through_chapter=1, role='reader', pov_entity_id=b)
        assert submit_summary(conn, book, level='chapter', chapter_start=1, chapter_end=1, text='伤愈。',
            source_refs=[dict(chapter_number=1, version_id=version, quote='伤口愈合')],
            visibility='reader', owner_entity_id=a, request_id='p') == summary


def test_stale_newer_summary_does_not_hide_older_current_summary(world):
    from story_core.memory_summaries import submit_summary, decide_summary, select_summaries
    store, book = world
    with store.write(book) as conn:
        for number in (1, 2):
            version = chapter(conn, book, number)
            summary = submit_summary(conn, book, level='chapter', chapter_start=number, chapter_end=number,
                text='恢复。', source_refs=[dict(chapter_number=number, version_id=version, quote='伤口愈合')],
                visibility='reader', request_id=f'p{number}')
            decide_summary(conn, book, summary['summary_id'], decision='accept', actor='author',
                           expected_revision=number-1, request_id=f'a{number}', trust=True)
        chapter(conn, book, 2, '不同的第二章。')
        summaries = select_summaries(conn, book, through_chapter=2, role='reader', recent_count=1)
        assert summaries[0]['chapter_end'] == 1


def test_explicit_source_invalidation_retires_summary_until_reverified(world):
    from story_core.memory_summaries import submit_summary, decide_summary, select_summaries
    from story_core.long_memory import invalidate_version
    store, book = world
    with store.write(book) as conn:
        version = chapter(conn, book, 1)
        proposal = submit_summary(conn, book, level='chapter', chapter_start=1, chapter_end=1,
            text='恢复。', source_refs=[dict(chapter_number=1, version_id=version, quote='伤口愈合')],
            visibility='reader', request_id='p')
        decide_summary(conn, book, proposal['summary_id'], decision='accept', actor='author',
                       expected_revision=0, request_id='a', trust=True)
        invalidate_version(conn, book, version)
        assert select_summaries(conn, book, through_chapter=1, role='reader') == []
