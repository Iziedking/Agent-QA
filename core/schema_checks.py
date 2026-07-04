"""Check 2: schema validity.

For each listed tool, confirm its declared input schema is a well-formed JSON
Schema object with sanely declared properties and required fields. A tool that
declares no schema, or an invalid one, is a real failure: it breaks AI callers
that rely on the schema to construct arguments.

The core function :func:`check_tool_schema` is pure. It takes a normalized tool
dict and returns a :class:`CheckResult`, so it can be unit-tested with hand-built
known-good and known-bad inputs, no live server required.
"""

from __future__ import annotations

from typing import Any

from .models import CATEGORY_SCHEMA, CheckResult

# The set of primitive types JSON Schema permits for the "type" keyword.
VALID_JSON_SCHEMA_TYPES = {
    "object",
    "array",
    "string",
    "number",
    "integer",
    "boolean",
    "null",
}


def _normalize_type(type_value: Any) -> list[str]:
    """Return the declared type(s) as a list, or [] if absent/unusable."""
    if isinstance(type_value, str):
        return [type_value]
    if isinstance(type_value, list):
        return [t for t in type_value if isinstance(t, str)]
    return []


def check_tool_schema(tool: dict[str, Any]) -> CheckResult:
    """Validate a single tool's input schema.

    Args:
        tool: Normalized tool dict with keys ``name`` and ``inputSchema``.

    Returns:
        A :class:`CheckResult` in the ``schema`` category. ``passed`` is False
        for a missing or structurally broken schema; ``score`` grades quality.
    """
    name = tool.get("name", "<unnamed>")
    schema = tool.get("inputSchema")

    # A missing schema is the most severe failure: AI callers have nothing to
    # build arguments from.
    if schema is None:
        return CheckResult(
            category=CATEGORY_SCHEMA,
            name=name,
            passed=False,
            score=0.0,
            note="Tool declares no input schema.",
        )

    if not isinstance(schema, dict):
        return CheckResult(
            category=CATEGORY_SCHEMA,
            name=name,
            passed=False,
            score=0.0,
            note=f"Input schema is not a JSON object (got {type(schema).__name__}).",
        )

    problems: list[str] = []
    score = 100.0

    # Top-level type. MCP tool input schemas describe an argument object, so the
    # canonical shape is {"type": "object", ...}. A missing type is tolerated by
    # some servers but weakens the contract.
    declared_types = _normalize_type(schema.get("type"))
    if not declared_types:
        problems.append("no top-level 'type' declared")
        score -= 20
    elif "object" not in declared_types:
        problems.append(f"top-level type is {declared_types}, expected 'object'")
        score -= 15

    # Properties. Must be a dict when present. It is legitimately absent/empty
    # for a no-argument tool, which is not penalized.
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        problems.append("'properties' is present but is not an object")
        score -= 25
        properties = {}
    else:
        for prop_name, prop_schema in properties.items():
            if not isinstance(prop_schema, dict):
                problems.append(f"property '{prop_name}' is not a schema object")
                score -= 10
                continue
            prop_types = _normalize_type(prop_schema.get("type"))
            # A property may use composition (anyOf/oneOf/allOf/$ref/enum)
            # instead of an explicit type; only flag when none of those exist.
            has_composition = any(
                k in prop_schema
                for k in ("anyOf", "oneOf", "allOf", "$ref", "enum", "const")
            )
            if not prop_types and not has_composition:
                problems.append(f"property '{prop_name}' declares no type")
                score -= 5
            else:
                for t in prop_types:
                    if t not in VALID_JSON_SCHEMA_TYPES:
                        problems.append(
                            f"property '{prop_name}' has invalid type '{t}'"
                        )
                        score -= 5

    # Required fields. Must be a list of strings, each referencing a declared
    # property. A required field with no matching property is a hard defect: a
    # caller cannot satisfy it.
    required = schema.get("required")
    fatal = False
    if required is not None:
        if not isinstance(required, list) or not all(
            isinstance(r, str) for r in required
        ):
            problems.append("'required' is not a list of field names")
            score -= 25
            fatal = True
        else:
            undeclared = [r for r in required if r not in properties]
            if undeclared:
                problems.append(
                    "required field(s) not declared in properties: "
                    + ", ".join(undeclared)
                )
                score -= 20
                fatal = True

    passed = not fatal and score >= 60
    note = "Schema is well-formed." if not problems else "; ".join(problems)
    return CheckResult(
        category=CATEGORY_SCHEMA,
        name=name,
        passed=passed,
        score=score,
        note=note,
        details={"problems": problems},
    )
