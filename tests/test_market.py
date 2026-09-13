from story_core.errors import StoryError
from story_core.market import derive_signals, parse_fanqie_rank


FANQIE_PAGE = '''<script>window.__INITIAL_STATE__={"rank":{"book_list":[
{"currentPos":2,"bookName":"灵田问道","author":"青石","abstract":"修仙种田，灵植可许愿。","wordNumber":"320000","read_count":"12000","bookId":"b2"},
{"currentPos":1,"bookName":"夜雨旧宅","author":"墨客","abstract":"都市悬疑，收音机播出失踪名单。","wordNumber":"210000","read_count":"9900","bookId":"b1"}
]}};</script>'''


def test_fanqie_public_rank_parser_requires_verifiable_title_and_rank():
    items = parse_fanqie_rank(FANQIE_PAGE)
    assert [(item['rank'], item['title']) for item in items] == [(1, '夜雨旧宅'), (2, '灵田问道')]
    assert items[0]['source_item_id'] == 'b1'
    try:
        parse_fanqie_rank('<html>no rank state</html>')
    except StoryError as error:
        assert error.code == 'MARKET_PARSE_FAILED'
    else:
        raise AssertionError('missing state must not become a fake empty ranking')


def test_market_snapshot_caches_public_fetch_and_marks_failed_refresh_stale(tmp_path):
    from story_core.service import StoryService

    service = StoryService(tmp_path)
    calls = []
    fresh = service.market_snapshot('fanqie_rank', refresh=True, http_get=lambda url: calls.append(url) or FANQIE_PAGE)
    assert fresh['status'] == 'fresh' and len(calls) == 1
    cached = service.market_snapshot('fanqie_rank', http_get=lambda url: (_ for _ in ()).throw(AssertionError('should not fetch')))
    assert cached['items'] == fresh['items']
    stale = service.market_snapshot('fanqie_rank', refresh=True, http_get=lambda url: '<html>changed</html>')
    assert stale['status'] == 'stale' and stale['items'] == fresh['items']
    qidian = service.market_snapshot('qidian_rank', refresh=True)
    assert qidian['status'] == 'adapter_unavailable' and not qidian['items']


def test_market_sources_are_available_over_http(tmp_path):
    from fastapi.testclient import TestClient
    from story_core.http import create_app

    with TestClient(create_app(tmp_path)) as client:
        sources = client.get('/api/market/sources').json()['sources']
        assert {source['source_id'] for source in sources} == {'fanqie_rank', 'qidian_rank'}
        qidian = client.get('/api/market/qidian_rank').json()
        assert qidian['status'] == 'adapter_unavailable'


def test_market_signals_keep_source_evidence_and_ideas_remain_author_suggestions(tmp_path):
    from story_core.service import StoryService

    signals = derive_signals(parse_fanqie_rank(FANQIE_PAGE))
    cultivation = next(signal for signal in signals if signal['label'] == '修仙')
    assert cultivation['count'] == 1
    assert cultivation['source_item_ids'] == ['b2']
    assert cultivation['observation'] == '在公开条目的标题或简介中出现'

    service = StoryService(tmp_path)
    service.market_snapshot('fanqie_rank', refresh=True, http_get=lambda url: FANQIE_PAGE)
    result = service.market_ideas(['fanqie_rank'], {
        'platform': 'fanqie', 'genre_tags': ['修仙'], 'target_reader': '喜欢成长与经营回报',
        'content_boundaries': '不写后宫', 'request': '灵物许愿带来机缘，但每次帮助都有代价。',
    })
    assert len(result['ideas']) == 3
    assert all(idea['is_canon'] is False for idea in result['ideas'])
    assert all(idea['source_observations'][0]['source_item_ids'] == ['b2'] for idea in result['ideas'])
    assert all(idea['project']['platform'] == 'fanqie' for idea in result['ideas'])


def test_market_ideas_are_available_over_http(tmp_path):
    from fastapi.testclient import TestClient
    from story_core.http import create_app
    from story_core.service import StoryService

    StoryService(tmp_path).market_snapshot('fanqie_rank', refresh=True, http_get=lambda url: FANQIE_PAGE)
    with TestClient(create_app(tmp_path)) as client:
        response = client.post('/api/market/ideas', json={
            'source_ids': ['fanqie_rank'], 'preferences': {'genre_tags': ['都市', '悬疑']},
        })
        assert response.status_code == 200, response.text
        assert len(response.json()['ideas']) == 3
