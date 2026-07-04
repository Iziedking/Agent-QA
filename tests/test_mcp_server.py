"""Tests for the FastMCP server layer (Step 3).

Covers the tool's behavior and a self-consistency check: Agent QA's own tool
must pass Agent QA's own schema and description quality checks.
"""

import pytest

import mcp_server.server as srv
from core.connect import connection_success_result
from core.description_checks import score_tool_description
from core.models import CheckResult
from core.report import assemble_report
from core.schema_checks import check_tool_schema
from mcp_server.server import evaluate_mcp_endpoint, mcp


def _report(url):
    conn = connection_success_result("streamable-http", 1)
    latency = CheckResult("latency", "round-trip", True, 100.0, "fast")
    return assemble_report(url, conn, latency, tools=[])


async def test_tool_returns_report_dict(monkeypatch):
    async def fake_evaluate(url):
        return _report(url)

    monkeypatch.setattr(srv, "evaluate", fake_evaluate)

    result = await evaluate_mcp_endpoint("https://good.example/mcp")
    assert isinstance(result, dict)
    assert result["url"] == "https://good.example/mcp"
    assert result["grade"] in {"A", "B", "C", "D", "F"}
    assert "category_scores" in result


async def test_tool_rejects_bad_url():
    with pytest.raises(ValueError):
        await evaluate_mcp_endpoint("ftp://not-allowed")


async def test_tool_is_registered():
    tools = await mcp.get_tools()
    assert "evaluate_mcp_endpoint" in tools


async def _tool_as_dict():
    tools = await mcp.get_tools()
    mcp_tool = tools["evaluate_mcp_endpoint"].to_mcp_tool()
    return {
        "name": mcp_tool.name,
        "description": mcp_tool.description,
        "inputSchema": mcp_tool.inputSchema,
    }


async def test_own_tool_passes_description_check():
    tool = await _tool_as_dict()
    result = score_tool_description(tool)
    assert result.passed, f"Agent QA's own tool failed its description check: {result.note}"


async def test_own_tool_passes_schema_check():
    tool = await _tool_as_dict()
    result = check_tool_schema(tool)
    assert result.passed, f"Agent QA's own tool failed its schema check: {result.note}"
