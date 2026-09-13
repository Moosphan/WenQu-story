"""Strict object parsing with limited envelope cleanup and safe diagnostics."""
import json
import re
from .errors import StoryError


def parse_object(content, code='PROVIDER_RESULT_ERROR'):
    details = {'phase': 'decode_result', 'next_action': '只重试当前任务；正文仍保留。若持续失败，请更换支持 JSON 输出的模型。'}
    def fail(category, message, **extra):
        raise StoryError(code, message, {**details, 'category': category, **extra})
    if not isinstance(content, str) or not content.strip():
        fail('empty', '模型没有返回可解析的正文结果。')
    text = content.strip().lstrip('\ufeff').strip()
    fenced = re.fullmatch(r'```(?:json)?\s*\n([\s\S]*?)\n```', text, re.I)
    if fenced: text = fenced.group(1).strip()
    def pairs(entries):
        result = {}
        for key, value in entries:
            if key in result: fail('duplicate_key', '模型 JSON 存在重复字段，未猜测采用哪一个。')
            result[key] = value
        return result
    def invalid_constant(value):
        raise ValueError('nonfinite')
    try:
        result = json.loads(text, object_pairs_hook=pairs, parse_constant=invalid_constant)
    except json.JSONDecodeError as error:
        fail('invalid_json', '模型返回内容不是完整合法 JSON。', line=error.lineno, column=error.colno, content_chars=len(content))
    except ValueError:
        fail('invalid_json', '模型返回了 JSON 不支持的数值。')
    if not isinstance(result, dict): fail('not_object', '模型返回了非对象 JSON，任务要求对象结果。')
    return result
