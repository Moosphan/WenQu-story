"""Separate required task output from optional, independently validated annotations.

Never reinterpret a verdict or remove issues to make a task pass. Original output
is retained by the service; only validated annotations may affect transitions.
"""
from jsonschema import Draft202012Validator
from .schemas import SCHEMAS, validate
from .errors import StoryError
from .revision_response import validate_response, validate_verification

SUPPLEMENTS = {'continuity': 'revision_verification', 'revise': 'revision_response'}


def validate_output(stage, result, book, candidate, inputs):
    field = SUPPLEMENTS.get(stage)
    if not isinstance(result, dict) or not field:
        validate(stage, result, book, candidate)
        return result, None
    # Keep the original size cap, even when annotations are discarded.
    from .storage import dumps
    if len(dumps(result).encode()) > 100000:
        raise StoryError('INVALID_RESULT', '单次任务结果过大。')
    core = {key: value for key, value in result.items() if key != field}
    validate(stage, core, book, candidate)
    schema = SCHEMAS[stage]
    if stage == 'revise':
        schema = schema['oneOf'][0]  # Patches are materialized before this boundary.
    errors = list(Draft202012Validator(schema['properties'][field]).iter_errors(result[field])) if field in result else []
    try:
        if errors:
            raise StoryError('INVALID_REVISION_VERIFICATION' if stage == 'continuity' else 'INVALID_REVISION_RESPONSE',
                '附加返修说明不符合协议；核心结果已保留。',
                {'errors': [{'path': [field, *e.path], 'message': e.message} for e in errors[:8]]})
        if stage == 'continuity':
            validate_verification(result, inputs.get('revision_check'), candidate)
        else:
            validate_response(result, inputs.get('feedback_items', []))
    except StoryError as error:
        return core, {'field': field, 'code': error.code, 'message': error.message, 'details': error.details}
    return result, None
