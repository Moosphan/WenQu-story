"""Apply exact, non-overlapping edits against the task's immutable source text."""
from jsonschema import Draft202012Validator
from .errors import StoryError


def materialize(result, candidate):
    if not isinstance(result, dict) or 'patches' not in result:
        return result
    from .schemas import PATCH_REVISION
    if list(Draft202012Validator(PATCH_REVISION).iter_errors(result)):
        raise StoryError('INVALID_PATCH', '定点修改格式无效，请返回 before/after 修改列表。')
    if not candidate or not isinstance(candidate.get('body'), str):
        raise StoryError('INVALID_PATCH', '定点返修缺少基准正文。')
    body = candidate['body']; spans = []
    for patch in result['patches']:
        before = patch['before']
        if body.count(before) != 1:
            raise StoryError('INVALID_PATCH', '待替换原文必须在基准正文中唯一出现；请复制足够上下文。')
        start = body.index(before); spans.append((start, start + len(before), patch['after']))
    spans.sort()
    if any(left[1] > right[0] for left, right in zip(spans, spans[1:])):
        raise StoryError('INVALID_PATCH', '修改范围发生重叠，未应用任何修改。')
    for start, end, after in reversed(spans):
        body = body[:start] + after + body[end:]
    output = {'title': result.get('title', candidate['title']), 'body': body}
    if 'revision_response' in result: output['revision_response'] = result['revision_response']
    return output
