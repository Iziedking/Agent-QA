"""Check 3: malformed-input handling (strictly read-only).

For each tool we send inputs that violate the tool's *own* declared schema:
missing required fields and wrong-typed values. A reliable server rejects these
cleanly, either a structured protocol error (JSON-RPC ``-32602 Invalid
params``) or a ``CallToolResult`` with ``isError=True``, without crashing and
without silently running as if the input were valid.

Safety model
------------
This is a probe of *error handling*, never an attack. We only ever send inputs
that a correct server rejects **before** reaching any business logic, so no
side-effecting code path is exercised. For a no-argument tool there is no way to
construct a schema-violating input without inventing data that might be accepted
and acted upon, so such tools are skipped and reported as "not fuzzable", rather
than probed unsafely.

Pure helpers (:func:`generate_malformed_inputs`, :func:`classify_call_outcome`)
are unit-tested directly; :func:`run_fuzz_check` is the async orchestration.
"""

from __future__ import annotations

from typing import Any

from mcp.shared.exceptions import McpError

from .models import CATEGORY_FUZZ, CheckResult

# For a declared property type, a value of a deliberately wrong type. Chosen so
# every value is a plain JSON scalar that only violates the *type* contract.
_WRONG_VALUE_FOR_TYPE: dict[str, Any] = {
    "string": 123456,               # number where string expected
    "integer": "not_an_integer",    # string where integer expected
    "number": "not_a_number",       # string where number expected
    "boolean": "not_a_boolean",     # string where boolean expected
    "object": "not_an_object",      # string where object expected
    "array": "not_an_array",        # string where array expected
    "null": "not_null",             # string where null expected
}


def _first_declared_type(prop_schema: Any) -> str | None:
    if not isinstance(prop_schema, dict):
        return None
    t = prop_schema.get("type")
    if isinstance(t, str):
        return t
    if isinstance(t, list):
        for item in t:
            if isinstance(item, str) and item != "null":
                return item
    return None


def generate_malformed_inputs(schema: Any) -> list[tuple[str, dict[str, Any]]]:
    """Build schema-violating argument payloads for a tool.

    Args:
        schema: The tool's ``inputSchema`` (a JSON Schema object) or None.

    Returns:
        A list of ``(label, arguments)`` pairs, each of which violates the
        schema and should therefore be rejected. Empty if the schema declares
        no constraints that can be safely violated (e.g. a no-argument tool).
    """
    if not isinstance(schema, dict):
        return []

    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        properties = {}
    required = schema.get("required", [])
    if not isinstance(required, list):
        required = []
    required = [r for r in required if isinstance(r, str)]

    cases: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()

    def _add(label: str, args: dict[str, Any]) -> None:
        key = repr(sorted(args.items()))
        if key not in seen:
            seen.add(key)
            cases.append((label, args))

    # 1. Omit all required fields (empty object). Violates "required".
    if required:
        _add("missing_all_required", {})

    # 2. Omit a single required field while supplying dummy others. Isolates
    #    the requiredness check from any all-empty short-circuit.
    if len(required) >= 2:
        partial: dict[str, Any] = {}
        for r in required[1:]:
            partial[r] = _placeholder_for(properties.get(r))
        _add("missing_one_required", partial)

    # 3. Wrong types: fill every declared property with a wrong-typed value.
    wrong_typed: dict[str, Any] = {}
    for prop_name, prop_schema in properties.items():
        declared = _first_declared_type(prop_schema)
        if declared in _WRONG_VALUE_FOR_TYPE:
            wrong_typed[prop_name] = _WRONG_VALUE_FOR_TYPE[declared]
    if wrong_typed:
        _add("wrong_types", wrong_typed)

    return cases


def _placeholder_for(prop_schema: Any) -> Any:
    """A schema-*valid* placeholder value, used to fill non-target fields."""
    declared = _first_declared_type(prop_schema)
    return {
        "string": "x",
        "integer": 1,
        "number": 1,
        "boolean": True,
        "object": {},
        "array": [],
        "null": None,
    }.get(declared, "x")


def classify_call_outcome(
    *,
    raised_mcp_error: bool,
    raised_other: bool,
    is_error_result: bool | None,
) -> tuple[str, bool]:
    """Classify the outcome of one malformed call.

    Args:
        raised_mcp_error: The call raised a structured MCP/JSON-RPC error.
        raised_other: The call raised some other (non-MCP) exception.
        is_error_result: For a returned ``CallToolResult``, its ``isError``
            flag; None if the call raised instead of returning.

    Returns:
        ``(outcome, handled_cleanly)``. ``outcome`` is one of
        ``clean_error`` / ``crash`` / ``accepted_invalid``.
    """
    if raised_mcp_error:
        # A structured "invalid params" rejection is exactly right.
        return ("clean_error", True)
    if raised_other:
        # A transport error / unhandled exception is a reliability failure.
        return ("crash", False)
    if is_error_result:
        # Tool-level error result is also a clean rejection.
        return ("clean_error", True)
    # The server returned a *successful* result for invalid input: it ignored
    # its own schema. That is a silent-wrong-answer risk.
    return ("accepted_invalid", False)


async def run_fuzz_check(session: Any, tool: dict[str, Any]) -> CheckResult:
    """Send malformed inputs to one tool and grade how cleanly it rejects them.

    Args:
        session: An initialized MCP ``ClientSession`` (or compatible stub with
            an async ``call_tool(name, arguments=...)``).
        tool: Normalized tool dict with ``name`` and ``inputSchema``.

    Returns:
        A :class:`CheckResult` in the ``fuzz`` category.
    """
    name = tool.get("name", "<unnamed>")
    cases = generate_malformed_inputs(tool.get("inputSchema"))

    if not cases:
        # Nothing can be safely violated (e.g. a no-argument tool). Report
        # honestly rather than probing unsafely; do not penalize the server.
        return CheckResult(
            category=CATEGORY_FUZZ,
            name=name,
            passed=True,
            score=100.0,
            note="No constrainable inputs to fuzz safely; skipped.",
            details={"skipped": True},
        )

    outcomes: list[dict[str, Any]] = []
    for label, args in cases:
        raised_mcp = raised_other = False
        is_error_result: bool | None = None
        try:
            result = await session.call_tool(name, arguments=args)
            is_error_result = bool(getattr(result, "isError", False))
        except McpError:
            raised_mcp = True
        except Exception:  # noqa: BLE001 - any non-MCP error counts as a crash
            raised_other = True

        outcome, ok = classify_call_outcome(
            raised_mcp_error=raised_mcp,
            raised_other=raised_other,
            is_error_result=is_error_result,
        )
        outcomes.append({"input": label, "outcome": outcome, "clean": ok})

    total = len(outcomes)
    clean = sum(1 for o in outcomes if o["clean"])
    crashes = sum(1 for o in outcomes if o["outcome"] == "crash")
    accepted = sum(1 for o in outcomes if o["outcome"] == "accepted_invalid")

    score = 100.0 * clean / total
    # A hard fail if it crashed on any input; otherwise pass only if it handled
    # a clear majority cleanly.
    passed = crashes == 0 and score >= 70

    if clean == total:
        note = f"Rejected all {total} malformed inputs cleanly."
    else:
        parts = []
        if crashes:
            parts.append(f"{crashes} crash(es)")
        if accepted:
            parts.append(f"{accepted} silently accepted invalid input")
        note = f"{clean}/{total} handled cleanly; " + ", ".join(parts) + "."

    return CheckResult(
        category=CATEGORY_FUZZ,
        name=name,
        passed=passed,
        score=score,
        note=note,
        details={"outcomes": outcomes},
    )
