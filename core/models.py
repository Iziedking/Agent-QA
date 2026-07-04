"""Shared result types for Agent QA.

Every check in the engine returns a :class:`CheckResult`. Per-tool checks are
grouped into a :class:`ToolResult`, and the whole run is assembled into a
:class:`Report`. Each type renders both as a plain ``dict`` (for JSON / machine
consumers) and as readable text (for humans and the demo).

Scores are on a 0-100 scale throughout so they compose cleanly into category
averages and an overall grade.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Category identifiers, used as dict keys in the assembled report. Kept as
# constants so checks and the report layer cannot drift apart on spelling.
CATEGORY_CONNECTION = "connection"
CATEGORY_SCHEMA = "schema"
CATEGORY_FUZZ = "fuzz"
CATEGORY_LATENCY = "latency"
CATEGORY_DESCRIPTION = "description"

CATEGORIES = (
    CATEGORY_CONNECTION,
    CATEGORY_SCHEMA,
    CATEGORY_FUZZ,
    CATEGORY_LATENCY,
    CATEGORY_DESCRIPTION,
)

# Human-facing labels for each category.
CATEGORY_LABELS = {
    CATEGORY_CONNECTION: "Connection & handshake",
    CATEGORY_SCHEMA: "Schema validity",
    CATEGORY_FUZZ: "Malformed-input handling",
    CATEGORY_LATENCY: "Latency",
    CATEGORY_DESCRIPTION: "Description quality",
}


def clamp_score(value: float) -> float:
    """Clamp a raw score into the valid 0-100 range."""
    return max(0.0, min(100.0, float(value)))


@dataclass
class CheckResult:
    """The result of a single reliability check.

    Attributes:
        category: One of :data:`CATEGORIES`.
        name: Short label for this specific check (e.g. a tool name).
        passed: Boolean pass/fail. A check can score below 100 and still pass;
            ``passed`` marks the hard threshold, ``score`` the graded quality.
        score: Quality on a 0-100 scale.
        note: One-line, human-readable explanation of the outcome.
        details: Optional structured extras (percentiles, per-input outcomes).
    """

    category: str
    name: str
    passed: bool
    score: float
    note: str
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.score = clamp_score(self.score)

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "name": self.name,
            "passed": self.passed,
            "score": round(self.score, 1),
            "note": self.note,
            "details": self.details,
        }


@dataclass
class ToolResult:
    """All per-tool checks for a single tool on the target server."""

    tool_name: str
    checks: list[CheckResult] = field(default_factory=list)

    def score(self) -> float:
        """Mean score across this tool's checks (100 if it has none)."""
        if not self.checks:
            return 100.0
        return clamp_score(sum(c.score for c in self.checks) / len(self.checks))

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "score": round(self.score(), 1),
            "checks": [c.to_dict() for c in self.checks],
        }


@dataclass
class Report:
    """The complete reliability report for one MCP endpoint."""

    url: str
    reachable: bool
    connection: CheckResult
    latency: CheckResult | None = None
    tools: list[ToolResult] = field(default_factory=list)
    category_scores: dict[str, float] = field(default_factory=dict)
    overall_score: float = 0.0
    grade: str = "F"
    top_issues: list[str] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "reachable": self.reachable,
            "grade": self.grade,
            "overall_score": round(self.overall_score, 1),
            "category_scores": {
                k: round(v, 1) for k, v in self.category_scores.items()
            },
            "connection": self.connection.to_dict(),
            "latency": self.latency.to_dict() if self.latency else None,
            "tools": [t.to_dict() for t in self.tools],
            "top_issues": self.top_issues,
            "error": self.error,
        }

    def to_text(self) -> str:
        """Render the report as readable plain text for humans / the demo."""
        lines: list[str] = []
        lines.append("=" * 60)
        lines.append(f"Agent QA reliability report")
        lines.append(f"Target: {self.url}")
        lines.append("=" * 60)
        lines.append(f"Overall grade: {self.grade}  ({self.overall_score:.0f}/100)")
        lines.append(f"Reachable: {'yes' if self.reachable else 'no'}")
        if self.error:
            lines.append(f"Error: {self.error}")

        lines.append("")
        lines.append("Category scores")
        lines.append("-" * 60)
        for cat in CATEGORIES:
            if cat in self.category_scores:
                label = CATEGORY_LABELS.get(cat, cat)
                lines.append(f"  {label:<28} {self.category_scores[cat]:>5.0f}/100")

        lines.append("")
        lines.append(f"Connection: {self.connection.note}")
        if self.latency:
            lines.append(f"Latency:    {self.latency.note}")

        if self.tools:
            lines.append("")
            lines.append("Per-tool results")
            lines.append("-" * 60)
            for tool in self.tools:
                lines.append(f"  {tool.tool_name}  ({tool.score():.0f}/100)")
                for check in tool.checks:
                    mark = "PASS" if check.passed else "FAIL"
                    label = CATEGORY_LABELS.get(check.category, check.category)
                    lines.append(f"    [{mark}] {label}: {check.note}")

        if self.top_issues:
            lines.append("")
            lines.append("Top issues")
            lines.append("-" * 60)
            for i, issue in enumerate(self.top_issues, 1):
                lines.append(f"  {i}. {issue}")

        lines.append("=" * 60)
        return "\n".join(lines)
