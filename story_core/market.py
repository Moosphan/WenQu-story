"""Public, source-attributed rank-page snapshots. No login or protected API access."""
import json
import time
from urllib.request import Request, urlopen

from .errors import StoryError

FANQIE_RANK_URL = 'https://fanqienovel.com/rank/'
SOURCES = {
    'fanqie_rank': {'label': '番茄小说排行榜', 'url': FANQIE_RANK_URL, 'status': 'available'},
    'qidian_rank': {'label': '起点中文网排行榜', 'url': 'https://www.qidian.com/rank/', 'status': 'adapter_unavailable'},
}

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
                    return json.loads(html[start:position + 1])
                except json.JSONDecodeError as error:
                    raise StoryError('MARKET_PARSE_FAILED', '公开榜单状态不是有效 JSON。') from error
    raise StoryError('MARKET_PARSE_FAILED', '公开榜单状态不完整。')


def parse_fanqie_rank(html):
    state = _assignment_json(html)
    books = state.get('rank', {}).get('book_list')
    if not isinstance(books, list) or not books:
        raise StoryError('MARKET_PARSE_FAILED', '公开榜单没有可验证的作品条目。')
    items = []
    for row in books[:50]:
        if not isinstance(row, dict) or not isinstance(row.get('bookName'), str) or not row['bookName'].strip():
            continue
        rank = row.get('currentPos')
        if type(rank) is not int or rank < 1:
            continue
        if any('\ue000' <= char <= '\uf8ff' for char in row['bookName']):
            raise StoryError('MARKET_PARSE_FAILED', '公开榜单以自定义字体编码作品名，当前适配器无法可靠还原标题。')
        items.append({'rank': rank, 'title': row['bookName'].strip(), 'author': str(row.get('author') or '').strip(),
                      'summary': str(row.get('abstract') or '').strip()[:600], 'word_count': str(row.get('wordNumber') or ''),
                      'read_count': str(row.get('read_count') or row.get('readCount') or ''), 'source_item_id': str(row.get('bookId') or '')})
    if not items:
        raise StoryError('MARKET_PARSE_FAILED', '公开榜单条目缺少可验证的排名或书名。')
    return sorted(items, key=lambda item: item['rank'])


def fetch_fanqie_rank(http_get=None):
    getter = http_get or _http_get
    return parse_fanqie_rank(getter(FANQIE_RANK_URL))


def _http_get(url):
    request = Request(url, headers={'User-Agent': 'HulkStoryResearch/0.1 (+local author workspace)'})
    with urlopen(request, timeout=15) as response:
        if response.status != 200:
            raise StoryError('MARKET_FETCH_FAILED', f'公开榜单返回 HTTP {response.status}。')
        return response.read().decode('utf-8', errors='replace')


def unavailable_snapshot(source_id):
    source = SOURCES[source_id]
    return {'source_id': source_id, 'label': source['label'], 'source_url': source['url'], 'status': source['status'],
            'collected_at': time.time(), 'items': [], 'signals': [], 'error': '该公开页面当前要求浏览器验证，尚未启用抓取适配器。'}
