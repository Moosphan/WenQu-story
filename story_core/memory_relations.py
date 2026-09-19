"""One-hop expansion over explicit, verified entity references only."""
from . import long_memory as lm
from .errors import StoryError
from .storage import dumps


def related_evidence(conn, book_id, entity_ids, *, through_chapter, story_time,
                     role='author', pov_entity_id=None, neighbor_limit=16, evidence_limit=32):
    if (type(neighbor_limit) is not int or not 1 <= neighbor_limit <= 16
            or type(evidence_limit) is not int or not 1 <= evidence_limit <= 32):
        raise StoryError('INVALID_SCOPE', '关系扩展最多 16 个邻居和 32 条可选证据。')
    seeds = list(dict.fromkeys(lm.resolve_entity_id(conn, book_id, identity) for identity in entity_ids))[:16]
    options = dict(through_chapter=through_chapter, story_time=story_time, role=role, pov_entity_id=pov_entity_id)
    state = lm.current_state(conn, book_id, seeds, **options)
    neighbors, edges = [], []
    for fact in state:
        value = fact['value']
        if not isinstance(value, dict) or value.get('type') != 'entity':
            continue
        identity = lm.resolve_entity_id(conn, book_id, value.get('entity_id'))
        if identity in seeds:
            continue
        if identity not in neighbors:
            if len(neighbors) >= neighbor_limit:
                continue
            neighbors.append(identity)
        edges.append(fact)
    expanded = lm.current_state(conn, book_id, neighbors, **options)
    result, seen = [], set()
    for fact in [*edges, *expanded]:
        if fact['fact_id'] in seen:
            continue
        seen.add(fact['fact_id'])
        result.append({'fact_id': fact['fact_id'], 'kind': 'fact', 'subject_entity_id': fact['subject_entity_id'],
            'predicate': fact['predicate'], 'key': fact['subject_entity_id'] + ':' + fact['predicate'],
            'value': dumps(fact['value']), 'evidence': fact['evidence'], 'source': fact['source'],
            'state_semantics': 'verified_at_explicit_story_time', 'story_time': story_time,
            'context_reason': 'one_hop_relation'})
        if len(result) >= evidence_limit:
            break
    return result
