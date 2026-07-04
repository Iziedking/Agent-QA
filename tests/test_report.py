"""Tests for core.report — grading, assembly, and end-to-end evaluate()."""

import contextlib

import core.report as report_mod
from core.models import CATEGORY_CONNECTION, CheckResult, ToolResult
from core.report import assemble_report, evaluate, letter_grade
from tests.fakes import FakeSession, accepts_invalid, clean_via_result


def test_letter_grade_bands():
    assert letter_grade(95) == "A"
    assert letter_grade(85) == "B"
    assert letter_grade(75) == "C"
    assert letter_grade(65) == "D"
    assert letter_grade(40) == "F"


def _connok(score=100.0):
    return CheckResult(CATEGORY_CONNECTION, "handshake", True, score, "ok")


def test_assemble_renormalizes_over_present_categories():
    # Only connection present (no tools, no latency) -> overall == connection.
    report = assemble_report("http://x", _connok(80.0), latency=None, tools=[])
    assert report.overall_score == 80.0
    assert report.reachable is True
    assert set(report.category_scores) == {CATEGORY_CONNECTION}


def test_assemble_collects_top_issues_sorted_by_severity():
    tool = ToolResult(
        "bad_tool",
        checks=[
            CheckResult("schema", "bad_tool", False, 0.0, "no schema"),
            CheckResult("description", "bad_tool", False, 30.0, "thin desc"),
            CheckResult("fuzz", "bad_tool", True, 100.0, "clean"),
        ],
    )
    report = assemble_report("http://x", _connok(), latency=None, tools=[tool])
    # Most severe (score 0 schema) first, then description (30).
    assert report.top_issues[0].startswith("[bad_tool] no schema")
    assert "thin desc" in report.top_issues[1]


def test_failed_connection_report_is_grade_f():
    from core.connect import connection_failed_result

    report = assemble_report(
        "http://x", connection_failed_result("boom"), latency=None, tools=[]
    )
    assert report.reachable is False
    assert report.grade == "F"
    assert report.overall_score == 0.0


def _patch_session(monkeypatch, session, transport="streamable-http"):
    @contextlib.asynccontextmanager
    async def fake_open(url, timeout=15.0):
        yield session, transport

    monkeypatch.setattr(report_mod, "open_mcp_session", fake_open)


async def test_evaluate_healthy_server(monkeypatch):
    tools = [
        {
            "name": "get_price",
            "description": "Return the latest price for the given symbol on the exchange.",
            "inputSchema": {
                "type": "object",
                "properties": {"symbol": {"type": "string", "description": "ticker"}},
                "required": ["symbol"],
            },
        }
    ]
    session = FakeSession(tools=tools, call_behavior=clean_via_result)
    _patch_session(monkeypatch, session)

    report = await evaluate("http://good.example/mcp")
    assert report.reachable is True
    assert report.grade in {"A", "B"}
    assert len(report.tools) == 1
    assert report.latency is not None
    # Every category should have run.
    assert set(report.category_scores) >= {
        "connection", "schema", "fuzz", "latency", "description",
    }


async def test_evaluate_flags_unreliable_server(monkeypatch):
    # Tool with no description, and a server that accepts invalid input.
    tools = [
        {"name": "x", "description": "", "inputSchema": {
            "type": "object",
            "properties": {"a": {"type": "string"}},
            "required": ["a"],
        }}
    ]
    session = FakeSession(tools=tools, call_behavior=accepts_invalid)
    _patch_session(monkeypatch, session)

    report = await evaluate("http://bad.example/mcp")
    assert report.reachable is True
    # Description (0) and fuzz (accepted invalid) both fail -> issues surface.
    assert any("description" in c.category for t in report.tools for c in t.checks)
    assert report.grade in {"C", "D", "F"}
    assert report.top_issues


async def test_evaluate_connection_failure_returns_report(monkeypatch):
    @contextlib.asynccontextmanager
    async def failing_open(url, timeout=15.0):
        raise ConnectionError("no route to host")
        yield  # pragma: no cover

    monkeypatch.setattr(report_mod, "open_mcp_session", failing_open)

    report = await evaluate("http://dead.example/mcp")
    assert report.reachable is False
    assert report.grade == "F"
    assert report.error is not None
