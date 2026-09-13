import pytest
from story_core.errors import StoryError


def test_response_parser_handles_transport_wrappers_without_repairing_values():
    from story_core.model_json import parse_object
    for text in ['{"ok":true}', '\ufeff {"ok":true}', '```json\n{"ok":true}\n```']:
        assert parse_object(text)=={'ok':True}


@pytest.mark.parametrize('text,category', [('', 'empty'),(' {"x": ', 'invalid_json'),('[1]', 'not_object'),('Here is {"x":1}', 'invalid_json'),('{"x":1,"x":2}', 'duplicate_key')])
def test_invalid_json_has_safe_specific_diagnosis(text,category):
    from story_core.model_json import parse_object
    with pytest.raises(StoryError) as error: parse_object(text)
    assert error.value.details['category']==category
    assert error.value.details['phase']=='decode_result'
    assert error.value.details['next_action']
