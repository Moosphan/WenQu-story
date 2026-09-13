import json
import os
import sys
from test_workflow import result_for

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def payload(result):
    assert not result.isError, result.content
    if result.structuredContent:
        return result.structuredContent.get("result", result.structuredContent)
    return json.loads(result.content[0].text)


@pytest.mark.asyncio
async def test_stdio_mcp_discovery_and_host_workflow(tmp_path):
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.getcwd()
    environment["HULK_STORY_HOME"] = str(tmp_path)
    server = StdioServerParameters(command=sys.executable, args=["-m", "story_core.mcp_server"], env=environment)
    async with stdio_client(server) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            names = {tool.name for tool in (await session.list_tools()).tools}
            assert {"story_capabilities", "story_open", "story_proposals", "story_next", "story_submit", "story_query", "story_memory_facets", "story_status", "story_project", "story_update_project", "story_control", "story_export", "story_feedback", "story_market_ideas"} <= names
            book = payload(await session.call_tool("story_open", {"request": "写一本都市悬疑", "title": "旧收音机", "chapter_count": 1}))
            book_id = book["book_id"]
            payload(await session.call_tool("story_control", {"book_id": book_id, "action": "start", "options": {"chapter_limit": 1}}))
            task = payload(await session.call_tool("story_next", {"book_id": book_id, "worker_id": "mcp-test"}))
            submitted = payload(await session.call_tool("story_submit", {"task_id": task["task_id"], "lease_id": task["lease_id"], "result": result_for(task), "worker_id": "mcp-test"}))
            assert submitted["accepted"] is True
            report = payload(await session.call_tool('story_report', {'book_id': book_id}))
            assert report['tasks']['submitted'] == 1
            assert report['literary_quality'] == 'not-human-validated'
            assert "hits" in payload(await session.call_tool("story_query", {"book_id": book_id, "query": "修理铺"}))
            assert payload(await session.call_tool('story_memory_facets', {'book_id': book_id})) == {'facets': {}}
            other = payload(await session.call_tool('story_open', {'request': '导入修仙试读', 'chapter_count': 40}))
            imported = payload(await session.call_tool('story_import', {'book_id': other['book_id'], 'chapters': [{'chapter_number': 1, 'title': '山门', 'body': '未审正文。'}]}))
            assert imported['imported'] == 1 and imported['reviewed'] is False


@pytest.mark.asyncio
async def test_stdio_mcp_reports_tool_failure_as_error(tmp_path):
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.getcwd()
    environment["HULK_STORY_HOME"] = str(tmp_path)
    server = StdioServerParameters(command=sys.executable, args=["-m", "story_core.mcp_server"], env=environment)
    async with stdio_client(server) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            result = await session.call_tool("story_status", {"book_id": "missing"})
            assert result.isError
            assert "NOT_FOUND" in result.content[0].text
