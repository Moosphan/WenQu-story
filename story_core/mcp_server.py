"""Official MCP SDK stdio adapter for :class:`StoryService`."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP


def create_server(root: str | Path | None = None) -> FastMCP:
    from story_core.errors import StoryError
    from story_core.service import StoryService

    service = StoryService(root or os.environ.get("HULK_STORY_HOME", "./books"))
    server = FastMCP("Hulk Story", instructions="Persistent novel-writing workflow tools. Call story_open, start, then repeatedly call story_next and story_submit.")

    def invoke(call, *args, **kwargs):
        try:
            return call(*args, **kwargs)
        except StoryError as error:
            details = f" details={error.details!r}" if getattr(error, "details", None) is not None else ""
            raise ValueError(f"{error.code}: {error.message}{details}") from error

    @server.tool(name="story_capabilities")
    def capabilities() -> dict[str, Any]: return invoke(service.capabilities)

    @server.tool(name="story_market_sources")
    def market_sources() -> list[dict[str, Any]]: return invoke(service.market_sources)

    @server.tool(name="story_market_snapshot")
    def market_snapshot(source_id: str, refresh: bool = False) -> dict[str, Any]: return invoke(service.market_snapshot, source_id, refresh)

    @server.tool(name="story_market_ideas")
    def market_ideas(source_ids: list[str], preferences: dict[str, Any] | None = None, limit: int = 3) -> dict[str, Any]:
        return invoke(service.market_ideas, source_ids, preferences or {}, limit)

    @server.tool(name="story_proposals")
    def proposals(request: str, project: dict[str, Any] | None = None, limit: int = 3) -> dict[str, Any]:
        return invoke(service.project_proposals, request, project or {}, limit)

    @server.tool(name="story_books")
    def books() -> list[dict[str, Any]]: return invoke(service.list_books)

    @server.tool(name="story_open")
    def open_story(request: str, title: str = "", genre: str = "", chapter_count: int = 40, target_words: int = 2500, mode: str = "host") -> dict[str, Any]:
        return invoke(service.open_book, request, title=title, genre=genre, chapter_count=chapter_count, target_words=target_words, mode=mode)

    @server.tool(name="story_next")
    def next_task(book_id: str, worker_id: str = "host") -> dict[str, Any]: return invoke(service.next_task, book_id, worker_id=worker_id)

    @server.tool(name="story_import")
    def import_chapters(book_id: str, chapters: list[dict[str, Any]], expected_revision: int | None = None) -> dict[str, Any]:
        return invoke(service.import_chapters, book_id, chapters, expected_revision=expected_revision)

    @server.tool(name="story_submit")
    def submit(task_id: str, lease_id: str, result: dict[str, Any], worker_id: str = "host") -> dict[str, Any]:
        return invoke(service.submit_task, task_id, lease_id, result, worker_id=worker_id)

    @server.tool(name="story_query")
    def query(book_id: str, query: str, role: str = "author", through_chapter: int | None = None, limit: int = 10, task_id: str | None = None, lease_id: str | None = None) -> dict[str, Any]:
        return invoke(service.query, book_id, query, role=role, through_chapter=through_chapter, limit=limit, task_id=task_id, lease_id=lease_id)

    @server.tool(name="story_memory_facets")
    def memory_facets(book_id: str) -> dict[str, Any]:
        return {'facets': invoke(service.memory_facets, book_id)}

    @server.tool(name="story_status")
    def status(book_id: str) -> dict[str, Any]: return invoke(service.status, book_id)

    @server.tool(name="story_project")
    def project(book_id: str) -> dict[str, Any]: return {'project': invoke(service.project_metadata, book_id)}

    @server.tool(name="story_update_project")
    def update_project(book_id: str, metadata: dict[str, Any], expected_revision: int | None = None) -> dict[str, Any]:
        return invoke(service.update_project_metadata, book_id, metadata, expected_revision)

    @server.tool(name="story_report")
    def report(book_id: str) -> dict[str, Any]: return invoke(service.execution_report, book_id)

    @server.tool(name="story_reviews")
    def reviews(book_id: str, chapter_number: int | None = None, limit: int = 100) -> list[dict[str, Any]]:
        return invoke(service.review_history, book_id, chapter_number, limit)

    @server.tool(name="story_feedback")
    def feedback(book_id: str, chapter_number: int, reviewer_type: str, notes: str, reviewer_name: str = '',
                 verdict: str = 'revise', rating: int | None = None, would_continue: bool | None = None,
                 version_id: str | None = None) -> dict[str, Any]:
        return invoke(service.add_human_feedback, book_id, chapter_number, reviewer_type, notes, reviewer_name,
                      verdict, rating, would_continue, version_id)

    @server.tool(name="story_control")
    def control(book_id: str, action: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
        values = options or {}
        if action == "start": return invoke(service.start_run, book_id, **values)
        return invoke(service.control, book_id, action, **values)

    @server.tool(name="story_export")
    def export(book_id: str, allow_partial: bool = False) -> dict[str, Any]: return invoke(service.export, book_id, allow_partial=allow_partial)
    return server


def serve(root: str | Path | None = None) -> None:
    create_server(root).run(transport="stdio")


if __name__ == "__main__":
    serve()
