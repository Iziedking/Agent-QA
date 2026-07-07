"""Tests for core.fuzz_checks: generation, classification, and the async probe."""

import anyio

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
    assert "wrong_types" in labels
    # No payload may carry a full set of valid-looking values, so the
    # "one field missing, the rest valid" case is deliberately not generated.
    assert "missing_one_required" not in labels
    # wrong_types must send each property a value its declared type forbids.
    wrong = dict(cases)["wrong_types"]
    assert not isinstance(wrong["symbol"], str)   # string field -> non-string
    assert not isinstance(wrong["limit"], int)    # integer field -> non-integer


def test_union_typed_property_never_gets_a_satisfying_value():
    # A union like ["string", "integer"] must not receive a value that satisfies
    # either member; otherwise a lax server would execute the real tool. The
    # probe must pick a type the union forbids (here a structural type).
    schema = {
        "type": "object",
        "properties": {"confirm": {"type": ["string", "integer"]}},
        "required": ["confirm"],
    }
    value = dict(generate_malformed_inputs(schema))["wrong_types"]["confirm"]
    assert not isinstance(value, str)
    assert not (isinstance(value, int) and not isinstance(value, bool))
    assert isinstance(value, (list, dict))


def test_property_accepting_every_type_is_not_fuzzed():
    # If a property's union covers every JSON type, no value is guaranteed to be
    # rejected, so we must not send one at all rather than risk a real call.
    schema = {
        "type": "object",
        "properties": {
            "anything": {
                "type": [
                    "string", "integer", "number",
                    "boolean", "object", "array", "null",
                ]
            }
        },
    }
    assert generate_malformed_inputs(schema) == []


def test_generate_never_sends_all_valid_values():
    # Every generated payload must violate the schema, so none may contain a
    # valid value for every required field.
    for _label, args in generate_malformed_inputs(SCHEMA):
        missing_required = any(r not in args for r in SCHEMA["required"])
        wrong_typed = "symbol" in args and not isinstance(args["symbol"], str)
        assert missing_required or wrong_typed


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


async def test_fuzz_skips_destructive_annotated_tool():
    session = FakeSession(call_behavior=crash)  # would fail if it called
    tool = {
        "name": "delete_everything",
        "inputSchema": SCHEMA,
        "annotations": {"destructiveHint": True},
    }
    result = await run_fuzz_check(session, tool)
    assert result.passed is True
    assert result.details.get("skipped") is True
    assert result.details.get("reason") == "destructive"
    assert session.call_log == []  # confirms nothing was sent


async def test_fuzz_times_out_a_hanging_tool(monkeypatch):
    import core.fuzz_checks as fz

    monkeypatch.setattr(fz, "PER_CALL_TIMEOUT", 0.05)

    class HangingSession:
        async def call_tool(self, name, arguments=None):
            await anyio.sleep(2)  # never returns within the timeout

    result = await run_fuzz_check(HangingSession(), TOOL)
    assert result.passed is False
    assert "crash" in result.note.lower()
