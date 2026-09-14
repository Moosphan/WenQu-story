# Hulk Story Task Protocol

## Select a project and its scope

Use one `book_id` per request. If the request does not name a book and more than one work is available, inspect `story_books` and `story_status`; select only when the title or current state makes it unambiguous. Creating a book requires a user request to create one. Starting a run requires an explicit request to write now, or an instruction such as “开书并开始写”.

Read `story_capabilities` when choosing an execution path. A host-driven session claims and submits one task; an API or configured worker can process a bounded batch. `worker_configured` means an executor is configured, not that the prose has passed review. A local export is not a platform publication.

## Natural-language operation matrix

| Request | Read first | Write action | Done when |
|---|---|---|---|
| 开书 | capabilities, books when needed | open; optionally start | a book and, if requested, a run exist |
| 看开书方向 | capabilities | proposals only | return editable non-canon suggestions; no book or run is created |
| 继续 / 日更 | status | start or resume, then claim one task | the task is submitted or a saved stop state is reached |
| 修改章节 | book, status, current revision | revise with full replacement body and feedback | a new candidate run is created; it is not yet canon |
| 查人物、物品、约定 | query or memory facets | none | return evidence and version source, or state no canon evidence was found |
| 审稿 | reviews, status, task result | none unless the user asks to revise | report the persisted reviewer, attempt, evidence, and verdict |
| 录入真人反馈 | book chapter and version | feedback | persist reader type, version, verdict, optional rating and notes without changing canon |
| 补充项目资料 | book, current revision | update project metadata | persist author material without changing canon |
| 看榜单 / 找选题 | market sources and snapshots | market ideas from selected sources and author preferences | return source-item evidence and editable suggestions, never a platform prediction |
| 导入 | book revision | import | chapters are `imported`, never automatically approved |
| 导出 | status | export | state whether output is complete or partial; EPUB 3 is present only for a complete export |

For a manual host step, the response schema returned by `story_next` is the complete contract. Validate field names, evidence, and chapter boundary before `story_submit`. Do not start a second state machine in prompts or side files.

Project metadata such as platform, reader target, boundaries, seed characters and world rules is author material. A GUI or embedded DSH WebView may use the Core project endpoint with the current book revision, but it must not promote that material to canon, reader memory, or a task result.

## Role and knowledge boundaries

| Task role | May use | Must not claim |
|---|---|---|
| Brief / outline | user request, imported text marked unreviewed | imported claims are confirmed canon |
| Draft / revise / extract / continuity | author task input, canonical memory, current plan and manifest | a planned event has happened without textual evidence |
| Reader | candidate, reader history, public reader memory, reader manifest | hidden outline, future chapters, private author memories, independent blind review without isolation |
| Ending | committed chapters, plan, canonical memory | platform readiness or human-reader validation |

Author tasks receive `required_memory` and bounded `supplementary_memory`. Required memory contains unresolved promises plus memories linked to named chapter participants or exact plan terms; it must not be discarded for relevance ranking. Supplemental memory is deterministic lexical recall and may be empty. Read authoritative memory from `required_memory`; the duplicate legacy `canonical_memory` field is no longer emitted to avoid doubling request size. A chapter outline may set `pov` to a canonical entity key. When it does, `knowledge` records owned by another character are withheld from the task; `pov_context` reports only the selected POV and a withheld count, never the withheld keys or values. No `pov` means legacy behavior remains unchanged.

`context_manifest` records `role`, `through_chapter`, `book_revision`, sources actually supplied in the `required` or `supplementary` tier, recent chapter sources, and optional safe `pov_context`. Cite those sources when explaining an inconsistency. It does not grant a reader access to author-only data.

Reader review is two serial, isolated tasks. `target_reader` is the target-genre reading pass; `logic_reader` checks information, causality, fair clues, POV, and continuity. The Core supplies immutable `reader_profile` metadata and persists it with the task; a host must not choose or alter it. The second profile never receives the first profile's review. Both profiles must pass before commit; either may request the existing bounded revision loop. These are model personas, not independent human readers or platform metrics.

Human feedback is a separate, author-supplied record attached to one committed `version_id`. Record its stated reader type, verdict, optional 1–5 rating, willingness to continue, and notes. It never impersonates a model review, changes canon, restarts a run, or proves platform or human-panel validation. If the author asks to act on it, begin the ordinary bounded revision flow against the then-current chapter revision.

After every fifth non-final committed chapter, the Core issues an `arc` review before advancing the batch. Its `arc_chapters` are exactly the five committed chapters in scope, with matching `arc_plan`, canon, and obligations; it has no future text or candidate draft. A pass advances to the next chapter or the batch stop. A `revise` result or blocker/major issue becomes `needs_attention` with an author decision required; it never auto-rewrites multiple committed chapters. The five-chapter cadence is an MVP checkpoint, not a claim that every story arc has that length.

## Memory and continuity

Canon memory is accepted only with an exact quote from the committed chapter version. Entity records can use `entity_type` only when the prose supports `person`, `item`, `place`, `organization`, `creature`, or `other`; leave it untyped rather than guessing. An entity may include 1–12 aliases only when those exact names or honorifics appear in evidence; aliases are search metadata, never separate canon or guessed synonyms. Legacy untyped entities remain searchable under `实体`.

Use exact entity, relationship, promise, timeline, knowledge, and fact keys for retrieval. A search miss means no matching retrieved evidence under the requested scope; it does not prove the story event never happened. Keep character knowledge separate from reader disclosure and author truth.

For an outline promise marked `mandatory`, `promise_obligations` lists obligations due now or within two chapters. If it is due or overdue and no prior canon record has status `paid` or `waived`, the extraction result must include the same key with `paid` or `waived` and an exact quote from this candidate body. Otherwise `story_submit` rejects the extraction with `PROMISE_DUE`; it does not rewrite the manuscript. `waived` means the candidate text explicitly replaces or abandons the expectation, never a silent skip. This gate verifies lifecycle evidence, not payoff quality or reader satisfaction.

## Recovery and handoff

Before resuming in Claude Code, Codex, DSH, or GUI, call `story_status` with the same `book_id`. If a task is leased to another worker, do not duplicate it. A stale revision or lease error means discard the local candidate and claim a fresh task. Pause, cancel, or resume only on the user's intended run.

When a reviewer returns `revise`, preserve the verdict, evidence, and suggested scope. Revise the candidate, then allow extraction, continuity review, and reader review to run again. `needs_attention` requires an author decision or a bounded new revision; it is never a hidden retry loop.

## Completion language

Call a chapter “定稿” only after the Core commits it. Call a book “可导出成稿” only when the Core marks it complete and export succeeds; that package may include EPUB 3 alongside text and manifest files. Partial export deliberately omits EPUB and retains an unfinished-draft label. Keep these separate from “真人读者验证”, “平台审核”, “签约”, and “已发布”; none follows automatically from a successful task or export.
