"""Trusted-author actions and deterministic maintenance, synthetic sources only."""
import pytest
from story_core.errors import StoryError
from story_core.storage import Store, uid
from story_core import long_memory as lm
from story_core import memory_workflow as wf


@pytest.fixture
def world(tmp_path):
    store = Store(tmp_path)
    book = store.create_book('synthetic', 'fixture', {})['book_id']
    with store.write() as conn:
        entity = lm.register_entity(conn, book, '甲')
        version = chapter(conn, book, 1)
    return store, book, entity, version


def chapter(conn, book, number, body='甲在城西。他以为乙已死。'):
    version = uid('v')
    conn.execute('INSERT INTO chapter_versions VALUES (?,?,?,?,?,?)', (version, book, number, 'fixture', body, 0))
    conn.execute("INSERT OR REPLACE INTO chapters VALUES (?,?,?,'committed')", (book, number, version))
    return version


def candidate(entity, version, **changes):
    result = dict(subject_entity_id=entity, predicate='所在位置', value='城西', source_chapter=1,
                  source_version=version, evidence='甲在城西', story_valid_from=10)
    result.update(changes)
    return result


def propose(conn, book, entity, version, **changes):
    return wf.submit_proposals(conn, book, [candidate(entity, version, **changes)], request_id=uid('request'))['proposals'][0]['id']


def decide(conn, book, proposal, **changes):
    args = dict(decision='accept', actor='author', expected_revision=0, request_id=uid('request'), trust=True)
    args.update(changes)
    return wf.decide_proposal(conn, book, proposal, **args)


def test_pending_to_explicit_accept_and_retry(world):
    store, book, entity, version = world
    with store.write() as conn:
        proposal = propose(conn, book, entity, version)
        assert lm.current_state(conn, book, [entity], story_time=10) == []
        with pytest.raises(StoryError, match='显式'):
            decide(conn, book, proposal, trust=False)
        result = decide(conn, book, proposal, request_id='accept-once')
        again = decide(conn, book, proposal, request_id='accept-once')
        assert again == result
        assert result['revision'] == 1 and result['canonical_changed']
        assert lm.current_state(conn, book, [entity], story_time=10)[0]['predicate'] == 'location'
        assert conn.execute('SELECT count(*) FROM lm_fact_events').fetchone()[0] == 1
        assert wf.preview_proposal(conn, book, proposal)['status'] == 'accepted'


def test_rejection_retains_source_and_status_prevents_second_decision(world):
    store, book, entity, version = world
    with store.write() as conn:
        proposal = propose(conn, book, entity, version)
        result = decide(conn, book, proposal, decision='reject', trust=False)
        assert result['status'] == 'rejected' and not result['canonical_changed']
        assert wf.preview_proposal(conn, book, proposal)['candidate']['source_version'] == version
        with pytest.raises(StoryError) as error:
            decide(conn, book, proposal)
        assert error.value.code == 'PROPOSAL_DECIDED'
        assert conn.execute('SELECT count(*) FROM lm_fact_events').fetchone()[0] == 0


def test_source_edit_and_revision_reject_approval(world):
    store, book, entity, version = world
    with store.write() as conn:
        proposal = propose(conn, book, entity, version)
        with pytest.raises(StoryError) as error:
            decide(conn, book, proposal, expected_revision=5)
        assert error.value.code == 'STALE_REVISION'
        chapter(conn, book, 1, '甲改在城东。')
        with pytest.raises(StoryError) as error:
            decide(conn, book, proposal)
        assert error.value.code == 'STALE_SOURCE'


def test_ambiguous_alias_requires_audited_explicit_binding(world):
    store, book, entity, version = world
    with store.write() as conn:
        other = lm.register_entity(conn, book, '另一个甲')
        lm.register_alias(conn, book, other, '甲')
        proposal = propose(conn, book, entity, version, subject_entity_id=None, subject_alias='甲')
        assert len(wf.preview_proposal(conn, book, proposal)['identity_candidates']) == 2
        with pytest.raises(StoryError) as error:
            decide(conn, book, proposal)
        assert error.value.code == 'AMBIGUOUS_ENTITY'
        wf.bind_proposal(conn, book, proposal, entity, actor='author', expected_revision=0, request_id='bind')
        decide(conn, book, proposal, expected_revision=1)
        assert conn.execute("SELECT count(*) FROM lm_workflow_audit WHERE action='bind'").fetchone()[0] == 1


def test_conflicts_require_explicit_acceptance_and_beliefs_do_not_conflict(world):
    store, book, entity, version = world
    with store.write() as conn:
        first = propose(conn, book, entity, version)
        decide(conn, book, first)
        second = propose(conn, book, entity, version, value='城东')
        assert len(wf.preview_proposal(conn, book, second)['conflicts']) == 1
        with pytest.raises(StoryError) as error:
            decide(conn, book, second, expected_revision=1)
        assert error.value.code == 'FACT_CONFLICT'
        decide(conn, book, second, expected_revision=1, allow_conflict=True)
        belief = propose(conn, book, entity, version, value='城北', scope='character_belief', owner_entity_id=entity)
        assert wf.preview_proposal(conn, book, belief)['conflicts'] == []
        decide(conn, book, belief, expected_revision=2)
        assert lm.current_state(conn, book, [entity], story_time=10)[0]['value'] == '城东'
        assert {x['value'] for x in lm.current_state(conn, book, [entity], story_time=10, pov_entity_id=entity)} == {'城东', '城北'}


def test_batch_quarantine_is_finite_preserves_good_candidates_and_idempotent(world):
    store, book, entity, version = world
    with store.write() as conn:
        candidates = [candidate(entity, version), candidate(entity, version, evidence='没有这句话'),
                      candidate(entity, version, predicate='未知属性'), candidate(entity, version, story_valid_from=None)]
        result = wf.submit_proposals(conn, book, candidates, request_id='batch')
        assert len(result['proposals']) == 1 and len(result['quarantined']) == 3
        assert result == wf.submit_proposals(conn, book, candidates, request_id='batch')
        with pytest.raises(StoryError):
            wf.submit_proposals(conn, book, candidates[:1], request_id='batch')
        with pytest.raises(StoryError):
            wf.submit_proposals(conn, book, candidates * 100, request_id='large')
        wf.register_predicate(conn, book, 'coins', aliases=['铜钱数'], actor='author')
        assert propose(conn, book, entity, version, predicate='铜钱数')
        overview = wf.list_proposals(conn, book, limit=1)
        assert len(overview['items']) == 1 and overview['counts'] == {'pending': 2, 'quarantined': 3}
        assert len(wf.list_proposals(conn, book, limit=1, offset=1)['items']) == 1


def test_cross_book_entities_and_belief_owner_are_quarantined(world):
    store, book, entity, version = world
    other_book = store.create_book('synthetic', 'other', {})['book_id']
    with store.write() as conn:
        other_entity = lm.register_entity(conn, other_book, '甲')
        result = wf.submit_proposals(conn, book, [candidate(other_entity, version),
              candidate(entity, version, scope='character_belief')], request_id='scope')
        assert not result['proposals'] and len(result['quarantined']) == 2


def test_queue_rebuild_restart_and_source_cas(world):
    store, book, entity, version = world
    with store.write() as conn:
        first = wf.enqueue_maintenance(conn, book, version)
        assert first == wf.enqueue_maintenance(conn, book, version)
    reopened = Store(store.root)
    with reopened.write() as conn:
        result = wf.run_maintenance_step(conn, book, limit=1)
        assert result['processed'] == 1
        digest = conn.execute('SELECT * FROM lm_source_digests WHERE source_version=?', (version,)).fetchone()
        assert digest['status'] == 'ready' and len(digest['digest']) <= 2048
        assert wf.run_maintenance_step(conn, book)['processed'] == 0
        newer = chapter(conn, book, 1, '新正文。')
        wf.enqueue_maintenance(conn, book, version, invalidated=True)
        wf.enqueue_maintenance(conn, book, newer)
        wf.run_maintenance_step(conn, book)
        assert conn.execute('SELECT status FROM lm_source_digests WHERE source_version=?', (version,)).fetchone()[0] == 'stale'
        assert conn.execute('SELECT body FROM chapter_versions WHERE id=?', (version,)).fetchone()[0] == '甲在城西。他以为乙已死。'


def test_transitive_reindex_does_not_clear_verification(world):
    store, book, entity, version = world
    with store.write() as conn:
        v2, v3 = chapter(conn, book, 2), chapter(conn, book, 3)
        conn.executemany('INSERT INTO lm_dependencies VALUES (?,?,?,?)', [(book, 'main', v2, version), (book, 'main', v3, v2)])
        wf.enqueue_maintenance(conn, book, version, invalidated=True)
        result = wf.run_maintenance_step(conn, book, limit=2)
        assert result['processed'] == 2
        assert wf.maintenance_status(conn, book)['counts']['pending'] == 1
        wf.run_maintenance_step(conn, book, limit=2)
        digests = conn.execute('SELECT status FROM lm_source_digests').fetchall()
        assert len(digests) == 3 and {x[0] for x in digests} == {'needs_verification'}
        assert conn.execute('SELECT count(*) FROM lm_dependencies').fetchone()[0] == 2


def test_queue_failure_attempts_are_bounded(world, monkeypatch):
    store, book, entity, version = world
    with store.write() as conn:
        wf.enqueue_maintenance(conn, book, version)
    def fail(*args, **kwargs):
        raise RuntimeError('synthetic failure')
    monkeypatch.setattr(wf, '_build_digest', fail)
    for _ in range(5):
        with store.write() as conn:
            wf.run_maintenance_step(conn, book)
    with store.read() as conn:
        job = wf.maintenance_status(conn, book)['items'][0]
        assert job['status'] == 'failed' and job['attempts'] == 3


def test_malformed_field_types_quarantine_without_losing_good_candidates(world):
    store, book, entity, version = world
    with store.write() as conn:
        candidates = [candidate(entity, version), candidate(entity, version, scope=[]),
                      candidate(entity, version, visibility={}), candidate(entity, version, owner_entity_id=['bad']),
                      candidate(entity, version, subject_alias=['bad'])]
        result = wf.submit_proposals(conn, book, candidates, request_id='malformed')
        assert len(result['proposals']) == 1 and len(result['quarantined']) == 4


def test_author_only_belief_conflicts_are_visible_to_author_review(world):
    store, book, entity, version = world
    with store.write() as conn:
        first = propose(conn, book, entity, version, scope='character_belief', owner_entity_id=entity, visibility='author')
        decide(conn, book, first)
        second = propose(conn, book, entity, version, value='城东', scope='character_belief', owner_entity_id=entity, visibility='author')
        assert len(wf.preview_proposal(conn, book, second)['conflicts']) == 1
        with pytest.raises(StoryError) as error:
            decide(conn, book, second, expected_revision=1)
        assert error.value.code == 'FACT_CONFLICT'


def test_binding_change_invalidates_stale_review_and_preserves_original_alias(world):
    store, book, entity, version = world
    with store.write() as conn:
        other = lm.register_entity(conn, book, '乙')
        proposal = propose(conn, book, entity, version, subject_entity_id=None, subject_alias='未绑定姓名')
        result = wf.bind_proposal(conn, book, proposal, entity, actor='author', expected_revision=0, request_id='bind-once')
        assert result['revision'] == 1
        assert result == wf.bind_proposal(conn, book, proposal, entity, actor='author', expected_revision=0, request_id='bind-once')
        with pytest.raises(StoryError) as error:
            wf.bind_proposal(conn, book, proposal, other, actor='author', expected_revision=0, request_id='stale-bind')
        assert error.value.code == 'STALE_REVISION'
        with pytest.raises(StoryError) as error:
            decide(conn, book, proposal)
        assert error.value.code == 'STALE_REVISION'
        assert wf.preview_proposal(conn, book, proposal)['candidate']['subject_alias'] == '未绑定姓名'
        decide(conn, book, proposal, expected_revision=1)


def test_atomic_submit_rolls_back_partial_proposals_when_audit_fails(world, monkeypatch):
    store, book, entity, version = world
    def fail(*args):
        raise RuntimeError('synthetic audit failure')
    monkeypatch.setattr(wf, '_audit', fail)
    with store.write() as conn:
        with pytest.raises(RuntimeError):
            propose(conn, book, entity, version)
        assert conn.execute('SELECT count(*) FROM lm_proposals').fetchone()[0] == 0


def test_stale_dependencies_cannot_be_silently_reverified(world):
    store, book, entity, version = world
    with store.write() as conn:
        v2 = chapter(conn, book, 2)
        proposal = propose(conn, book, entity, v2, source_chapter=2, dependency_versions=[version])
        chapter(conn, book, 1, '已修改。')
        with pytest.raises(StoryError) as error:
            decide(conn, book, proposal)
        assert error.value.code == 'STALE_DEPENDENCY'


def test_queue_transaction_crash_rolls_back_and_resumes(world):
    store, book, entity, version = world
    with store.write() as conn:
        wf.enqueue_maintenance(conn, book, version)
    with pytest.raises(RuntimeError):
        with store.write() as conn:
            wf.run_maintenance_step(conn, book)
            raise RuntimeError('process interrupted before transaction commit')
    reopened = Store(store.root)
    with reopened.write() as conn:
        assert wf.maintenance_status(conn, book)['counts'] == {'pending': 1}
        assert conn.execute('SELECT count(*) FROM lm_source_digests').fetchone()[0] == 0
        assert wf.run_maintenance_step(conn, book)['ready'] == 1


def test_source_digest_rechecks_pointer_and_does_not_publish_stale_digest(world, monkeypatch):
    store, book, entity, version = world
    original = wf._build_digest
    def edit_during_build(conn, book, branch, source):
        result = original(conn, book, branch, source)
        chapter(conn, book, 1, 'changed during computation')
        return result
    monkeypatch.setattr(wf, '_build_digest', edit_during_build)
    with store.write() as conn:
        wf.enqueue_maintenance(conn, book, version)
        result = wf.run_maintenance_step(conn, book)
        assert result['failed'] == 1
        assert conn.execute('SELECT count(*) FROM lm_source_digests').fetchone()[0] == 0
        assert wf.maintenance_status(conn, book)['items'][0]['error'] == 'STALE_SOURCE'


def test_legacy_binding_copies_source_and_never_infers_story_time(world):
    store, book, entity, version = world
    with store.write() as conn:
        conn.execute('INSERT INTO memories VALUES (?,?,?,?,?,?,?,?,?,?)',
                     ('legacy', book, 1, version, 'fact', '甲', '城西', '甲在城西', 'reader', '{}'))
        result = wf.submit_legacy_proposals(conn, book, [{'legacy_id': 'legacy', 'subject_entity_id': entity,
                  'predicate': 'location', 'story_valid_from': 10}], request_id='legacy-submit')
        proposal = wf.preview_proposal(conn, book, result['proposals'][0]['id'])
        assert proposal['candidate']['source_version'] == version
        assert proposal['candidate']['evidence'] == '甲在城西'
        assert proposal['candidate']['legacy_id'] == 'legacy'
        bad = wf.submit_legacy_proposals(conn, book, [{'legacy_id': 'legacy', 'subject_entity_id': entity,
                 'predicate': 'location'}], request_id='legacy-no-time')
        assert len(bad['quarantined']) == 1
        assert conn.execute('SELECT count(*) FROM memories').fetchone()[0] == 1


def test_source_digest_views_bound_output_without_recursive_summaries(world):
    store, book, entity, version = world
    with store.write() as conn:
        for number in range(1, 41):
            v = version if number == 1 else chapter(conn, book, number, '\x00' * 10000)
            wf.enqueue_maintenance(conn, book, v)
        wf.run_maintenance_step(conn, book, limit=20)
        wf.run_maintenance_step(conn, book, limit=20)
        view = wf.source_digest_view(conn, book, through_chapter=40, recent_count=5, arc_count=3, arc_span=10)
        assert len(view['recent']) == 5
        assert len(view['arcs']) == 3
        assert all(len(arc['sources']) <= 3 for arc in view['arcs'])
        assert all(len(row[0].encode()) <= 2048 for row in conn.execute('SELECT digest FROM lm_source_digests'))
        assert all(x['source_chapter'] <= 40 and x['source_version'] for x in view['recent'])
        assert all(x['digest']['kind'] == 'source_index' for arc in view['arcs'] for x in arc['sources'])


def test_failed_jobs_do_not_reset_attempts_on_invalidation(world, monkeypatch):
    store, book, entity, version = world
    def fail(*args):
        raise RuntimeError('failure')
    monkeypatch.setattr(wf, '_build_digest', fail)
    with store.write() as conn:
        wf.enqueue_maintenance(conn, book, version)
        for _ in range(3):
            wf.run_maintenance_step(conn, book)
        wf.enqueue_maintenance(conn, book, version, invalidated=True)
        job = wf.maintenance_status(conn, book)['items'][0]
        assert job['attempts'] == 3 and job['status'] == 'failed'
        assert wf.run_maintenance_step(conn, book)['processed'] == 0


def test_digest_view_marks_dependency_stale_even_before_maintenance_hook(world):
    store, book, entity, version = world
    with store.write() as conn:
        dependent = chapter(conn, book, 2)
        conn.execute('INSERT INTO lm_dependencies VALUES (?,?,?,?)', (book, 'main', dependent, version))
        wf.enqueue_maintenance(conn, book, dependent)
        wf.run_maintenance_step(conn, book)
        chapter(conn, book, 1, 'edited dependency')
        view = wf.source_digest_view(conn, book, through_chapter=2)
        assert view['recent'][0]['status'] == 'needs_verification'


def test_newer_schema_guard_and_workflow_book_cascade(world):
    store, book, entity, version = world
    with store.write() as conn:
        propose(conn, book, entity, version)
        wf.enqueue_maintenance(conn, book, version)
        wf.run_maintenance_step(conn, book)
        for table in ('chapters', 'chapter_versions'):
            conn.execute(f'DELETE FROM {table} WHERE book_id=?', (book,))
        conn.execute('DELETE FROM books WHERE id=?', (book,))
        assert conn.execute('SELECT count(*) FROM lm_proposals').fetchone()[0] == 0
        assert conn.execute('SELECT count(*) FROM lm_source_digests').fetchone()[0] == 0
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
        conn.execute('PRAGMA user_version=12')
    with pytest.raises(StoryError) as error:
        Store(store.root)
    assert error.value.code == 'NEWER_DATABASE'


def test_maintenance_backfills_missing_legacy_retrieval_index_before_ready(world):
    from story_core.retrieval import SQLiteRetriever, RetrievalScope
    store, book, entity, version = world
    with store.write() as conn:
        conn.execute('INSERT INTO memories VALUES (?,?,?,?,?,?,?,?,?,?)',
                     ('legacy-unindexed', book, 1, version, 'fact', '所在位置', '甲在城西', '甲在城西', 'reader', '{}'))
        wf.enqueue_maintenance(conn, book, version)
    retriever, scope = SQLiteRetriever(store), RetrievalScope(book, through_chapter=1)
    before = retriever.search('城西', scope)
    assert before['index_complete'] is False and before['hits'] == []
    with store.write() as conn:
        assert wf.run_maintenance_step(conn, book)['ready'] == 1
    after = retriever.search('城西', scope)
    assert after['index_complete'] is True
    assert [hit['id'] for hit in after['hits']] == ['legacy-unindexed']


def test_retrieval_index_failure_rolls_back_partial_index_and_digest_with_bounded_retry(world, monkeypatch):
    from story_core import retrieval
    store, book, entity, version = world
    original = retrieval.index_version
    with store.write() as conn:
        conn.execute('INSERT INTO memories VALUES (?,?,?,?,?,?,?,?,?,?)',
                     ('legacy-index-failure', book, 1, version, 'fact', '所在位置', '甲在城西', '甲在城西', 'reader', '{}'))
        retrieval.initialize_index(conn)
        wf.enqueue_maintenance(conn, book, version)
    def fail_after_index(conn, book_id, version_id):
        original(conn, book_id, version_id)
        raise RuntimeError('synthetic index failure')
    monkeypatch.setattr(retrieval, 'index_version', fail_after_index)
    for attempt in range(1, 4):
        with store.write() as conn:
            result = wf.run_maintenance_step(conn, book)
            assert result['failed'] == 1 and result['ready'] == 0
            assert conn.execute('SELECT count(*) FROM memory_indexed').fetchone()[0] == 0
            assert conn.execute('SELECT count(*) FROM memory_terms').fetchone()[0] == 0
            assert conn.execute('SELECT count(*) FROM lm_source_digests').fetchone()[0] == 0
            job = wf.maintenance_status(conn, book)['items'][0]
            assert job['attempts'] == attempt
    with store.write() as conn:
        assert wf.maintenance_status(conn, book)['items'][0]['status'] == 'failed'
        assert wf.run_maintenance_step(conn, book)['processed'] == 0


def test_stale_pending_proposal_can_be_rejected_with_revision_guard_and_audit(world):
    store, book, entity, version = world
    with store.write() as conn:
        proposal = propose(conn, book, entity, version)
        chapter(conn, book, 1, '作者已修改来源。')
        conn.execute('UPDATE books SET revision=revision+1 WHERE id=?', (book,))
        with pytest.raises(StoryError) as error:
            decide(conn, book, proposal, decision='reject')
        assert error.value.code == 'STALE_REVISION'
        with pytest.raises(StoryError) as error:
            decide(conn, book, proposal, expected_revision=1)
        assert error.value.code == 'STALE_SOURCE'
        rejected = decide(conn, book, proposal, decision='reject', expected_revision=1, request_id='reject-stale')
        assert rejected['status'] == 'rejected' and rejected['revision'] == 1
        assert rejected == decide(conn, book, proposal, decision='reject', expected_revision=1, request_id='reject-stale')
        preview = wf.preview_proposal(conn, book, proposal)
        assert preview['candidate']['source_version'] == version and preview['source_current'] is False
        assert conn.execute("SELECT count(*) FROM lm_workflow_audit WHERE request_id='reject-stale'").fetchone()[0] == 1
        assert conn.execute('SELECT count(*) FROM lm_fact_events').fetchone()[0] == 0
