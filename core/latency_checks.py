"""Check 4: latency under repetition.

Times repeated round-trips to the server and reports p50 / p95. Slow or wildly
variable latency is a reliability signal AI orchestrators care about: a tool that
usually answers in 100 ms but occasionally takes 8 s will stall agent pipelines.

To stay strictly read-only (never triggering a tool's business logic or side
effects) the probe times ``list_tools()`` round-trips. That still exercises the
full request and response path (transport, framing, serialization, the server's
request handler) without invoking any listed tool.

The first round-trips after a handshake carry one-off warm-up cost (TLS session
setup, cold caches, JIT paths), which would otherwise dominate the tail and make
the score swing between runs. So a couple of untimed warm-up calls are discarded
before sampling, and steady-state latency is what gets graded.

The math (:func:`compute_percentiles`) and the grading (:func:`grade_latency`)
are pure and unit-tested. :func:`run_latency_check` is the thin async wrapper
that gathers the samples from a live session.
"""

from __future__ import annotations

import time
from typing import Any

from .models import CATEGORY_LATENCY, CheckResult

# Label thresholds on p95, in milliseconds. Each entry is (max_p95_ms, label).
# These name the band in the note; the numeric score is interpolated (below) so
# it slides smoothly rather than jumping at a threshold.
LATENCY_BANDS = (
    (300.0, "excellent"),
    (800.0, "good"),
    (2000.0, "acceptable"),
    (5000.0, "slow"),
    (float("inf"), "very slow"),
)

# Score anchors as (p95_ms, score) points. The score is a linear interpolation
# between neighbouring anchors, so a server sitting near a boundary (say p95 just
# over 2000 ms) loses a point or two, not a whole band. Anchors line up with the
# label thresholds above, so the wording and the number still agree.
LATENCY_ANCHORS = (
    (0.0, 100.0),
    (300.0, 100.0),
    (800.0, 85.0),
    (2000.0, 65.0),
    (5000.0, 40.0),
    (10000.0, 15.0),
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


def _interpolate_score(p95_ms: float) -> float:
    """Linearly interpolate the latency score from the anchor points."""
    anchors = LATENCY_ANCHORS
    if p95_ms <= anchors[0][0]:
        return anchors[0][1]
    for (x0, y0), (x1, y1) in zip(anchors, anchors[1:]):
        if p95_ms <= x1:
            t = (p95_ms - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)
    return anchors[-1][1]  # beyond the last anchor, hold the floor


def grade_latency(p50_ms: float, p95_ms: float, rounds: int) -> CheckResult:
    """Grade latency from p50 / p95 in milliseconds.

    Args:
        p50_ms: Median round-trip time in milliseconds.
        p95_ms: 95th-percentile round-trip time in milliseconds.
        rounds: How many samples were taken (recorded for context).

    Returns:
        A :class:`CheckResult` in the ``latency`` category.
    """
    score = _interpolate_score(p95_ms)
    label = "very slow"
    for max_p95, band_label in LATENCY_BANDS:
        if p95_ms <= max_p95:
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


async def run_latency_check(
    session: Any, rounds: int = 8, warmup: int = 2
) -> CheckResult:
    """Measure round-trip latency by timing repeated ``list_tools`` calls.

    Args:
        session: An initialized MCP ``ClientSession`` (or a compatible stub
            exposing an async ``list_tools()``).
        rounds: Number of round-trips to time (after warm-up).
        warmup: Number of untimed round-trips run first and discarded, so
            one-off connection warm-up does not skew the sampled latency.

    Returns:
        A graded latency :class:`CheckResult`. If any round-trip raises, the
        check fails with a note (an endpoint that cannot answer a basic
        request repeatedly is unreliable by definition).
    """

    def _failed(phase: str, exc: Exception, timed: int) -> CheckResult:
        return CheckResult(
            category=CATEGORY_LATENCY,
            name="round-trip",
            passed=False,
            score=0.0,
            note=f"Round-trip failed during latency {phase}: {exc}",
            details={"rounds_attempted": timed + 1},
        )

    # Warm-up: exercise the path a few times without timing, so cold-start cost
    # (TLS, first-request caches) does not land in the sampled percentiles.
    for _ in range(max(0, warmup)):
        try:
            await session.list_tools()
        except Exception as exc:  # noqa: BLE001 - report, don't crash the run
            return _failed("warm-up", exc, timed=0)

    samples_ms: list[float] = []
    for _ in range(max(1, rounds)):
        start = time.perf_counter()
        try:
            await session.list_tools()
        except Exception as exc:  # noqa: BLE001 - report, don't crash the run
            return _failed("probe", exc, timed=len(samples_ms))
        samples_ms.append((time.perf_counter() - start) * 1000.0)

    stats = compute_percentiles(samples_ms)
    return grade_latency(stats["p50"], stats["p95"], rounds=len(samples_ms))
