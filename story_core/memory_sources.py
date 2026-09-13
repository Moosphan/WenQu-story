"""Resolve model-selected source IDs against the immutable task candidate."""
from copy import deepcopy
import re
from .errors import StoryError


def source_paragraphs(body):
    return [{'id': index + 1, 'text': text} for index, text in enumerate(
        part for part in re.split(r'\n\s*\n', body) if part.strip())]


def resolve_sources(result, candidate):
    if not isinstance(result, dict) or not isinstance(result.get('memories'), list):
        return result
    output = deepcopy(result)
    paragraphs = {item['id']: item['text'] for item in source_paragraphs((candidate or {}).get('body', ''))}
    for index, item in enumerate(output['memories']):
        if isinstance(item, dict) and 'evidence' not in item and 'evidence_paragraph' not in item:
            key = str(item.get('key', '未命名记忆'))[:100]
            raise StoryError('INVALID_EVIDENCE_SOURCE', f'第 {index + 1} 条记忆“{key}”缺少证据来源。',
                {'key': key, 'field': 'evidence_paragraph', 'index': index, 'phase': 'memory_validation',
                 'next_action': '从本章 source_paragraphs 选择有依据的段落编号；如果只是重复既有伏笔未回收，省略该条，不要编造证据。'})
        if not isinstance(item, dict) or 'evidence_paragraph' not in item:
            continue
        source = item['evidence_paragraph']
        if type(source) is not int or source not in paragraphs or 'evidence' in item:
            raise StoryError('INVALID_EVIDENCE_SOURCE', '证据段落编号无效或同时指定了两种证据来源；未写入记忆。')
        item.pop('evidence_paragraph')
        item['evidence'] = paragraphs[source]
    return output
