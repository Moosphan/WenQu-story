"""Resolve declared source IDs inside the task transaction before compilation."""
import json

from .long_memory import current_state
from .storage import dumps

STAGES = {'draft', 'revise', 'continuity', 'reader', 'arc', 'ending'}


def resolve_dependencies(conn, book_id, plan, stage, chapter_number, *, role, pov_entity_id):
    requested = list(dict.fromkeys(plan.get('required_fact_ids', [])))
    promises = list(dict.fromkeys(plan.get('promise_ids', [])))
    result = {'evidence': [], 'state': []}
    if stage not in STAGES or not (requested or promises):
        return result
    boundary = chapter_number if stage in {'arc', 'ending'} else chapter_number - 1
    params = {'book': book_id, 'ids': dumps(requested), 'promises': dumps(promises), 'boundary': boundary,
              'role': role, 'pov': plan.get('pov') or ''}
    rows = conn.execute('''SELECT m.* FROM memories m JOIN chapters c
        ON c.book_id=m.book_id AND c.number=m.chapter_number AND c.version_id=m.version_id
        WHERE m.book_id=:book AND (m.id IN (SELECT value FROM json_each(:ids))
          OR (m.kind='promise' AND (m.id IN (SELECT value FROM json_each(:promises))
              OR m.key IN (SELECT value FROM json_each(:promises)))))
        AND c.status='committed' AND m.chapter_number<=:boundary
        AND (:role='author' OR m.visibility='reader')
        AND (:pov='' OR (m.visibility='reader' AND
             (m.kind!='knowledge' OR json_extract(m.data,'$.owner')=:pov)))
        ORDER BY m.chapter_number,m.id''', params).fetchall()
    for row in rows:
        item = json.loads(row['data'])
        item.pop('fact_id', None)
        item.pop('promise_id', None)
        item.update(id=row['id'], kind=row['kind'], key=row['key'], value=row['value'],
            visibility=row['visibility'], evidence=row['evidence'], context_reason='explicit_dependency',
            state_semantics='historical_evidence',
            source={'chapter_number': row['chapter_number'], 'version_id': row['version_id']})
        result['evidence'].append(item)
    if 'story_time' in plan:
        result['state'] = current_state(conn, book_id, [], fact_ids=requested, story_time=plan['story_time'],
            through_chapter=boundary, role=role, pov_entity_id=pov_entity_id)
    for item in result['state']:
        item['identity_aliases'] = [row[0] for row in conn.execute(
            'SELECT source_id FROM lm_fact_redirects WHERE book_id=? AND branch_id=? AND target_id=?',
            (book_id, 'main', item['fact_id']))]
        item['context_reason'] = 'explicit_dependency'
    return result


def attach_dependencies(data, resolved):
    def source_key(item):
        source = item.get('source') or {}
        return item.get('kind'), item.get('key'), source.get('version_id'), item.get('owner')

    keys = {source_key(item) for item in resolved['evidence']}
    if keys:
        for layer in ('required_memory', 'supplementary_memory', 'reader_memory', 'historical_evidence'):
            if layer in data:
                data[layer] = [item for item in data[layer] if source_key(item) not in keys]
        data.setdefault('required_memory', []).extend(resolved['evidence'])
    if resolved['state']:
        states = {item['fact_id']: item for item in data.get('current_state', [])}
        states.update({item['fact_id']: item for item in resolved['state']})
        data['current_state'] = list(states.values())
    sources = (data.get('context_manifest') or {}).get('canonical_sources')
    if sources is not None:
        for item in [*resolved['evidence'], *resolved['state']]:
            source = item['source']
            identity = item.get('fact_id') or item['id']
            if item.get('state_semantics') == 'historical_evidence':
                sources[:] = [entry for entry in sources if not (
                    entry.get('kind') == item['kind'] and entry.get('key') == item['key']
                    and entry.get('chapter_number') == source['chapter_number']
                    and entry.get('version_id') == source['version_id']
                    and entry.get('id') in (None, identity))]
            else:
                sources[:] = [entry for entry in sources if entry.get('id') != identity]
            sources.append({'id': identity, 'kind': item.get('kind', 'fact'),
                'key': item.get('key') or item.get('predicate'), 'chapter_number': source['chapter_number'],
                'version_id': source['version_id'], 'visibility': item['visibility'],
                'tier': 'required', 'reason': 'explicit_dependency'})
