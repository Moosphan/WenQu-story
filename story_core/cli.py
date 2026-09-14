"""Command line transport for the story service."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Sequence


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hulk-story")
    parser.add_argument("--root", default=os.environ.get("HULK_STORY_HOME", "./books"))
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init")
    request = init.add_mutually_exclusive_group(required=True)
    request.add_argument("--request")
    request.add_argument("--request-file", type=Path)
    init.add_argument("--title", default="")
    init.add_argument("--genre", default="")
    init.add_argument("--chapters", type=int, default=40)
    init.add_argument("--words", type=int, default=2500)

    commands.add_parser("books")
    market = commands.add_parser('market'); market.add_argument('source', choices=('fanqie_rank', 'qidian_rank')); market.add_argument('--refresh', action='store_true')
    show = commands.add_parser("show"); show.add_argument("book")
    start = commands.add_parser("start"); start.add_argument("book"); start.add_argument("--chapters", type=int); start.add_argument("--max-steps", type=int, default=500); start.add_argument("--budget-tokens", type=int, default=2_000_000)
    nxt = commands.add_parser("next"); nxt.add_argument("book"); nxt.add_argument("--worker", default="host")
    submit = commands.add_parser("submit"); submit.add_argument("task"); submit.add_argument("--lease", required=True); submit.add_argument("--result-file", type=Path, required=True); submit.add_argument("--worker", default="host")
    lookup = commands.add_parser('lookup'); lookup.add_argument('task'); lookup.add_argument('--lease', required=True); lookup.add_argument('--query', required=True); lookup.add_argument('--reason', required=True); lookup.add_argument('--worker', default='host')
    query = commands.add_parser("query"); query.add_argument("book"); query.add_argument("text"); query.add_argument("--role", default="author"); query.add_argument("--through-chapter", type=int); query.add_argument("--strategy", choices=("legacy", "bounded"), default="legacy")
    memory = commands.add_parser('memory'); memory.add_argument('book')
    operations = memory.add_subparsers(dest='memory_action', required=True)
    listing = operations.add_parser('list'); listing.add_argument('--status'); listing.add_argument('--limit', type=int, default=50); listing.add_argument('--offset', type=int, default=0)
    proposal = operations.add_parser('propose'); proposal.add_argument('--file', type=Path, required=True); proposal.add_argument('--request-id', required=True)
    decision = operations.add_parser('decide'); decision.add_argument('proposal'); decision.add_argument('--decision', choices=('accept', 'reject'), required=True); decision.add_argument('--expected-revision', type=int, required=True); decision.add_argument('--request-id', required=True); decision.add_argument('--actor', required=True); decision.add_argument('--verified-by-author', action='store_true'); decision.add_argument('--allow-conflict', action='store_true')
    binding = operations.add_parser('bind'); binding.add_argument('proposal'); binding.add_argument('entity'); binding.add_argument('--expected-revision', type=int, required=True); binding.add_argument('--request-id', required=True); binding.add_argument('--actor', required=True)
    entity = operations.add_parser('entity'); entity.add_argument('name'); entity.add_argument('--actor', required=True)
    maintenance = operations.add_parser('maintain'); maintenance.add_argument('--limit', type=int, default=5); maintenance.add_argument('--backfill', action='store_true')
    operations.add_parser('maintenance')
    status = commands.add_parser("status"); status.add_argument("book")
    project = commands.add_parser('project'); project.add_argument('book'); project.add_argument('--metadata-file', type=Path); project.add_argument('--expected-revision', type=int)
    report = commands.add_parser('report'); report.add_argument('book')
    for name in ("pause", "resume", "cancel"):
        control = commands.add_parser(name); control.add_argument("book")
    revise = commands.add_parser("revise"); revise.add_argument("book"); revise.add_argument("--chapter", type=int, required=True); revise.add_argument("--body-file", type=Path, required=True); revise.add_argument("--title")
    revise.add_argument("--feedback", default="")
    revise.add_argument("--expected-revision", type=int)
    reviews = commands.add_parser("reviews")
    reviews.add_argument("book")
    reviews.add_argument("--chapter", type=int)
    feedback = commands.add_parser('feedback')
    feedback.add_argument('book')
    feedback.add_argument('--chapter', type=int, required=True)
    feedback.add_argument('--type', dest='reviewer_type', choices=('target_reader', 'logic_reader', 'editor', 'author'), required=True)
    feedback.add_argument('--name', default='')
    feedback.add_argument('--verdict', choices=('pass', 'revise'), default='revise')
    feedback.add_argument('--rating', type=int)
    feedback.add_argument('--would-continue', action=argparse.BooleanOptionalAction, default=None)
    feedback.add_argument('--notes', required=True)
    feedback.add_argument('--version-id')
    export = commands.add_parser("export"); export.add_argument("book"); export.add_argument("--allow-partial", action="store_true")
    mcp = commands.add_parser("mcp"); mcp.add_argument("operation", choices=("serve",))
    worker = commands.add_parser("worker"); worker.add_argument("book")
    worker.add_argument('--executor', choices=('api', 'claude'))
    importer = commands.add_parser("import")
    importer.add_argument("book")
    importer.add_argument("--chapters-file", type=Path, required=True)
    importer.add_argument("--expected-revision", type=int)
    serve = commands.add_parser("serve"); serve.add_argument("--port", type=int, default=8765)
    return parser


def _dispatch(args: argparse.Namespace) -> Any:
    from story_core.service import StoryService

    service = StoryService(args.root)
    command = args.command
    if command == 'memory':
        operation = args.memory_action
        if operation == 'list': return service.memory_proposals(args.book, args.status, args.limit, args.offset)
        if operation == 'propose': return service.memory_propose(args.book, json.loads(args.file.read_text(encoding='utf-8')), request_id=args.request_id)
        if operation == 'decide': return service.memory_decide(args.book, args.proposal, decision=args.decision, actor=args.actor,
            expected_revision=args.expected_revision, request_id=args.request_id, trust=args.verified_by_author, allow_conflict=args.allow_conflict)
        if operation == 'bind': return service.memory_bind(args.book, args.proposal, args.entity, actor=args.actor, expected_revision=args.expected_revision, request_id=args.request_id)
        if operation == 'entity': return service.memory_entity(args.book, args.name, actor=args.actor)
        if operation == 'maintain': return service.memory_maintain(args.book, limit=args.limit, backfill=args.backfill)
        if operation == 'maintenance': return service.memory_maintenance(args.book)
    if command == "import":
        return service.import_chapters(args.book, json.loads(args.chapters_file.read_text(encoding="utf-8")), expected_revision=args.expected_revision)
    if command == "worker":
        from story_core.providers import configured_provider, run_worker
        return run_worker(service, args.book, configured_provider(args.executor))
    if command == "serve":
        import uvicorn
        from story_core.http import create_app
        return uvicorn.run(create_app(args.root), host="127.0.0.1", port=args.port)
    if command == "init":
        request = args.request if args.request is not None else args.request_file.read_text(encoding="utf-8")
        return service.open_book(request, title=args.title, genre=args.genre, chapter_count=args.chapters, target_words=args.words)
    if command == "books": return service.list_books()
    if command == 'market': return service.market_snapshot(args.source, refresh=args.refresh)
    if command == "show": return service.get_book(args.book)
    if command == "start": return service.start_run(args.book, chapter_limit=args.chapters, max_steps=args.max_steps, budget_tokens=args.budget_tokens)
    if command == "next": return service.next_task(args.book, worker_id=args.worker)
    if command == "submit": return service.submit_task(args.task, args.lease, json.loads(args.result_file.read_text(encoding="utf-8")), worker_id=args.worker)
    if command == 'lookup': return service.lookup_task(args.task, args.lease, args.query, args.reason, worker_id=args.worker)
    if command == "query": return service.query(args.book, args.text, role=args.role, through_chapter=args.through_chapter, strategy=args.strategy)
    if command == "status": return service.status(args.book)
    if command == 'project':
        return service.update_project_metadata(args.book, json.loads(args.metadata_file.read_text(encoding='utf-8')), args.expected_revision) if args.metadata_file else {'project': service.project_metadata(args.book)}
    if command == 'report': return service.execution_report(args.book)
    if command in {"pause", "resume", "cancel"}: return service.control(args.book, command)
    if command == "revise": return service.control(args.book, "revise", chapter_number=args.chapter, body=args.body_file.read_text(encoding="utf-8"), title=args.title, feedback=args.feedback, expected_revision=args.expected_revision)
    if command == "reviews": return service.review_history(args.book, chapter_number=args.chapter)
    if command == 'feedback': return service.add_human_feedback(args.book, args.chapter, reviewer_type=args.reviewer_type,
                                                                  reviewer_name=args.name, verdict=args.verdict,
                                                                  rating=args.rating, would_continue=args.would_continue,
                                                                  notes=args.notes, version_id=args.version_id)
    if command == "export": return service.export(args.book, allow_partial=args.allow_partial)
    if command == "mcp":
        from story_core.mcp_server import serve
        return serve(args.root)
    raise AssertionError(command)


def _error(error: Exception) -> dict[str, Any]:
    code = getattr(error, "code", "INVALID_INPUT")
    message = getattr(error, "message", str(error))
    value: dict[str, Any] = {"code": code, "message": message}
    details = getattr(error, "details", None)
    if details is not None: value["details"] = details
    return {"error": value}


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = _dispatch(args)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps(_error(error), ensure_ascii=False), file=sys.stderr)
        return 2
    except Exception as error:
        from story_core.errors import StoryError
        if not isinstance(error, StoryError): raise
        print(json.dumps(_error(error), ensure_ascii=False), file=sys.stderr)
        return 1
    if args.command != "mcp":
        print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
