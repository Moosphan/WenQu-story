"""Current-candidate citations for continuity reviews; old host contracts still work."""
from copy import deepcopy
from .schemas import SCHEMAS
from .memory_sources import source_paragraphs
from .errors import StoryError


def output_schema(candidate):
    schema=deepcopy(SCHEMAS['continuity'])
    item=schema['properties']['issues']['items']
    item['properties'].pop('evidence')
    item['properties']['evidence_paragraph']={'type':'integer','enum':[p['id'] for p in source_paragraphs(candidate['body'])]}
    item['required']=[k for k in item['required'] if k!='evidence']+['evidence_paragraph']
    return schema


def resolve(result,candidate):
    if not isinstance(result,dict) or not isinstance(result.get('issues'),list):return result
    result=deepcopy(result)
    sources={p['id']:p['text'] for p in source_paragraphs(candidate['body'])}
    for index,item in enumerate(result['issues']):
        if not isinstance(item,dict) or 'evidence_paragraph' not in item:continue
        source=item['evidence_paragraph']
        if type(source) is not int or source not in sources or 'evidence' in item:
            raise StoryError('INVALID_REVIEW_SOURCE','审稿证据段落无效，未触发正文返修。',{'index':index,'phase':'review_source_validation'})
        item.pop('evidence_paragraph');item['evidence']=sources[source]
    return result
