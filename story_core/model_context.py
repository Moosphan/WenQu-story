"""Keep the UI's duplicate provenance index out of model requests.

Actual memory evidence, paragraph identifiers and all narrative instructions remain.
The full task input is still persisted for context inspection and host tooling.
"""


def model_input(data):
    def compact(value):
        if isinstance(value, list):
            return [compact(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {key: compact(item) for key, item in value.items()}
        source = result.get('source')
        if isinstance(result.get('evidence'), str) and isinstance(source, dict) and source.get('quote') == result['evidence']:
            source.pop('quote')
        return result
    result = compact({key: value for key, value in data.items() if key not in ('context_manifest', 'context_diagnostics', 'context_selection')})
    for field in ('required_memory', 'supplementary_memory'):
        memories = result.get(field)
        if isinstance(memories, list):
            for memory in memories:
                if isinstance(memory, dict):
                    memory.pop('context_reason', None)
    return result
