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
from mcp_server.server import evaluate_mcp_endpoint, mcp, recall_tool_reputation


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


async def test_evaluate_remembers_the_verdict(monkeypatch):
    async def fake_evaluate(url):
        return _report(url)

    remembered = {}
    monkeypatch.setattr(srv, "evaluate", fake_evaluate)
    monkeypatch.setattr(srv, "remember_verdict", lambda report: remembered.update(url=report.url))

    await evaluate_mcp_endpoint("https://good.example/mcp")
    assert remembered.get("url") == "https://good.example/mcp"


async def test_recall_tool_returns_records(monkeypatch):
    async def fake_recall(query, limit=6):
        return {"query": query, "enabled": True, "records": ["svc graded A 92/100"]}

    monkeypatch.setattr(srv, "recall_reputation", fake_recall)
    out = await recall_tool_reputation("is svc reliable")
    assert out["records"] == ["svc graded A 92/100"]
    assert out["memory_enabled"] is True
    assert "Found" in out["note"]


async def test_recall_tool_graceful_when_memory_disabled(monkeypatch):
    async def fake_recall(query, limit=6):
        return {"query": query, "enabled": False, "records": []}

    monkeypatch.setattr(srv, "recall_reputation", fake_recall)
    out = await recall_tool_reputation("anything")
    assert out["memory_enabled"] is False
    assert "not configured" in out["note"]


async def test_tool_is_registered():
    tools = await mcp.get_tools()
    assert "evaluate_mcp_endpoint" in tools
    assert "recall_tool_reputation" in tools


async def _tool_as_dict(name="evaluate_mcp_endpoint"):
    tools = await mcp.get_tools()
    mcp_tool = tools[name].to_mcp_tool()
    return {
        "name": mcp_tool.name,
        "description": mcp_tool.description,
        "inputSchema": mcp_tool.inputSchema,
    }


@pytest.mark.parametrize("name", ["evaluate_mcp_endpoint", "recall_tool_reputation"])
async def test_own_tools_pass_description_check(name):
    tool = await _tool_as_dict(name)
    result = score_tool_description(tool)
    assert result.passed, f"Agent QA tool {name} failed its own description check: {result.note}"


@pytest.mark.parametrize("name", ["evaluate_mcp_endpoint", "recall_tool_reputation"])
async def test_own_tools_pass_schema_check(name):
    tool = await _tool_as_dict(name)
    result = check_tool_schema(tool)
    assert result.passed, f"Agent QA tool {name} failed its own schema check: {result.note}"
