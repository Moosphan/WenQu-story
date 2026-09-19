# Offline retrieval and service acceptance tools

2026-09-19. Synthetic data only; no private libraries, credentials, paid provider requests or model downloads.

## Verified locally

- TDD red: new test module failed to import the absent evaluator. Green: `.venv/bin/python -m pytest tests/test_retrieval_evaluation.py -q` → **5 passed**.
- `.venv/bin/python -m story_core.state_machine_benchmark --chapters 6 --output test-results/state-machine-small.json` → complete, **6 committed chapters / 34 real service tasks** (brief 1, outline 1, draft 6, extract 6, continuity 6, reader 12, arc 1, ending 1).
- Budget fixture stops before first task; repeated synthetic continuity blockers reach `needs_attention` with zero committed chapters and finite `max_revisions=1`.
- Real SQLite fixture verifies reader filtering for full-history and source-labelled summaries, including a deliberately mislabelled public summary whose source is author-only.
- Metric tests cover exact numerators/denominators, empty-answer false positives, repeated independent-query IDs, unavailable vector/hybrid/summary backends.

## Run state-machine experiments

```sh
.venv/bin/python -m story_core.state_machine_benchmark --chapters 500 --mode adaptive --output test-results/state-machine-500.json
.venv/bin/python -m story_core.state_machine_benchmark --chapters 500 --mode off --output test-results/state-machine-500-off.json
```

Each run uses a temporary Store, real `StoryService.next_task/submit_task`, a deterministic synthetic output adapter, explicit 64k context capacity, and checkpoint records at 100/300/500. Above 200 chapters, only the temporary database fixture changes the chapter count; product API validation stays at 200. Every point records input UTF-8 bytes, task-building time and compiler diagnostics. Results include the actual stopping reason, reserved token budget, guard experiments and driver transition bound; premature failure is not reported as completion. Model quality and actual bills are null. These commands are experiment entry points; the 500-chapter results are not claimed by this small-run report.

## Run human-labelled retrieval evaluation

```sh
.venv/bin/python -m story_core.retrieval_evaluation --store /explicit/authorized/store --labels /explicit/labels.jsonl --k 10 --output test-results/retrieval-evaluation.json
```

Each label line requires:

```json
{"case_id":"paraphrase-distance-100","query":"归还借走的旧物","scope":{"book_id":"explicit-book-id","through_chapter":100,"role":"reader","pov":null,"branch_id":"main"},"expected_ids":["human-labelled-memory-id"],"forbidden_ids":["future-or-secret-memory-id"],"category":"ordinary-paraphrase","independent_query_id":"paraphrase-1","hard_required_ids":[]}
```

First occurrence of each independent query contributes to quality totals; later occurrences contribute only to distance metrics. Categories are separate, so exact-ID and alias cases cannot silently inflate ordinary paraphrase recall. Recall and Precision@k report numerator/denominator and descriptive Wilson intervals; MRR uses a distribution-free Hoeffding interval over query scores. Correlated labels limit population interpretation. Precision denominator is k per independent query, including empty slots. Unknown false positives count answerless queries receiving any result. Timings, CPU milliseconds, result-input bytes, candidate counts and truncation flags remain per-case records.

Four strategies are always present:

- `full-history`: real Store SQL over current committed chapter versions under book/chapter/role/POV scope; descending chapter and ID define the top-k ordering. Full material byte size is recorded; this measures source availability, not model understanding.
- `verified-summary`: enabled with `--summary summary.jsonl`; requires human/offline artifacts containing `verified: true`, `book_id`, `text`, `source_ids`, `visibility`, optional `owner`. Ranking is lexical over **actual summary text** and reported as source-ID retrieval; this is not a generated semantic-summary model. Every source must still pass Store authorization. No artifact → unavailable.
- `vector-only` / `hybrid`: only enabled via `--allow-local-model` and explicitly configured offline semantic policy, using the product `retriever_for` with semantic / hybrid mode. Missing local weights/index/dependencies or backend failures → unavailable with reason and null metrics. Default never loads models. Hybrid without a vector backend is unavailable, not relabelled lexical retrieval.

## Still external-evidence-pending

Real ordinary synonym paraphrases, renaming, causal and ownership-transfer labels need independently authored authorized examples. No pretrained local model was loaded in these tests. No 95% ordinary far-context semantic recall, population confidence, generated prose quality, style continuity, real token usage or provider billing claim is established. Compiler preservation after packing is a separate evaluation; these retrieval metrics score top-k evidence before packing.
