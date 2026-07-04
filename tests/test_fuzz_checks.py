"""Tests for core.fuzz_checks — generation, classification, and the async probe."""

from core.fuzz_checks import (
    classify_call_outcome,
    generate_malformed_inputs,
    run_fuzz_check,
)
from tests.fakes import (
    FakeSession,
    accepts_invalid,
    clean_via_mcp_error,
    clean_via_result,
    crash,
)

SCHEMA = {
    "type": "object",
    "properties": {
        "symbol": {"type": "string"},
        "limit": {"type": "integer"},
    },
    "required": ["symbol", "limit"],
}


def test_generate_includes_missing_required_and_wrong_types():
    cases = generate_malformed_inputs(SCHEMA)
    labels = {label for label, _ in cases}
    assert "missing_all_required" in labels
    assert "missing_one_required" in labels
    assert "wrong_types" in labels
    # wrong_types must actually invert each property's type.
    wrong = dict(cases)["wrong_types"]
    assert isinstance(wrong["symbol"], int)      # string -> int
    assert isinstance(wrong["limit"], str)       # integer -> str


def test_generate_no_args_tool_yields_nothing():
    assert generate_malformed_inputs({"type": "object", "properties": {}}) == []
    assert generate_malformed_inputs(None) == []


def test_generate_optional_only_still_fuzzes_types():
    schema = {"type": "object", "properties": {"q": {"type": "string"}}}
    cases = generate_malformed_inputs(schema)
    labels = {label for label, _ in cases}
    assert labels == {"wrong_types"}


def test_classify_all_branches():
    assert classify_call_outcome(
        raised_mcp_error=True, raised_other=False, is_error_result=None
    ) == ("clean_error", True)
    assert classify_call_outcome(
        raised_mcp_error=False, raised_other=True, is_error_result=None
    ) == ("crash", False)
    assert classify_call_outcome(
        raised_mcp_error=False, raised_other=False, is_error_result=True
    ) == ("clean_error", True)
    assert classify_call_outcome(
        raised_mcp_error=False, raised_other=False, is_error_result=False
    ) == ("accepted_invalid", False)


TOOL = {"name": "get_price", "inputSchema": SCHEMA}


async def test_fuzz_clean_via_error_result_passes():
    session = FakeSession(call_behavior=clean_via_result)
    result = await run_fuzz_check(session, TOOL)
    assert result.passed is True
    assert result.score == 100.0
    assert "cleanly" in result.note


async def test_fuzz_clean_via_mcp_error_passes():
    session = FakeSession(call_behavior=clean_via_mcp_error)
    result = await run_fuzz_check(session, TOOL)
    assert result.passed is True
    assert result.score == 100.0


async def test_fuzz_crash_fails():
    session = FakeSession(call_behavior=crash)
    result = await run_fuzz_check(session, TOOL)
    assert result.passed is False
    assert "crash" in result.note.lower()


async def test_fuzz_accepts_invalid_fails():
    session = FakeSession(call_behavior=accepts_invalid)
    result = await run_fuzz_check(session, TOOL)
    assert result.passed is False
    assert "silently accepted" in result.note


async def test_fuzz_no_arg_tool_skipped_not_penalized():
    session = FakeSession(call_behavior=crash)  # would fail if it called
    tool = {"name": "ping", "inputSchema": {"type": "object", "properties": {}}}
    result = await run_fuzz_check(session, tool)
    assert result.passed is True
    assert result.score == 100.0
    assert result.details.get("skipped") is True
    assert session.call_log == []  # confirms nothing was sent
