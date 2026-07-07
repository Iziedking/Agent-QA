"""Check 3: malformed-input handling (strictly read-only).

For each tool we send inputs that violate the tool's *own* declared schema:
missing required fields and wrong-typed values. A reliable server rejects these
cleanly, either a structured protocol error (JSON-RPC ``-32602 Invalid
params``) or a ``CallToolResult`` with ``isError=True``, without crashing and
without silently running as if the input were valid.

Safety model
------------
This is a probe of *error handling*, never an attack. Every payload violates the
schema in a way a correct server rejects **before** reaching any business logic:
an empty object that omits required fields, and a payload whose every field is a
wrong type. No payload carries a full set of valid-looking values, so it does not
look like a real call. Three further safeguards:

* No-argument tools are skipped: there is no schema-violating input to send.
* Tools the server explicitly annotates ``destructiveHint: true`` are skipped, so
  a server's own warning is respected.
* Each call is bounded by a timeout, so a tool that hangs on bad input is graded
  as a failure rather than stalling the run.

Pure helpers (:func:`generate_malformed_inputs`, :func:`classify_call_outcome`)
are unit-tested directly; :func:`run_fuzz_check` is the async orchestration.
"""

from __future__ import annotations

import os
from typing import Any

import anyio
from mcp.shared.exceptions import McpError

from .models import CATEGORY_FUZZ, CheckResult

# Per-call timeout, seconds. A tool that does not answer a malformed call within
# this window is treated as failing to reject cleanly.
PER_CALL_TIMEOUT = float(os.environ.get("AGENT_QA_CALL_TIMEOUT", "20"))

# Cap on how many of a tool's properties we build a payload from, so a hostile
# server that lists a huge property set cannot pin a worker on payload building.
MAX_PROPERTIES = int(os.environ.get("AGENT_QA_MAX_PROPERTIES", "200"))

# Marker: this property has no value we can send that is guaranteed to violate
# its declared type, so we must not send one (see ``_wrong_value``).
_NO_WRONG_VALUE = object()

# Candidate wrong values, each tagged with its JSON Schema type, tried in order.
# We send a value whose type is excluded by every type the property declares, so
# the payload is guaranteed type-invalid and a compliant server rejects it before
# any business logic runs. Structural types (array, object) come first because
# real tool inputs rarely permit them, which keeps the probe unmistakably wrong.
_PROBE_BY_TYPE: tuple[tuple[str, Any], ...] = (
    ("array", ["agent-qa-invalid-probe"]),
    ("object", {"agent_qa_invalid_probe": True}),
    ("string", "agent-qa-invalid-probe"),
    ("boolean", False),
    ("null", None),
    ("number", 0.5),
    ("integer", 987654321),
)


def _declared_types(prop_schema: Any) -> set[str] | None:
    """The set of JSON Schema types a property declares, or None if untyped."""
    if not isinstance(prop_schema, dict):
        return None
    t = prop_schema.get("type")
    if isinstance(t, str):
        return {t}
    if isinstance(t, list):
        names = {item for item in t if isinstance(item, str)}
        return names or None
    return None


def _wrong_value(prop_schema: Any) -> Any:
    """A value whose JSON type no declared type of the property permits.

    Returns :data:`_NO_WRONG_VALUE` when the property is untyped (any value could
    be valid) or its declared types cover every JSON type, so no value is
    guaranteed to be rejected. This is what keeps the probe strictly read-only: a
    union like ``["string", "integer"]`` must never receive a value that happens
    to satisfy one of its members, which a lax server could then execute for real.
    """
    declared = _declared_types(prop_schema)
    if declared is None:
        return _NO_WRONG_VALUE
    for type_name, value in _PROBE_BY_TYPE:
        if type_name in declared:
            continue
        # An integer literal also satisfies a "number" schema, so only offer the
        # integer probe when neither integer nor number is permitted.
        if type_name == "integer" and "number" in declared:
            continue
        return value
    return _NO_WRONG_VALUE


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

    # 1. Omit all required fields (empty object). Violates "required". This
    #    already exercises the requiredness check; we deliberately do not send a
    #    "one field missing, the rest valid" payload, because that would carry
    #    real, valid-looking values and could be executed by a server that does
    #    not enforce its own required list.
    if required:
        _add("missing_all_required", {})

    # 2. Wrong types: fill each declared property with a value whose type the
    #    property forbids. Properties with no safely-violating value (untyped, or
    #    a union that accepts every type) are skipped, so the payload is always
    #    guaranteed type-invalid and never a real-looking, executable call.
    wrong_typed: dict[str, Any] = {}
    for prop_name, prop_schema in list(properties.items())[:MAX_PROPERTIES]:
        value = _wrong_value(prop_schema)
        if value is not _NO_WRONG_VALUE:
            wrong_typed[prop_name] = value
    if wrong_typed:
        _add("wrong_types", wrong_typed)

    return cases


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

    # Respect a server's own warning: never fuzz a tool it marks destructive.
    annotations = tool.get("annotations")
    if isinstance(annotations, dict) and annotations.get("destructiveHint") is True:
        return CheckResult(
            category=CATEGORY_FUZZ,
            name=name,
            passed=True,
            score=100.0,
            note="Tool is annotated destructive; not fuzzed.",
            details={"skipped": True, "reason": "destructive"},
        )

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
            with anyio.fail_after(PER_CALL_TIMEOUT):
                result = await session.call_tool(name, arguments=args)
            is_error_result = bool(getattr(result, "isError", False))
        except McpError:
            raised_mcp = True
        except Exception:  # noqa: BLE001 - non-MCP error or timeout counts as a crash
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
