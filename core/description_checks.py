"""Check 5: description quality.

Scores whether an AI orchestrator could *pick* and *call* a tool correctly from
its declared metadata alone. This is heuristic, so the rubric is written out here
in full and the returned ``details`` break the score down component by component.
That makes every grade defensible: a caller can see exactly why a tool lost
points and what to fix.

Rubric (0-100 points), all pure functions of the tool's declared metadata:

    Has a non-blank description ............................ 40 pts
        The gate. Without any description an AI cannot know what the tool does.
        Scored all-or-nothing; the remaining components are moot without it.

    Description substance (word count) .................... 15 pts
        >= 8 meaningful words ....... 15
        4-7 words ...................  8
        1-3 words ...................  3
        A one-word description ("search") barely constrains tool choice.

    Parameters documented ................................. 25 pts
        Fraction of declared properties that either carry their own
        description or are named in the tool description, times 25.
        A tool with no parameters scores the full 25 (nothing to document).

    Name quality ......................................... 20 pts
        A descriptive, multi-token name ("get_user_balance") ... 20
        A short or generic name ("run", "tool", "do", "x") .....  5

The main entry point :func:`score_tool_description` is pure and takes a
normalized tool dict, so it unit-tests against hand-built inputs.
"""

from __future__ import annotations

import re
from typing import Any

from .models import CATEGORY_DESCRIPTION, CheckResult

# Names that convey nothing about what a tool does.
GENERIC_NAMES = {"tool", "run", "do", "call", "exec", "execute", "handler", "fn", "func", "x", "test"}

# Split camelCase and snake_case / kebab-case tool names into tokens.
_TOKEN_SPLIT = re.compile(r"[_\-\s]+|(?<=[a-z0-9])(?=[A-Z])")


def _tokenize_name(name: str) -> list[str]:
    return [t for t in _TOKEN_SPLIT.split(name) if t]


def _word_count(text: str) -> int:
    return len([w for w in re.split(r"\s+", text.strip()) if w])


def score_tool_description(tool: dict[str, Any]) -> CheckResult:
    """Score a single tool's name + description for AI usability.

    Args:
        tool: Normalized tool dict with ``name``, ``description``, ``inputSchema``.

    Returns:
        A :class:`CheckResult` in the ``description`` category. ``details``
        contains the per-component point breakdown.
    """
    name = tool.get("name", "") or ""
    description = (tool.get("description") or "").strip()
    schema = tool.get("inputSchema") or {}
    properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
    if not isinstance(properties, dict):
        properties = {}

    breakdown: dict[str, float] = {}

    # --- Component 1: has a description (gate) -------------------------------
    if not description:
        breakdown["has_description"] = 0.0
        return CheckResult(
            category=CATEGORY_DESCRIPTION,
            name=name or "<unnamed>",
            passed=False,
            score=0.0,
            note="Tool has no description; an AI cannot know what it does.",
            details={"breakdown": breakdown},
        )
    breakdown["has_description"] = 40.0

    # --- Component 2: description substance ----------------------------------
    words = _word_count(description)
    if words >= 8:
        breakdown["substance"] = 15.0
    elif words >= 4:
        breakdown["substance"] = 8.0
    else:
        breakdown["substance"] = 3.0

    # --- Component 3: parameters documented ----------------------------------
    if not properties:
        breakdown["params_documented"] = 25.0
        documented = 0
        total_params = 0
    else:
        total_params = len(properties)
        desc_lower = description.lower()
        documented = 0
        for prop_name, prop_schema in properties.items():
            has_own_desc = (
                isinstance(prop_schema, dict)
                and bool((prop_schema.get("description") or "").strip())
            )
            named_in_desc = prop_name.lower() in desc_lower
            if has_own_desc or named_in_desc:
                documented += 1
        breakdown["params_documented"] = 25.0 * (documented / total_params)

    # --- Component 4: name quality -------------------------------------------
    tokens = _tokenize_name(name)
    is_generic = name.lower() in GENERIC_NAMES
    if not is_generic and (len(tokens) >= 2 or len(name) >= 6):
        breakdown["name_quality"] = 20.0
    else:
        breakdown["name_quality"] = 5.0

    score = sum(breakdown.values())
    passed = score >= 60

    # Build a short human note pointing at the weakest components.
    weak: list[str] = []
    if breakdown["substance"] < 15:
        weak.append("description is thin")
    if properties and breakdown["params_documented"] < 25:
        weak.append(
            f"{documented}/{total_params} parameters documented"
        )
    if breakdown["name_quality"] < 20:
        weak.append("name is generic or too short")
    note = "Description is clear and complete." if not weak else "; ".join(weak)

    return CheckResult(
        category=CATEGORY_DESCRIPTION,
        name=name or "<unnamed>",
        passed=passed,
        score=score,
        note=note,
        details={"breakdown": {k: round(v, 1) for k, v in breakdown.items()}},
    )
