"""Tests for core.description_checks: the documented heuristic rubric."""

from core.description_checks import score_tool_description


def test_full_quality_description_scores_high():
    tool = {
        "name": "get_user_balance",
        "description": (
            "Return the current token balance for the given wallet address on "
            "the specified chain. Requires the wallet address and chain id."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "wallet address"},
                "chain": {"type": "string", "description": "chain id"},
            },
        },
    }
    result = score_tool_description(tool)
    assert result.passed is True
    assert result.score == 100.0


def test_no_description_is_hard_fail():
    tool = {"name": "get_thing", "description": "", "inputSchema": {}}
    result = score_tool_description(tool)
    assert result.passed is False
    assert result.score == 0.0
    assert "no description" in result.note.lower()


def test_missing_description_key_is_hard_fail():
    result = score_tool_description({"name": "x", "inputSchema": {}})
    assert result.passed is False
    assert result.score == 0.0


def test_thin_description_loses_substance_points():
    tool = {"name": "search_docs", "description": "Searches.", "inputSchema": {}}
    result = score_tool_description(tool)
    # 40 (has desc) + 3 (1-3 words) + 25 (no params) + 20 (good name) = 88
    assert result.score == 88.0
    assert "thin" in result.note


def test_undocumented_params_lose_points():
    tool = {
        "name": "transfer_funds",
        "description": "Move funds between two accounts as requested by caller.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "src": {"type": "string"},
                "dst": {"type": "string"},
            },
        },
    }
    result = score_tool_description(tool)
    # Neither 'src' nor 'dst' documented or named in the description -> 0/25.
    assert result.details["breakdown"]["params_documented"] == 0.0
    assert "0/2 parameters documented" in result.note


def test_param_named_in_description_counts_as_documented():
    tool = {
        "name": "lookup_symbol",
        "description": "Look up market data for the given symbol on the exchange.",
        "inputSchema": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
        },
    }
    result = score_tool_description(tool)
    assert result.details["breakdown"]["params_documented"] == 25.0


def test_generic_short_name_loses_name_points():
    tool = {
        "name": "run",
        "description": "Executes the configured job with the provided settings now.",
        "inputSchema": {},
    }
    result = score_tool_description(tool)
    assert result.details["breakdown"]["name_quality"] == 5.0
    assert "generic" in result.note
