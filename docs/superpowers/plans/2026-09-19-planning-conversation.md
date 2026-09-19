# Planning Conversation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Authors use a global creative assistant to discuss worldbuilding, volume/chapter plans, characters and selected prose, then explicitly confirm independent replanning when appropriate.

**Architecture:** Persist book-scoped conversation turns and revision-bound proposals. Discussion never changes canonical material; accepting a proposal starts existing atomic, cancellable settings planning with a proposed brief and author direction. Existing manuscript and candidate chapter skeletons remain protected.

**Tech Stack:** Python, SQLite, FastAPI, current configured provider, vanilla JavaScript.

## Global Constraints

- Develop directly on main as requested. No real novel rewrites without accepting a displayed proposal.
- Retain 200-chapter bounded batches, budget gates and independent completion stop.
- Chat persists, survives reload, returns recoverable failures after restart, and never treats proposals as facts.
- Treat chapter outlines as plans, not actual manuscript evidence. Disclose bounded context and protected chapter boundary.
- One active discussion call per book; reject stale proposal application and apply idempotently.

### Task 1: Conversation domain and API

**Files:** Create `story_core/author_assistant.py`, `tests/test_author_assistant.py`; modify storage/http/settings_planning/service/outline_batches.

**Interfaces:** GET `/api/books/{id}/assistant` -> `{turns:[{id,message,status,reply,proposal,error,base_revision,can_apply}],protected_through}`; POST same with `{message,expected_revision,chapter_number?}` starts configured-provider background job and returns turn. POST `/assistant/{turn_id}/apply` starts independent planner and returns planning result. Proposal is null for ordinary discussion, otherwise `{direction,brief}`.

- [x] Add failing tests: discussion leaves book/plan/candidate unchanged; second turn receives previous conversation; stale proposal rejected; accepted proposal reaches every outline batch; cancellation leaves original brief/plan; success publishes proposed brief/plan and pauses.
- [x] Run `.venv/bin/python -m pytest tests/test_author_assistant.py -q`, confirm missing module/interface failures.
- [x] Implement durable queued/running/complete/failed turns, bounded context and provider schema `{reply,proposal}` using existing brief schema in optional proposals. Validate before marking complete. Recover queued/running turns to failed on server restart. API errors retain user messages and show retry guidance.
- [x] Apply under book transaction with revision fence, reject active generation/planning; save proposal brief and author direction into settings-planning target, inject direction into outline task instruction, require full volume roadmap consistent with proposed scope. Preserve completed/candidate chapter skeletons and old canonical data until final publish.
- [x] Run focused tests; review API/domain behavior before deployment.

### Task 2: Planning conversation UI

**Files:** Create `story_core/web/assistant.js`; modify `story_core/web/index.html`, `app.js`, `style.css`; add `tests/test_author_assistant_ui.py`.

**Interfaces:** Consume Task 1 routes. All displayed generated/user text uses textContent. Show proposal and explicit accept action. Do not invoke apply when sending a message.

- [x] Add a global topbar entry and nonmodal conversation panel with history, message composer, optional selected chapter, status/error, protected-boundary note and example prompts that fill composer only.
- [x] Poll pending turns through existing refresh cycle; fence late responses on book switches; preserve draft while polling; disable duplicate sends and apply during pending calls or planning.
- [x] Show reply and direction separately; require explicit confirm button to generate revised outline. Keep independent planning progress globally visible.
- [x] Verify via fake-provider HTTP tests and browser that discussion has no canonical side effects, markup is inert, proposal confirmation is separate, and history reloads.

### Task 3: Integration and release

- [x] Run `.venv/bin/python -m pytest -q`, `node --check story_core/web/app.js`, `git diff --check`.
- [x] Review changes for concurrency, stale revisions, budget/context limits and manuscript preservation.
- [x] Back up runtime SQLite and changed files, deploy only after checking runtime idle, validate UI and routes. Use fake-provider sandbox to test full confirm/replan/cancel flows.
- [x] Commit/push main and explain entry point plus a concrete author prompt for expanding this cultivation novel beyond one sect. Do not automatically accept a real proposal.
