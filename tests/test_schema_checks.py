"""Tests for core.schema_checks: known-good and known-bad schemas."""

from core.schema_checks import check_tool_schema


def test_well_formed_schema_passes_full_marks():
    tool = {
        "name": "get_price",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "currency": {"type": "string"},
            },
            "required": ["symbol"],
        },
    }
    result = check_tool_schema(tool)
    assert result.passed is True
    assert result.score == 100.0
    assert result.category == "schema"


def test_no_arg_tool_with_empty_object_schema_passes():
    tool = {"name": "ping", "inputSchema": {"type": "object", "properties": {}}}
    result = check_tool_schema(tool)
    assert result.passed is True
    assert result.score == 100.0


def test_missing_schema_is_hard_fail():
    result = check_tool_schema({"name": "broken", "inputSchema": None})
    assert result.passed is False
    assert result.score == 0.0
    assert "no input schema" in result.note.lower()


def test_schema_not_an_object_fails():
    result = check_tool_schema({"name": "weird", "inputSchema": "just a string"})
    assert result.passed is False
    assert result.score == 0.0


def test_required_field_not_in_properties_is_fatal():
    tool = {
        "name": "bad_required",
        "inputSchema": {
            "type": "object",
            "properties": {"a": {"type": "string"}},
            "required": ["a", "ghost"],
        },
    }
    result = check_tool_schema(tool)
    assert result.passed is False
    assert "ghost" in result.note


def test_required_not_a_list_is_fatal():
    tool = {
        "name": "bad_required_type",
        "inputSchema": {
            "type": "object",
            "properties": {"a": {"type": "string"}},
            "required": "a",
        },
    }
    result = check_tool_schema(tool)
    assert result.passed is False


def test_missing_top_level_type_deducts_but_may_pass():
    tool = {
        "name": "no_type",
        "inputSchema": {"properties": {"a": {"type": "string"}}},
    }
    result = check_tool_schema(tool)
    # 20-point deduction, still above the 60 pass line.
    assert result.score == 80.0
    assert result.passed is True
    assert "type" in result.note.lower()


def test_property_with_invalid_type_is_noted():
    tool = {
        "name": "bad_prop",
        "inputSchema": {
            "type": "object",
            "properties": {"a": {"type": "stringy"}},
        },
    }
    result = check_tool_schema(tool)
    assert "invalid type" in result.note
    assert result.score < 100.0


def test_property_using_composition_not_penalized():
    tool = {
        "name": "compose",
        "inputSchema": {
            "type": "object",
            "properties": {"a": {"anyOf": [{"type": "string"}, {"type": "number"}]}},
        },
    }
    result = check_tool_schema(tool)
    assert result.score == 100.0
    assert result.passed is True
