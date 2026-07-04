"""Check 4 — Latency under repetition.

Times repeated round-trips to the server and reports p50 / p95. Slow or wildly
variable latency is a reliability signal AI orchestrators care about: a tool that
usually answers in 100 ms but occasionally takes 8 s will stall agent pipelines.

To stay strictly read-only (never triggering a tool's business logic or side
effects) the probe times ``list_tools()`` round-trips. That still exercises the
full request/response path — transport, framing, serialization, the server's
request handler — without invoking any listed tool.

The math (:func:`compute_percentiles`) and the grading (:func:`grade_latency`)
are pure and unit-tested. :func:`run_latency_check` is the thin async wrapper
that gathers the samples from a live session.
"""

from __future__ import annotations

import time
from typing import Any

from .models import CATEGORY_LATENCY, CheckResult

# Grading thresholds on p95, in milliseconds. Documented so the grade is
# defensible and tunable. Each entry is (max_p95_ms, score, label).
LATENCY_BANDS = (
    (300.0, 100.0, "excellent"),
    (800.0, 85.0, "good"),
    (2000.0, 65.0, "acceptable"),
    (5000.0, 40.0, "slow"),
    (float("inf"), 15.0, "very slow"),
)

# p95 at or below this passes the hard reliability gate.
PASS_P95_MS = 2000.0

# If p95 exceeds this multiple of p50, latency is unstable even when fast.
VARIANCE_RATIO = 4.0
VARIANCE_PENALTY = 15.0


def compute_percentiles(samples: list[float]) -> dict[str, float]:
    """Return p50 and p95 (nearest-rank) for a list of samples.

    Args:
        samples: Measured durations (any unit). Must be non-empty.

    Returns:
        Dict with ``p50``, ``p95``, ``min``, ``max``, ``mean`` keys.

    Raises:
        ValueError: If ``samples`` is empty.
    """
    if not samples:
        raise ValueError("compute_percentiles requires at least one sample")

    ordered = sorted(samples)
    n = len(ordered)

    def _percentile(pct: float) -> float:
        # Nearest-rank method: rank = ceil(pct/100 * n), 1-indexed.
        if n == 1:
            return ordered[0]
        rank = max(1, min(n, int(-(-pct * n // 100))))  # ceil via floor trick
        return ordered[rank - 1]

    return {
        "p50": _percentile(50),
        "p95": _percentile(95),
        "min": ordered[0],
        "max": ordered[-1],
        "mean": sum(ordered) / n,
    }


def grade_latency(p50_ms: float, p95_ms: float, rounds: int) -> CheckResult:
    """Grade latency from p50 / p95 in milliseconds.

    Args:
        p50_ms: Median round-trip time in milliseconds.
        p95_ms: 95th-percentile round-trip time in milliseconds.
        rounds: How many samples were taken (recorded for context).

    Returns:
        A :class:`CheckResult` in the ``latency`` category.
    """
    score = 15.0
    label = "very slow"
    for max_p95, band_score, band_label in LATENCY_BANDS:
        if p95_ms <= max_p95:
            score = band_score
            label = band_label
            break

    # Penalize instability: a large p95/p50 spread means unpredictable tails.
    unstable = p50_ms > 0 and p95_ms > VARIANCE_RATIO * p50_ms
    if unstable:
        score = max(0.0, score - VARIANCE_PENALTY)

    passed = p95_ms <= PASS_P95_MS
    note = (
        f"p50 {p50_ms:.0f} ms, p95 {p95_ms:.0f} ms over {rounds} calls ({label})"
    )
    if unstable:
        note += "; high variance (p95 >> p50)"

    return CheckResult(
        category=CATEGORY_LATENCY,
        name="round-trip",
        passed=passed,
        score=score,
        note=note,
        details={
            "p50_ms": round(p50_ms, 1),
            "p95_ms": round(p95_ms, 1),
            "rounds": rounds,
            "band": label,
            "unstable": unstable,
        },
    )


async def run_latency_check(session: Any, rounds: int = 6) -> CheckResult:
    """Measure round-trip latency by timing repeated ``list_tools`` calls.

    Args:
        session: An initialized MCP ``ClientSession`` (or a compatible stub
            exposing an async ``list_tools()``).
        rounds: Number of round-trips to time.

    Returns:
        A graded latency :class:`CheckResult`. If a round-trip raises, the
        check fails with a note (an endpoint that cannot answer a basic
        request repeatedly is unreliable by definition).
    """
    samples_ms: list[float] = []
    for _ in range(max(1, rounds)):
        start = time.perf_counter()
        try:
            await session.list_tools()
        except Exception as exc:  # noqa: BLE001 - report, don't crash the run
            return CheckResult(
                category=CATEGORY_LATENCY,
                name="round-trip",
                passed=False,
                score=0.0,
                note=f"Round-trip failed during latency probe: {exc}",
                details={"rounds_attempted": len(samples_ms) + 1},
            )
        samples_ms.append((time.perf_counter() - start) * 1000.0)

    stats = compute_percentiles(samples_ms)
    return grade_latency(stats["p50"], stats["p95"], rounds=len(samples_ms))
