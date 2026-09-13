"""Versioned task contracts shared by host agents and API workers."""
import json
import math
from copy import deepcopy
import re

from jsonschema import Draft202012Validator

from .errors import StoryError
from .memory import validate_memories


def obj(properties, required=None):
    return {"type": "object", "properties": properties, "required": required or list(properties), "additionalProperties": False}


S = {"type": "string", "minLength": 1}
TEXT = {"type": "string"}
CHARACTER = obj({k: S for k in ("name", "desire", "fear", "boundary", "voice")})
CHAPTER = obj({"number": {"type": "integer", "minimum": 1}, **{k:S for k in ("title", "goal", "conflict", "change", "payoff", "emotion")},
               "participants": {"type": "array", "items": S, "maxItems": 12, "uniqueItems": True}, "pov": S},
              ["number", "title", "goal", "conflict", "change", "payoff", "emotion"])
PROMISE = obj({"key": S, "setup_chapter": {"type": "integer", "minimum": 1}, "due_chapter": {"type": "integer", "minimum": 1}, "resolution": S, "mandatory": {"type": "boolean"}})
MEMORY = obj({"kind": {"enum": ["fact", "entity", "relationship", "knowledge", "promise", "emotion", "timeline", "summary"]},
              "key": S, "value": S, "evidence": S, "visibility": {"enum": ["author", "reader"]},
              "entity_type": {"enum": ["person", "item", "place", "organization", "creature", "other"]},
              "aliases": {"type": "array", "items": S, "minItems": 1, "maxItems": 12, "uniqueItems": True},
              "owner": TEXT, "status": {"enum": ["open", "paid", "waived"]}, "mandatory": {"type": "boolean"},
              "due_chapter": {"type": "integer", "minimum": 1}}, ["kind", "key", "value", "evidence", "visibility"])
# Entity-only metadata must be constrained in the advertised contract as well as
# the persistence validator, otherwise a schema-valid response fails after generation.
MEMORY['allOf'] = [{
    'if': {'properties': {'kind': {'const': 'entity'}}, 'required': ['kind']},
    'else': {'not': {'anyOf': [{'required': ['entity_type']}, {'required': ['aliases']}]}}
}]
MEMORY['allOf'].extend([
    {'if': {'properties': {'kind': {'const': 'knowledge'}}, 'required': ['kind']},
     'then': {'required': ['owner'], 'properties': {'owner': S}}},
    {'if': {'properties': {'kind': {'const': 'promise'}}, 'required': ['kind']},
     'then': {'required': ['status']}}
])
MEMORY['properties']['entity_type']['description'] = '仅 kind=entity 时允许；其他记忆类型必须省略本字段。'
MEMORY['properties']['aliases']['description'] = '仅 kind=entity 时允许；其他记忆类型必须省略本字段。'
MEMORY_SOURCE = deepcopy(MEMORY)
MEMORY_SOURCE['properties'].pop('evidence')
MEMORY_SOURCE['properties']['evidence_paragraph'] = {'type': 'integer', 'minimum': 1, 'description': '从 source_paragraphs 选择能支持 value 的段落编号，不自行抄写原文。'}
MEMORY_SOURCE['required'] = [key for key in MEMORY_SOURCE['required'] if key != 'evidence'] + ['evidence_paragraph']
ISSUE = obj({"severity": {"enum": ["blocker", "major", "medium", "minor"]}, **{k:S for k in ("dimension", "evidence", "explanation", "suggestion")}})
REVIEW = obj({"verdict": {"enum": ["pass", "revise"]}, "issues": {"type": "array", "items": ISSUE, "maxItems": 30}, "notes": TEXT})
SCHEMAS = {
    "brief": obj({**{k:S for k in ("title", "premise", "audience", "pov", "style", "ending")}, "characters": {"type": "array", "items": CHARACTER, "minItems": 1, "maxItems": 20}}),
    "outline": obj({"chapters": {"type": "array", "items": CHAPTER, "minItems": 1}, "promises": {"type": "array", "items": PROMISE}}),
    "draft": obj({"title": S, "body": S}), "revise": obj({"title": S, "body": S, "revision_response": {"type": "array", "maxItems": 150, "items": obj({"feedback_id": S, "status": {"enum": ["changed", "not_changed"]}, "explanation": S, "evidence": TEXT})}}, ["title", "body"]),
    "extract": obj({"memories": {"type": "array", "items": {"oneOf": [MEMORY, MEMORY_SOURCE]}, "maxItems": 30}}),
    "continuity": obj({**REVIEW['properties'], 'repair_target': {'enum': ['manuscript', 'memory']}, 'revision_verification': {'type': 'array', 'maxItems': 150, 'items': obj({'feedback_id': S, 'status': {'enum': ['verified', 'unresolved', 'uncertain']}, 'evidence': TEXT, 'explanation': S})}}, REVIEW['required']), "reader": REVIEW, "arc": REVIEW, "ending": REVIEW,
}

PATCH_REVISION = obj({'title': S, 'patches': {'type': 'array', 'minItems': 1, 'maxItems': 20,
    'items': obj({'before': S, 'after': TEXT})}, 'revision_response': SCHEMAS['revise']['properties']['revision_response']}, ['patches'])
SCHEMAS['revise'] = {'oneOf': [SCHEMAS['revise'], PATCH_REVISION]}


def word_count(text):
    """Chinese/Han characters plus Latin/number word groups; punctuation excluded."""
    return len(re.findall(r"[\u3400-\u9fff]|[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*", text))


def length_requirement(target):
    """Shared authoring/validation bounds; aim at target, not the lower bound."""
    return {'target': target, 'min': math.ceil(target * .9), 'max': math.floor(target * 1.6),
            'counting': '汉字及英文/数字词组，标点和空白不计'}


def validate(stage, result, book, candidate=None):
    if not isinstance(result, dict):
        raise StoryError("INVALID_RESULT", "结果必须是 JSON 对象。")
    errors = sorted(Draft202012Validator(SCHEMAS[stage]).iter_errors(result), key=lambda e: str(e.path))
    if errors:
        raise StoryError("INVALID_RESULT", "任务结果不符合协议。", {"errors": [{"path": list(e.path), "message": e.message} for e in errors[:8]]})
    if len(json.dumps(result, ensure_ascii=False).encode()) > 100000:
        raise StoryError("INVALID_RESULT", "单次任务结果过大。")
    if stage == "outline":
        total = book["settings"]["chapter_count"]
        if [c["number"] for c in result["chapters"]] != list(range(1, total + 1)):
            raise StoryError("INVALID_RESULT", "章节规划必须覆盖整本书，编号连续。")
        for promise in result["promises"]:
            if not promise["setup_chapter"] <= promise["due_chapter"] <= total:
                raise StoryError("INVALID_RESULT", "伏笔回收窗口超出全书范围。")
        if len({p['key'] for p in result['promises']}) != len(result['promises']):
            raise StoryError("INVALID_RESULT", "伏笔 key 必须唯一。")
    if stage in ("draft", "revise"):
        count = word_count(result["body"])
        target = book["settings"]["target_words"]
        bounds = length_requirement(target)
        if not bounds['min'] <= count <= bounds['max']:
            action = '补足' if count < bounds['min'] else '压缩'
            raise StoryError("WORD_COUNT", f"正文{count}字，目标{target}字，允许{bounds['min']}–{bounds['max']}字，需要{action}正文。",
                {**bounds, 'count': count, 'phase': 'manuscript_length',
                 'next_action': f'按目标{target}字{action}本章场景与行动过程，保留已成立的情节和人物。重试会带入字数反馈；不能通过修复 JSON 或重放原结果解决。'})
        if re.search(r"<\/?(?:think|analysis)>|作为(?:一个)?(?:AI|人工智能)|```|^\s*#{1,6}\s", result["body"], re.M | re.I):
            raise StoryError("MANUSCRIPT_ARTIFACT", "正文包含推理标记、代码围栏或提示语，请只提交小说正文。")
    if stage == "extract":
        validate_memories(candidate["body"], result["memories"])
        for item in result["memories"]:
            if item["kind"] == "knowledge" and not item.get("owner"):
                raise StoryError("INVALID_RESULT", "角色知识需要 owner，防止混淆谁知道什么。")
            if item["kind"] == "promise" and not item.get("status"):
                raise StoryError("INVALID_RESULT", "伏笔记忆必须包含 open/paid/waived 状态。")
    return result
