"""Synthetic-only regression coverage for the opt-in normalized memory store."""
import json

import pytest

from story_core.storage import Store, uid
from story_core.errors import StoryError


@pytest.fixture
def world(tmp_path):
    store = Store(tmp_path)
    book = store.create_book('synthetic', 'synthetic', {})['book_id']
    return store, book


def chapter(conn, book, number, body='持有十枚铜钱，后来只剩三枚。伤口愈合。他以为父亲已死，其实尚在人世。'):
    version = uid('v')
    conn.execute('INSERT INTO chapter_versions VALUES (?,?,?,?,?,?)', (version, book, number, 'test', body, 0))
    conn.execute("INSERT OR REPLACE INTO chapters VALUES (?,?,?,'committed')", (book, number, version))
    return version


def fact(conn, book, entity, version, number, predicate='coins', value=10, **kwargs):
    from story_core.long_memory import record_fact
    options = dict(verified=True, story_valid_from=number, evidence='铜钱')
    options.update(kwargs)
    return record_fact(conn, book, entity, predicate, value, source_chapter=number,
                       source_version=version, **options)


def test_schema_migration_is_additive_and_reopens_v7(world):
    store, book = world
    with store.write() as conn:
        conn.execute('PRAGMA user_version=7')
        chapter(conn, book, 1)
    reopened = Store(store.root)
    with reopened.read() as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 8
        assert conn.execute('SELECT count(*) FROM chapters').fetchone()[0] == 1
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='lm_facts'").fetchone()


def test_aliases_keep_identity_and_report_ambiguity(world):
    from story_core.long_memory import register_entity, rename_entity, register_alias, resolve_alias
    store, book = world
    other = store.create_book('other', 'other', {})['book_id']
    with store.write() as conn:
        one = register_entity(conn, book, '沈知秋')
        rename_entity(conn, book, one, '沈无名')
        assert resolve_alias(conn, book, '沈知秋') == one
        assert resolve_alias(conn, book, '沈无名') == one
        two = register_entity(conn, book, '另一个沈知秋')
        register_alias(conn, book, two, '沈知秋')
        with pytest.raises(StoryError) as caught:
            resolve_alias(conn, book, '沈知秋')
        assert caught.value.code == 'AMBIGUOUS_ENTITY'
        assert resolve_alias(conn, other, '沈无名') is None
        assert resolve_alias(conn, book, '沈无名', branch_id='alternate') is None


def test_projection_uses_verified_latest_story_time_not_chapter_order(world):
    from story_core.long_memory import register_entity, current_state
    store, book = world
    with store.write() as conn:
        hero = register_entity(conn, book, '主角')
        v1, v2, v3 = [chapter(conn, book, n) for n in (1, 2, 3)]
        first = fact(conn, book, hero, v1, 1, story_valid_from=10)
        latest = fact(conn, book, hero, v2, 2, value=3, story_valid_from=20)
        flashback = fact(conn, book, hero, v3, 3, value=99, story_valid_from=5)
        assert len({first, latest, flashback}) == 1  # stable identity, immutable source events
        fact(conn, book, hero, v3, 3, value=200, verified=False, story_valid_from=30)
        fact(conn, book, hero, v2, 2, predicate='injury', value='healed', evidence='伤口愈合', story_valid_from=20)
        current = current_state(conn, book, [hero], through_chapter=3, story_time=30)
        assert {f['predicate']: f['value'] for f in current} == {'coins': 3, 'injury': 'healed'}
        assert current_state(conn, book, [hero], through_chapter=3, story_time=15)[0]['value'] == 10
        assert conn.execute('SELECT count(*) FROM lm_fact_events').fetchone()[0] == 5


def test_belief_plan_and_revelation_do_not_leak(world):
    from story_core.long_memory import register_entity, current_state
    store, book = world
    with store.write() as conn:
        hero, sister = [register_entity(conn, book, n) for n in ('主角', '妹妹')]
        v = chapter(conn, book, 1)
        fact(conn, book, hero, v, 1, predicate='father', value='alive', revealed_from_chapter=5)
        fact(conn, book, hero, v, 1, predicate='father', value='dead', scope='character_belief', owner_entity_id=hero)
        fact(conn, book, hero, v, 1, predicate='father', value='secret', scope='character_belief', owner_entity_id=sister)
        fact(conn, book, hero, v, 1, predicate='father', value='will return', scope='author_plan')
        reader = current_state(conn, book, [hero], role='reader', through_chapter=1, story_time=1, pov_entity_id=hero)
        assert [x['value'] for x in reader] == ['dead']
        author = current_state(conn, book, [hero], through_chapter=1, story_time=1)
        assert [x['value'] for x in author] == ['alive']
        with pytest.raises(StoryError):
            current_state(conn, book, [hero], role='reader', story_time=1)


def test_replaced_version_invalidates_transitive_dependent_state(world):
    from story_core.long_memory import register_entity, current_state, invalidate_version
    store, book = world
    with store.write() as conn:
        hero = register_entity(conn, book, '主角')
        v1, v2, v3 = [chapter(conn, book, n) for n in (1, 2, 3)]
        fact(conn, book, hero, v1, 1)
        fact(conn, book, hero, v2, 2, value=3, dependency_versions=[v1])
        fact(conn, book, hero, v3, 3, predicate='injury', value='healed', dependency_versions=[v2])
        chapter(conn, book, 1)
        assert current_state(conn, book, [hero], through_chapter=3, story_time=3) == []
        assert invalidate_version(conn, book, v1) == 3
        assert conn.execute('SELECT count(*) FROM lm_fact_events').fetchone()[0] == 3


def test_projection_rejects_cross_book_entities_and_filters_uncommitted(world):
    from story_core.long_memory import register_entity, current_state
    store, book = world
    other = store.create_book('other', 'other', {})['book_id']
    with store.write() as conn:
        hero = register_entity(conn, book, '主角')
        foreign = register_entity(conn, other, '主角')
        v = chapter(conn, book, 1)
        fact(conn, book, hero, v, 1)
        with pytest.raises(StoryError):
            fact(conn, book, foreign, v, 1)
        conn.execute("UPDATE chapters SET status='needs_review' WHERE book_id=?", (book,))
        assert current_state(conn, book, [hero, foreign], through_chapter=1, story_time=1) == []


def test_legacy_migration_is_conservative_idempotent_and_preserves_sources(world):
    from story_core.long_memory import migrate_legacy, current_state
    from story_core.memory import index_chapter
    store, book = world
    with store.write() as conn:
        body = '沈知秋持有铜钱。'
        v = chapter(conn, book, 1, body)
        index_chapter(conn, book, 1, v, body, [
            dict(kind='entity', key='沈知秋', value='主人公', evidence='沈知秋'),
            dict(kind='fact', key='沈知秋铜钱', value='很多', evidence='持有铜钱')])
        before = [dict(r) for r in conn.execute('SELECT * FROM memories')]
        report = migrate_legacy(conn, book)
        assert report['entities_created'] == 1 and report['unresolved'] == 1
        assert migrate_legacy(conn, book)['entities_created'] == 0
        assert [dict(r) for r in conn.execute('SELECT * FROM memories')] == before
        entity = conn.execute('SELECT id FROM lm_entities').fetchone()[0]
        assert current_state(conn, book, [entity], through_chapter=1, story_time=1) == []


def test_promise_scheduler_preserves_all_due_and_queues_undated(world):
    from story_core.long_memory import schedule_promise, select_promises, set_promise_status
    store, book = world
    with store.write() as conn:
        distant = schedule_promise(conn, book, '主角还债', due_chapter=350)
        triggered = schedule_promise(conn, book, '铜牌归属', triggers=['return_city'])
        undated = schedule_promise(conn, book, '父亲下落')
        hard = [schedule_promise(conn, book, f'义务{i}', due_chapter=3, mandatory=True) for i in range(60)]
        early = select_promises(conn, book, 3)
        assert {x['promise_id'] for x in early['required']} == set(hard)
        assert undated in early['periodic_check_ids']
        assert distant not in {x['promise_id'] for x in early['required']}
        selected = select_promises(conn, book, 350, trigger_keys=['return_city'])
        assert {distant, triggered} <= {x['promise_id'] for x in selected['required']}
        set_promise_status(conn, book, distant, 'resolved')
        assert distant not in {x['promise_id'] for x in select_promises(conn, book, 351)['required']}


def test_opt_in_layers_do_not_require_remote_name_matches():
    from story_core.memory import bounded_context_layers
    memories = [dict(kind='promise', key='主角旧债', value='遥远', status='open', due_chapter=350),
                dict(kind='fact', key='主角经历', value='旧事')]
    layers = bounded_context_layers(memories, {'participants': ['主角']}, 3)
    assert layers['required'] == []
    assert len(layers['supplementary']) <= 8


def test_author_task_with_limited_pov_cannot_read_unrevealed_truth(world):
    from story_core.long_memory import register_entity, current_state
    store, book = world
    with store.write() as conn:
        hero = register_entity(conn, book, '主角')
        v = chapter(conn, book, 1)
        fact(conn, book, hero, v, 1, value='future reveal', revealed_from_chapter=5)
        fact(conn, book, hero, v, 1, predicate='secret', value='author only', visibility='author')
        assert current_state(conn, book, [hero], through_chapter=1, story_time=1, pov_entity_id=hero) == []


def test_fact_provenance_and_owner_are_validated_before_write(world):
    from story_core.long_memory import register_entity, current_state
    store, book = world
    other = store.create_book('other', 'other', {})['book_id']
    with store.write() as conn:
        hero = register_entity(conn, book, '主角')
        foreign = register_entity(conn, other, '妹妹')
        v = chapter(conn, book, 1)
        foreign_v = chapter(conn, other, 1)
        for options in (dict(evidence='不存在的证据'), dict(dependency_versions=[foreign_v]),
                        dict(scope='character_belief', owner_entity_id=foreign), dict(story_valid_to=0)):
            with pytest.raises(StoryError):
                fact(conn, book, hero, v, 1, **options)
        assert conn.execute('SELECT count(*) FROM lm_fact_events').fetchone()[0] == 0
        fact(conn, book, hero, v, 1, known_from_chapter=5)
        assert current_state(conn, book, [hero], through_chapter=1, story_time=1) == []


def test_explicit_missing_promise_is_reported_and_distant_reference_selected(world):
    from story_core.long_memory import schedule_promise, select_promises
    store, book = world
    with store.write() as conn:
        distant = schedule_promise(conn, book, '远期伏笔', due_chapter=500)
        result = select_promises(conn, book, 1, explicit_ids=[distant, 'missing'])
        assert [x['promise_id'] for x in result['required']] == [distant]
        assert result['missing_explicit_ids'] == ['missing']


def test_promise_source_replacement_excludes_stale_obligations(world):
    from story_core.long_memory import schedule_promise, select_promises
    store, book = world
    with store.write() as conn:
        old = chapter(conn, book, 1)
        promise = schedule_promise(conn, book, '旧版本义务', due_chapter=2, source_chapter=1, source_version=old)
        assert select_promises(conn, book, 2)['required'][0]['promise_id'] == promise
        chapter(conn, book, 1)
        assert select_promises(conn, book, 2)['required'] == []


def test_explicit_fact_references_report_missing_ids():
    from story_core.memory import bounded_context_layers
    result = bounded_context_layers([], {'required_memory_ids': ['missing']}, 1)
    assert result['missing_required_ids'] == ['missing']


def test_legacy_ambiguous_alias_remains_legacy_and_private_claim_stays_unverified(world):
    from story_core.long_memory import migrate_legacy, current_state
    from story_core.memory import index_chapter
    store, book = world
    with store.write() as conn:
        for number, key in [(1, '沈知秋'), (5, '沈无名')]:
            body = f'{key}就是沈老板。他持有铜钱。'
            v = chapter(conn, book, number, body)
            index_chapter(conn, book, number, v, body, [
                dict(kind='entity', key=key, value='沈老板', aliases=['沈老板'], evidence=key),
                dict(kind='fact', key='秘密', value='unknown inference', evidence='持有铜钱', visibility='author')])
        report = migrate_legacy(conn, book)
        assert report['entities_created'] == 1
        assert report['unresolved'] == 3
        assert report['ambiguous_records'] == 1
        mapping = conn.execute("SELECT * FROM lm_migrations WHERE reason='ambiguous_alias_requires_confirmation'").fetchone()
        assert mapping['entity_id'] is None
        assert conn.execute('SELECT count(*) FROM memories').fetchone()[0] == 4
        ids = [row[0] for row in conn.execute('SELECT id FROM lm_entities')]
        assert current_state(conn, book, ids, through_chapter=1, story_time=1, role='reader') == []
        assert migrate_legacy(conn, book)['already_processed'] == 4


def test_promise_author_limited_pov_applies_visibility_guard(world):
    from story_core.long_memory import register_entity, schedule_promise, select_promises
    store, book = world
    with store.write() as conn:
        hero = register_entity(conn, book, '主角')
        secret = schedule_promise(conn, book, '作者秘密', due_chapter=2, visibility='author')
        public = schedule_promise(conn, book, '已揭晓义务', due_chapter=2, visibility='reader')
        result = select_promises(conn, book, 2, role='author', pov_entity_id=hero, explicit_ids=[secret])
        assert [x['promise_id'] for x in result['required']] == [public]
        assert result['missing_explicit_ids'] == [secret]


def test_unrestricted_author_keeps_every_owner_scoped_due_obligation(world):
    from story_core.long_memory import register_entity, schedule_promise, select_promises
    store, book = world
    with store.write() as conn:
        hero, sister = [register_entity(conn, book, name) for name in ('主角', '妹妹')]
        required = {schedule_promise(conn, book, name, owner_entity_id=owner, due_chapter=2,
                                     visibility='reader', mandatory=True)
                    for owner, name in [(hero, '还债'), (sister, '归还铜牌')]}
        result = select_promises(conn, book, 2)
        assert {x['promise_id'] for x in result['required']} == required
        limited = select_promises(conn, book, 2, pov_entity_id=hero)
        assert [x['label'] for x in limited['required']] == ['还债']


def test_legacy_migration_handles_valid_null_optional_entity_metadata(world):
    from story_core.long_memory import migrate_legacy
    from story_core.memory import index_chapter
    store, book = world
    with store.write() as conn:
        body = '沈知秋持有铜钱。'
        v = chapter(conn, book, 1, body)
        from story_core.memory import validate_memories
        item = dict(kind='entity', key='沈知秋', value='主人公', evidence='沈知秋', aliases=None, entity_type=None)
        validate_memories(body, [item])
        # Seed an already accepted legacy record independently of the new index.
        conn.execute('INSERT INTO memories VALUES (?,?,?,?,?,?,?,?,?,?)',
                     (uid('mem'), book, 1, v, 'entity', '沈知秋', '主人公', '沈知秋', 'reader', json.dumps(item)))
        assert migrate_legacy(conn, book)['entities_created'] == 1
        assert conn.execute('SELECT entity_type FROM lm_entities').fetchone()[0] == 'other'
        assert migrate_legacy(conn, book)['already_processed'] == 1


def test_bounded_layers_use_standard_hard_reference_and_constraint_fields():
    from story_core.memory import bounded_context_layers
    facts = [dict(id='standard', kind='fact', value='important'), dict(id='legacy', kind='fact', value='old')]
    result = bounded_context_layers(facts, {'required_fact_ids': ['standard', 'missing'],
                                           'required_memory_ids': ['legacy']}, 2,
                                    scheduled_promises=[{'promise_id': 'triggered', 'context_reason': 'triggered'}])
    assert result['missing_required_ids'] == ['missing']
    selected = {x.get('id'): x for x in result['required'] if x.get('id')}
    assert set(selected) == {'standard', 'legacy'}
    assert all(x['context_reason'] == 'explicit_dependency' for x in selected.values())
    assert result['required'][0]['hard_constraint'] is True


def test_bounded_due_promises_use_compiler_hard_reason():
    from story_core.memory import bounded_context_layers
    result = bounded_context_layers([dict(kind='promise', id='due', due_chapter=2, status='open')], {}, 2)
    assert result['required'][0]['context_reason'] == 'due_promise'


@pytest.mark.parametrize('terminal_status', ['resolved', 'cancelled'])
def test_replacing_fulfillment_version_revives_original_obligation(world, terminal_status):
    from story_core.long_memory import schedule_promise, set_promise_status, select_promises
    store, book = world
    with store.write() as conn:
        opened = chapter(conn, book, 1)
        fulfilled = chapter(conn, book, 2)
        promise = schedule_promise(conn, book, '归还铜牌', due_chapter=2, mandatory=True,
                                   source_chapter=1, source_version=opened)
        set_promise_status(conn, book, promise, terminal_status, source_chapter=2, source_version=fulfilled)
        assert select_promises(conn, book, 3)['required'] == []
        assert select_promises(conn, book, 2)['required'][0]['promise_id'] == promise
        chapter(conn, book, 2)
        assert select_promises(conn, book, 3)['required'][0]['promise_id'] == promise
        history = conn.execute('SELECT * FROM lm_promise_status_events WHERE promise_id=?', (promise,)).fetchall()
        assert len(history) == 1 and history[0]['source_version'] == fulfilled


def test_promise_status_events_reject_foreign_source_and_keep_admin_decision(world):
    from story_core.long_memory import schedule_promise, set_promise_status, select_promises
    store, book = world
    other = store.create_book('other', 'other', {})['book_id']
    with store.write() as conn:
        foreign = chapter(conn, other, 1)
        promise = schedule_promise(conn, book, '归还铜牌', due_chapter=1)
        with pytest.raises(StoryError):
            set_promise_status(conn, book, promise, 'resolved', source_chapter=1, source_version=foreign)
        assert select_promises(conn, book, 2)['required'][0]['promise_id'] == promise
        set_promise_status(conn, book, promise, 'cancelled')
        assert select_promises(conn, book, 2)['required'] == []
        assert conn.execute('SELECT count(*) FROM lm_promise_status_events').fetchone()[0] == 1


def test_promise_fulfillment_dependency_invalidation_revives_obligation(world):
    from story_core.long_memory import schedule_promise, set_promise_status, select_promises
    store, book = world
    with store.write() as conn:
        evidence = chapter(conn, book, 1)
        fulfillment = chapter(conn, book, 2)
        promise = schedule_promise(conn, book, '契约', due_chapter=2)
        set_promise_status(conn, book, promise, 'resolved', source_chapter=2,
                           source_version=fulfillment, dependency_versions=[evidence])
        assert select_promises(conn, book, 3)['required'] == []
        chapter(conn, book, 1)
        assert select_promises(conn, book, 3)['required'][0]['promise_id'] == promise


def test_legacy_alias_collision_normalizes_whitespace(world):
    from story_core.long_memory import register_entity, migrate_legacy
    from story_core.memory import index_chapter
    store, book = world
    with store.write() as conn:
        register_entity(conn, book, '沈老板')
        body = '沈知秋持有铜钱。'
        v = chapter(conn, book, 1, body)
        index_chapter(conn, book, 1, v, body, [dict(kind='entity', key='沈知秋', value='老板',
                                                  evidence='沈知秋', aliases=[' 沈老板 '])])
        assert migrate_legacy(conn, book)['ambiguous_records'] == 1
        assert conn.execute('SELECT count(*) FROM lm_entities').fetchone()[0] == 1


def test_backfilled_earlier_status_cannot_override_later_chapter_resolution(world):
    from story_core.long_memory import schedule_promise, set_promise_status, select_promises
    store, book = world
    with store.write() as conn:
        v2, v3 = [chapter(conn, book, number) for number in (2, 3)]
        promise = schedule_promise(conn, book, '旧债', due_chapter=2)
        set_promise_status(conn, book, promise, 'resolved', source_chapter=3, source_version=v3)
        set_promise_status(conn, book, promise, 'active', source_chapter=2, source_version=v2)
        assert select_promises(conn, book, 4)['required'] == []
        assert select_promises(conn, book, 3)['required'][0]['promise_id'] == promise


def test_administrative_status_starts_new_baseline_for_later_source_events(world):
    from story_core.long_memory import schedule_promise, set_promise_status, select_promises
    store, book = world
    with store.write() as conn:
        v2, v3 = [chapter(conn, book, number) for number in (2, 3)]
        promise = schedule_promise(conn, book, '旧债', due_chapter=2)
        set_promise_status(conn, book, promise, 'resolved', source_chapter=3, source_version=v3)
        set_promise_status(conn, book, promise, 'active')
        assert select_promises(conn, book, 4)['required'][0]['promise_id'] == promise
        # New sourced decisions after the administrative baseline can settle it.
        set_promise_status(conn, book, promise, 'resolved', source_chapter=2, source_version=v2)
        assert select_promises(conn, book, 4)['required'] == []
        # Invalidating that new source restores the admin baseline, not the
        # earlier source decisions the administrator explicitly superseded.
        chapter(conn, book, 2)
        assert select_promises(conn, book, 4)['required'][0]['promise_id'] == promise


@pytest.mark.parametrize('status', ['resolved', 'cancelled'])
def test_explicit_reference_can_recall_terminal_promise_without_reopening(world, status):
    from story_core.long_memory import schedule_promise, set_promise_status, select_promises
    store, book = world
    with store.write() as conn:
        source = chapter(conn, book, 1)
        fulfilled = chapter(conn, book, 2)
        identity = schedule_promise(conn, book, '旧铜牌', due_chapter=2, mandatory=True,
                                    source_chapter=1, source_version=source, visibility='reader')
        set_promise_status(conn, book, identity, status, source_chapter=2, source_version=fulfilled)
        assert select_promises(conn, book, 3)['required'] == []
        result = select_promises(conn, book, 3, explicit_ids=[identity])
        assert result['missing_explicit_ids'] == []
        assert result['required'][0]['status'] == status
        assert result['required'][0]['context_reason'] == 'explicit_reference'
