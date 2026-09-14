"""Public, source-attributed rank-page snapshots. No login or protected API access."""
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.error import URLError, HTTPError
from urllib.request import Request, urlopen

from .errors import StoryError

FANQIE_RANK_URL = 'https://fanqienovel.com/rank/'
SOURCES = {
    'fanqie_rank': {'label': '番茄 · 古风世情阅读榜', 'url': 'https://fanqienovel.com/rank/0_2_1139', 'status': 'available'},
    'fanqie_male': {'label': '番茄 · 西方奇幻阅读榜', 'url': 'https://fanqienovel.com/rank/1_2_1141', 'status': 'available'},
    'fanqie_female_new': {'label': '番茄 · 古风世情新书榜', 'url': 'https://fanqienovel.com/rank/0_1_1139', 'status': 'available'},
    'fanqie_male_new': {'label': '番茄 · 西方奇幻新书榜', 'url': 'https://fanqienovel.com/rank/1_1_1141', 'status': 'available'},
    'qidian_rank': {'label': '起点 · 排行榜', 'url': 'https://www.qidian.com/rank/', 'status': 'available'},
}

# Category identifiers are taken from the public rank page's own links.
for gender, category, label in [('1', '1140', '东方仙侠'), ('1', '257', '玄幻脑洞'),
                                 ('1', '261', '都市日常'), ('1', '539', '悬疑脑洞'),
                                 ('0', '267', '现言脑洞'), ('0', '23', '种田'), ('0', '24', '快穿')]:
    for rank_type, suffix in [('2', '阅读榜'), ('1', '新书榜')]:
        source_id = f'fanqie_{gender}_{rank_type}_{category}'
        SOURCES[source_id] = {'label': f'番茄 · {label}{suffix}',
                              'url': f'https://fanqienovel.com/rank/{gender}_{rank_type}_{category}', 'status': 'available'}

# These words are only labels for visible public title/summary text. They are not
# a classifier, a platform taxonomy, or a claim about reader demand.
SIGNAL_TERMS = (
    '修仙', '玄幻', '高武', '都市', '悬疑', '推理', '灵异', '规则怪谈', '末世', '科幻',
    '历史', '游戏', '无限流', '经营', '种田', '系统', '直播', '穿越', '重生', '复仇',
    '爱情', '古言', '现言', '校园', '职场', '豪门',
)


def derive_signals(items):
    """Return explainable term counts backed by the exact visible source items."""
    evidence = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        text = f"{item.get('title', '')}\n{item.get('summary', '')}"
        source_item_id = item.get('source_item_id')
        if not isinstance(source_item_id, str) or not source_item_id:
            continue
        for term in SIGNAL_TERMS:
            if term in text:
                evidence.setdefault(term, []).append(source_item_id)
    return [
        {'label': label, 'count': len(ids), 'source_item_ids': ids,
         'observation': '在公开条目的标题或简介中出现'}
        for label, ids in sorted(evidence.items(), key=lambda value: (-len(value[1]), value[0]))
    ]


def create_idea_cards(observations, preferences, limit=3):
    """Build editable, deterministic author suggestions from selected observations."""
    genre_tags = preferences.get('genre_tags') or []
    topic = genre_tags[0] if genre_tags else (observations[0]['label'] if observations else '成长')
    premise = preferences.get('request', '').strip()
    reader = preferences.get('target_reader', '').strip() or '偏好明确成长和回报的读者'
    boundary = preferences.get('content_boundaries', '').strip()
    platform = preferences.get('platform', 'general')
    variants = [
        ('代价开局', f'主角得到一份与“{topic}”有关的能力，但每次使用都必须先付出可见代价。', '每次选择都有即时收益与后续代价，适合逐章兑现。'),
        ('关系推进', f'主角靠解决身边人的真实难题进入“{topic}”主线，每次合作都改变一段关系。', '人物关系与主线目标同时推进，避免只靠数值升级。'),
        ('倒计时目标', f'主角必须在一个明确期限前完成“{topic}”目标，失败会失去最在意的东西。', '章节目标清晰，方便设计阶段性反转和回收。'),
    ]
    selected = observations[:6]
    cards = []
    for index, (structure, seed, promise) in enumerate(variants[:limit], start=1):
        hook = premise or seed
        title = f'{topic}：{structure}'
        cards.append({
            'idea_id': f'idea_{index}', 'title': title, 'structure': structure, 'hook': hook,
            'reader_promise': promise, 'author_note': '这是基于作者偏好与公开条目文字的可编辑灵感，不代表榜单趋势或平台建议。',
            'is_canon': False, 'source_observations': selected,
            'project': {'platform': platform, 'genre_tags': genre_tags or [topic],
                        'target_reader': reader, 'content_boundaries': boundary,
                        'custom_notes': f'选题灵感：{hook}'},
        })
    return cards


def _assignment_json(html, marker='window.__INITIAL_STATE__='):
    start = html.find(marker)
    if start < 0:
        raise StoryError('MARKET_PARSE_FAILED', '未找到公开榜单状态，页面结构可能已变化。')
    start += len(marker)
    depth = 0
    quote = None
    escaped = False
    for position in range(start, len(html)):
        char = html[position]
        if quote:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in ('"', "'"):
            quote = char
        elif char == '{':
            depth += 1
        elif char == '}':
            depth -= 1
            if depth == 0:
                try:
                    payload = html[start:position + 1]
                    # Public SSR state is a JS literal: optional fields may be
                    # undefined. Normalize tokens only, never text inside strings.
                    payload = re.sub(r'"(?:\\.|[^"\\])*"|\bundefined\b',
                                     lambda match: 'null' if match.group() == 'undefined' else match.group(), payload)
                    return json.loads(payload)
                except json.JSONDecodeError as error:
                    raise StoryError('MARKET_PARSE_FAILED', '公开榜单状态不是有效 JSON。') from error
    raise StoryError('MARKET_PARSE_FAILED', '公开榜单状态不完整。')


def parse_fanqie_rank(html, detail_get=None):
    state = _assignment_json(html)
    books = state.get('rank', {}).get('book_list')
    if not isinstance(books, list) or not books:
        raise StoryError('MARKET_PARSE_FAILED', '公开榜单没有可验证的作品条目。')
    items = []
    def enrich(row):
        row = dict(row)
        if any('\ue000' <= c <= '\uf8ff' for c in str(row.get('bookName', '')) + str(row.get('abstract', ''))):
            if not detail_get:
                raise StoryError('MARKET_PARSE_FAILED', '榜单文字需要从公开书籍详情页补全。')
            book_id = str(row.get('bookId', ''))
            if not book_id.isdigit():
                raise StoryError('MARKET_PARSE_FAILED', '书籍 ID 无效。')
            detail = _assignment_json(detail_get('https://fanqienovel.com/page/' + book_id)).get('page', {})
            if str(detail.get('bookId')) != book_id:
                raise StoryError('MARKET_PARSE_FAILED', '详情页与榜单书籍 ID 不一致。')
            for field in ('bookName', 'abstract', 'author'):
                row[field] = detail.get(field, row.get(field, ''))
            if any('\ue000' <= c <= '\uf8ff' for c in str(row.get('bookName', '')) + str(row.get('abstract', ''))):
                raise StoryError('MARKET_PARSE_FAILED', '详情页仍有无法识别的字体编码。')
        return row
    with ThreadPoolExecutor(max_workers=4) as pool:
        resolved = list(pool.map(enrich, [r for r in books[:20] if isinstance(r, dict)]))
    for row in resolved:
        if not isinstance(row, dict) or not isinstance(row.get('bookName'), str) or not row['bookName'].strip():
            continue
        rank = row.get('currentPos')
        if type(rank) is not int or rank < 1:
            continue
        if any('\ue000' <= char <= '\uf8ff' for char in row['bookName']):
            raise StoryError('MARKET_PARSE_FAILED', '公开榜单以自定义字体编码作品名，当前适配器无法可靠还原标题。')
        items.append({'rank': rank, 'title': row['bookName'].strip(), 'author': str(row.get('author') or '').strip(),
                      'url': 'https://fanqienovel.com/page/' + str(row.get('bookId') or ''),
                      'summary': str(row.get('abstract') or '').strip()[:600], 'word_count': str(row.get('wordNumber') or ''),
                      'read_count': str(row.get('read_count') or row.get('readCount') or ''), 'source_item_id': str(row.get('bookId') or '')})
    if not items:
        raise StoryError('MARKET_PARSE_FAILED', '公开榜单条目缺少可验证的排名或书名。')
    return sorted(items, key=lambda item: item['rank'])


def fetch_fanqie_rank(http_get=None, source_id='fanqie_rank'):
    getter = http_get or _http_get
    return parse_fanqie_rank(getter(SOURCES[source_id]['url']), getter)


def fetch_qidian_rank(http_get=None):
    html = (http_get or _http_get)(SOURCES['qidian_rank']['url'])
    if 'probe.js' in html or 'captcha' in html.lower():
        raise StoryError('MARKET_BLOCKED', '起点返回浏览器验证页；请在官方页面查看，当前未获取到榜单。')
    raise StoryError('MARKET_PARSE_FAILED', '起点页面暂未匹配到可验证的榜单结构，请在官方页面查看。')


def _http_get(url):
    request = Request(url, headers={'User-Agent': 'HulkStoryResearch/0.1 (+local author workspace)'})
    try:
        with urlopen(request, timeout=8) as response:
            if response.status != 200:
                raise StoryError('MARKET_FETCH_FAILED', f'平台返回 HTTP {response.status}，未获取榜单；可能要求浏览器验证。')
            return response.read(5_000_000).decode('utf-8', errors='replace')
    except (URLError, TimeoutError) as error:
        raise StoryError('MARKET_FETCH_FAILED', f'榜单请求失败（{type(error).__name__}），请稍后刷新。') from None


def unavailable_snapshot(source_id):
    source = SOURCES[source_id]
    return {'source_id': source_id, 'label': source['label'], 'source_url': source['url'], 'status': source['status'],
            'collected_at': time.time(), 'items': [], 'signals': [], 'error': '该公开页面当前要求浏览器验证，尚未启用抓取适配器。'}
