# Entity merge and stable redirects — implementation evidence

Synthetic isolated SQLite tests only. No production database, manuscript migration, paid calls, model invocation, or external deployment performed.

## Behavior

- Immutable original entity/fact/event IDs remain stored. Entity and fact redirects resolve old aliases, write identities, POV owners, explicit dependencies, and declared typed relation references (`{"type":"entity","entity_id":"..."}`). Free text is unchanged.
- Merge previews include both subject and belief-owner affected facts. Overlapping verified event values require an explicit winner for every collision; inconsistent decisions fail. The preview explains that the rejected event is superseded over its **entire** interval. Supersessions are separate audit rows; original values/evidence are not edited.
- Self merges, type mismatches, foreign book/branch IDs, cycles, stale revisions and altered idempotency requests are rejected. Identical requests replay before revision checking. Existing author revision/lease fence preserves leases created after the accepted revision.
- HTTP POST `/api/books/{book_id}/memory/merge/preview` and `/memory/merge`; CLI `memory BOOK merge-preview SOURCE TARGET` and `memory BOOK merge SOURCE TARGET --resolutions-file FILE --actor ACTOR --expected-revision N --request-id ID`.
- Explicit old fact dependencies carry identity aliases so compilation still regards them as required. Shared dependency dedup fix also removes index-retrieved historical evidence when an explicit dependency provides the same source.

## Red / green evidence

1. `python -m pytest tests/test_memory_identity.py -q`: **2 failed** before implementation, both `ModuleNotFoundError: story_core.memory_identity`.
2. Same command after core implementation: **2 passed**.
3. Cascade regression initially failed with foreign-key constraint; redirect and supersession schema now participates in book/entity/fact cascades, alongside normal source cleanup.
4. `.venv/bin/python -m pytest tests/test_memory_identity.py -q`: new historical-evidence dedup regression **1 failed, 7 passed** before parent-agent fix; then **8 passed** after it.
5. `.venv/bin/python -m pytest tests/test_memory_identity.py tests/test_long_memory.py tests/test_memory_workflow.py tests/test_memory_actions.py tests/test_context_compiler.py -q`: **98 passed**, two existing FastAPI/Starlette deprecation warnings.

The system Python lacks `jsonschema`; an intermediate HTTP/CLI run failed during imports. Final validation uses repository `.venv/bin/python` and covers HTTP/CLI successfully.

## Changed files

Owned: `story_core/memory_identity.py`, `story_core/long_memory.py`, `story_core/memory_workflow.py`, `story_core/memory_actions.py`, `tests/test_memory_identity.py`, this report.

Coordinated shared changes: `story_core/storage.py` (schema initialization only; user_version left to parent), `story_core/dependency_resolution.py`, `story_core/context_compiler.py` (stable alias recognition), `story_core/http.py`, `story_core/cli.py`.

## Limits

- Author merge preview inspects the affected fact history, with pairwise comparisons within each canonical fact group. It is an administrative operation, not an automatic context-selection path.
- Supersession is event-wide; interval slicing and undo are not implemented. The author must select a consistent surviving set, explicitly described in the preview.
- Relation canonicalization recognizes the declared typed object above; arbitrary strings and undeclared JSON fields are intentionally untouched.
- Core supports branch IDs; author HTTP/CLI facade currently uses `main`, matching the existing memory facade.
- Synthetic correctness tests establish identity/audit/lease behavior, not literary or semantic-recall quality.
