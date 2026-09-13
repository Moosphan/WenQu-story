"""Uncommitted extraction checkpoints, bound to an immutable task candidate."""
from copy import deepcopy
from jsonschema import Draft202012Validator
from .errors import StoryError
from .memory_sources import resolve_sources
from .schemas import MEMORY_SOURCE, obj, validate

REPAIR_SCHEMA = obj({'repairs': {'type': 'array', 'maxItems': 30, 'items': {
    'oneOf': [obj({'index': {'type': 'integer', 'minimum': 0}, 'memory': MEMORY_SOURCE}),
              obj({'index': {'type': 'integer', 'minimum': 0}, 'memory': {'type': 'null'},
                   'reason': {'type': 'string', 'minLength': 1, 'maxLength': 500}})]}}})


def checkpoint(result, candidate, book):
    if not isinstance(result, dict) or set(result) != {'memories'}:
        return None
    items = result['memories']
    if not isinstance(items, list) or not 0 < len(items) <= 30:
        return None
    retained, invalid = [], []
    for index, item in enumerate(items):
        try:
            resolved = resolve_sources({'memories': [item]}, candidate)
            validate('extract', resolved, book, candidate)
            retained.append({'index': index, 'memory': resolved['memories'][0]})
        except StoryError as error:
            invalid.append({'index': index, 'memory': deepcopy(item), 'code': error.code,
                            'message': error.message[:1000]})
    if not invalid:
        return None
    return {'retained': retained, 'retained_count': len(retained), 'invalid': invalid}


def merge(result, repair):
    if not repair:
        return result
    if not Draft202012Validator(REPAIR_SCHEMA).is_valid(result):
        raise StoryError('INVALID_EXTRACTION_REPAIR', '仅提交待修复条目的 repairs；每条提供证据段落，或说明为何舍弃。')
    expected = {item['index'] for item in repair['invalid']}
    indexes = [item['index'] for item in result['repairs']]
    if len(indexes) != len(set(indexes)) or set(indexes) != expected:
        raise StoryError('INVALID_EXTRACTION_REPAIR', '修复必须逐项对应待修复编号，不能遗漏、重复或修改已保留条目。')
    items = {item['index']: deepcopy(item['memory']) for item in repair['retained']}
    items.update({item['index']: deepcopy(item['memory']) for item in result['repairs'] if item['memory'] is not None})
    return {'memories': [items[index] for index in sorted(items)]}
