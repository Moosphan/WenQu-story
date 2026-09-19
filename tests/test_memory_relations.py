from test_long_memory import world, chapter, fact
from story_core import long_memory as lm


def test_related_evidence_is_one_hop_and_scope_checked(world):
    from story_core.memory_relations import related_evidence
    store, book = world
    with store.write(book) as conn:
        a, b, c = [lm.register_entity(conn, book, name) for name in ('甲', '乙', '丙')]
        version = chapter(conn, book, 1)
        fact(conn, book, a, version, 1, predicate='knows', value={'type': 'entity', 'entity_id': b})
        fact(conn, book, b, version, 1, predicate='knows', value={'type': 'entity', 'entity_id': c})
        fact(conn, book, b, version, 1, predicate='secret', value='隐藏', visibility='author')
        fact(conn, book, c, version, 1, predicate='third', value='不可递归带入')
        results = related_evidence(conn, book, [a], through_chapter=1, story_time=1, role='reader')
        assert any(item['subject_entity_id'] == b for item in results)
        assert all(item['subject_entity_id'] != c for item in results)
        assert all(item['predicate'] != 'secret' for item in results)
        chapter(conn, book, 1, '重写后的正文。')
        assert related_evidence(conn, book, [a], through_chapter=1, story_time=1, role='reader') == []
