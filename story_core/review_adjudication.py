"""One independent adjudication before spending more revisions on disputed gates."""
from copy import deepcopy
from jsonschema import Draft202012Validator
from .schemas import obj, S
from .errors import StoryError
from .memory_sources import source_paragraphs

SCHEMA = obj({'decisions': {'type': 'array', 'maxItems': 30, 'items': obj({
    'index': {'type': 'integer', 'minimum': 0},
    'decision': {'enum': ['confirmed_conflict', 'editorial', 'unsupported', 'uncertain']},
    'evidence_paragraph': {'type': 'integer', 'minimum': 1}, 'reason': S})}})
INSTRUCTION = '''你是独立审稿裁决者，不是原审稿人，也不是改稿者。只裁决 review_adjudication 中的 blocker/major，逐项输出 decisions，不新增问题、不改正文。
不因达到返修上限而放宽标准，也不默认原审稿正确。confirmed_conflict 必须解释两条不能同时成立的事实/明确规则及其来源；核算数量时写出互斥分组和算式。未提前介绍一个合理细节不构成矛盾。
editorial 表示表达、重复、节奏等改进建议，正文事实成立；unsupported 表示原审稿的指控经核对不成立；uncertain 表示提供的上下文不足以裁决，保持阻断。
每条 evidence_paragraph 选择 source_paragraphs 中支持裁决的段落编号，不抄写引文，reason 明确说明与既有事实/规则的对照依据；不能仅重复结论。index 必须使用 adjudication_items 中已给出的编号，不自行重新编号；只处理该列表。不得遗漏或重复。只输出 output_schema JSON。'''


def materialize(result, review, candidate):
    if not Draft202012Validator(SCHEMA).is_valid(result):
        raise StoryError('INVALID_ADJUDICATION', '裁决格式无效；未解除审稿阻断。')
    expected = {i for i, issue in enumerate(review['issues']) if issue['severity'] in ('major','blocker')}
    indexes = [d['index'] for d in result['decisions']]
    if len(indexes) != len(set(indexes)) or set(indexes) != expected:
        raise StoryError('INVALID_ADJUDICATION', '裁决必须逐项覆盖原阻断问题。')
    output = {k: deepcopy(v) for k,v in review.items() if k in ('issues','verdict','notes','repair_target')}
    notes = []
    for decision in result['decisions']:
        if decision['evidence_paragraph'] not in {p['id'] for p in source_paragraphs(candidate['body'])}:
            raise StoryError('INVALID_ADJUDICATION', '裁决引用不在当前候选正文中。')
        label = {'confirmed_conflict':'保留冲突','editorial':'表达建议','unsupported':'指控不成立','uncertain':'证据不足，待确认'}[decision['decision']]
        issue = output['issues'][decision['index']]
        if decision['decision'] in ('editorial','unsupported'):
            issue['severity'] = 'minor'
        issue['explanation'] = f"裁决：{label}。{decision['reason']}\n原意见：{issue['explanation']}"
        notes.append(f"问题 {decision['index']+1}：{label}，{decision['reason']}")
    output['verdict'] = 'revise' if any(i['severity'] != 'minor' for i in output['issues']) else 'pass'
    output['notes'] = '独立裁决（未修改正文）：\n' + '\n'.join(notes)
    return output


def output_schema(review):
    schema = deepcopy(SCHEMA)
    indexes = [i for i, issue in enumerate(review['issues']) if issue['severity'] in ('major','blocker')]
    array = schema['properties']['decisions']
    array['minItems'] = array['maxItems'] = len(indexes)
    array['items']['properties']['index'] = {'enum': indexes}
    return schema
