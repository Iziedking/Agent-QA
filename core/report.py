"""Report assembly and the top-level ``evaluate`` entry point.

This ties the five checks together into a single :class:`Report`: per-tool
results, per-category scores, a weighted overall score, an overall letter grade,
and a ranked list of the top issues found. The assembly (:func:`assemble_report`,
:func:`letter_grade`) is pure so it unit-tests against constructed check results;
:func:`evaluate` is the async orchestration that drives a live endpoint.
"""

from __future__ import annotations

from statistics import mean
from typing import Any

from .connect import (
    connection_failed_result,
    connection_success_result,
    open_mcp_session,
    tool_to_dict,
)
from .description_checks import score_tool_description
from .fuzz_checks import run_fuzz_check
from .latency_checks import run_latency_check
from .models import (
    CATEGORY_CONNECTION,
    CATEGORY_DESCRIPTION,
    CATEGORY_FUZZ,
    CATEGORY_LATENCY,
    CATEGORY_SCHEMA,
    CheckResult,
    Report,
    ToolResult,
    clamp_score,
)
from .schema_checks import check_tool_schema

# Weight of each category in the overall score. When a category did not run
# (e.g. no tools, so no schema/fuzz/description data), its weight is dropped and
# the remainder is renormalized, so the overall score is always over what was
# actually measured.
CATEGORY_WEIGHTS = {
    CATEGORY_CONNECTION: 0.15,
    CATEGORY_SCHEMA: 0.25,
    CATEGORY_FUZZ: 0.25,
    CATEGORY_LATENCY: 0.15,
    CATEGORY_DESCRIPTION: 0.20,
}


def letter_grade(score: float) -> str:
    """Map a 0-100 overall score to a letter grade."""
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    if score >= 60:
        return "D"
    return "F"


def _mean_score(checks: list[CheckResult]) -> float | None:
    if not checks:
        return None
    return clamp_score(mean(c.score for c in checks))


def assemble_report(
    url: str,
    connection: CheckResult,
    latency: CheckResult | None,
    tools: list[ToolResult],
) -> Report:
    """Assemble check results into a finished :class:`Report`.

    Pure: given the already-run checks it computes category scores, the weighted
    overall score, the letter grade, and the ranked top issues.
    """
    reachable = connection.passed

    # Gather per-category check lists from the per-tool results.
    schema_checks: list[CheckResult] = []
    fuzz_checks: list[CheckResult] = []
    description_checks: list[CheckResult] = []
    for tool in tools:
        for check in tool.checks:
            if check.category == CATEGORY_SCHEMA:
                schema_checks.append(check)
            elif check.category == CATEGORY_FUZZ:
                fuzz_checks.append(check)
            elif check.category == CATEGORY_DESCRIPTION:
                description_checks.append(check)

    category_scores: dict[str, float] = {CATEGORY_CONNECTION: connection.score}
    schema_score = _mean_score(schema_checks)
    if schema_score is not None:
        category_scores[CATEGORY_SCHEMA] = schema_score
    fuzz_score = _mean_score(fuzz_checks)
    if fuzz_score is not None:
        category_scores[CATEGORY_FUZZ] = fuzz_score
    desc_score = _mean_score(description_checks)
    if desc_score is not None:
        category_scores[CATEGORY_DESCRIPTION] = desc_score
    if latency is not None:
        category_scores[CATEGORY_LATENCY] = latency.score

    # Weighted overall, renormalized over the categories that actually ran.
    present_weight = sum(
        CATEGORY_WEIGHTS[c] for c in category_scores if c in CATEGORY_WEIGHTS
    )
    if present_weight > 0:
        overall = sum(
            category_scores[c] * CATEGORY_WEIGHTS[c]
            for c in category_scores
            if c in CATEGORY_WEIGHTS
        ) / present_weight
    else:
        overall = 0.0
    overall = clamp_score(overall)

    # Rank top issues: every failing check, most severe (lowest score) first.
    all_checks: list[CheckResult] = [connection]
    if latency is not None:
        all_checks.append(latency)
    for tool in tools:
        all_checks.extend(tool.checks)

    failing = sorted(
        (c for c in all_checks if not c.passed), key=lambda c: c.score
    )
    top_issues = [f"[{c.name}] {c.note}" for c in failing[:5]]

    return Report(
        url=url,
        reachable=reachable,
        connection=connection,
        latency=latency,
        tools=tools,
        category_scores=category_scores,
        overall_score=overall,
        grade=letter_grade(overall),
        top_issues=top_issues,
    )


async def evaluate(url: str) -> Report:
    """Evaluate a live MCP endpoint and return a full reliability report.

    Opens a real session, runs all five checks, and assembles the report. A
    connection failure yields a well-formed report with an ``F`` grade rather
    than raising, so callers (the API, the MCP tool) always get a report back.
    """
    try:
        async with open_mcp_session(url) as (session, transport):
            tools_resp = await session.list_tools()
            raw_tools = list(getattr(tools_resp, "tools", []) or [])
            tools = [tool_to_dict(t) for t in raw_tools]

            connection = connection_success_result(transport, len(tools))
            latency = await run_latency_check(session)

            tool_results: list[ToolResult] = []
            for tool in tools:
                checks = [
                    check_tool_schema(tool),
                    score_tool_description(tool),
                    await run_fuzz_check(session, tool),
                ]
                tool_results.append(
                    ToolResult(tool_name=tool.get("name") or "<unnamed>", checks=checks)
                )

            return assemble_report(url, connection, latency, tool_results)

    except Exception as exc:  # noqa: BLE001 - surface as a failed report
        connection = connection_failed_result(f"{type(exc).__name__}: {exc}")
        report = assemble_report(url, connection, latency=None, tools=[])
        report.error = str(exc)
        return report


def evaluate_sync(url: str) -> Report:
    """Blocking convenience wrapper around :func:`evaluate`."""
    import anyio

    return anyio.run(evaluate, url)
