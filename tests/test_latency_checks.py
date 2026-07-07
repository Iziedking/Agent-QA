"""Tests for core.latency_checks: percentiles, grading, and the async probe."""

import pytest

from core.latency_checks import (
    compute_percentiles,
    grade_latency,
    run_latency_check,
)
from tests.fakes import FakeSession


def test_compute_percentiles_basic():
    stats = compute_percentiles([10, 20, 30, 40, 50, 60, 70, 80, 90, 100])
    assert stats["p50"] == 50
    assert stats["p95"] == 100
    assert stats["min"] == 10
    assert stats["max"] == 100
    assert stats["mean"] == 55


def test_compute_percentiles_single_sample():
    stats = compute_percentiles([42.0])
    assert stats["p50"] == 42.0
    assert stats["p95"] == 42.0


def test_compute_percentiles_empty_raises():
    with pytest.raises(ValueError):
        compute_percentiles([])


def test_grade_latency_excellent():
    result = grade_latency(80, 250, rounds=6)
    assert result.score == 100.0
    assert result.passed is True
    assert result.details["band"] == "excellent"


def test_grade_latency_acceptable_passes():
    result = grade_latency(500, 1800, rounds=6)
    assert result.passed is True
    assert result.details["band"] == "acceptable"


def test_grade_latency_slow_fails_gate():
    result = grade_latency(1000, 4000, rounds=6)
    assert result.passed is False
    assert result.details["band"] == "slow"


def test_grade_latency_is_smooth_across_band_edge():
    # A server hovering at the 2000 ms edge must not swing scores by a whole band
    # on normal jitter; the interpolated score changes only slightly.
    just_under = grade_latency(900, 1980, rounds=8).score
    just_over = grade_latency(900, 2020, rounds=8).score
    assert abs(just_under - just_over) < 3


def test_grade_latency_variance_penalty():
    # Fast median but a long tail (p95 = 10x p50) triggers the penalty.
    steady = grade_latency(50, 60, rounds=6).score
    spiky = grade_latency(50, 500, rounds=6).score
    assert spiky < steady
    assert grade_latency(50, 500, rounds=6).details["unstable"] is True


async def test_run_latency_check_times_calls():
    session = FakeSession(tools=[])
    result = await run_latency_check(session, rounds=5, warmup=2)
    # Warm-up calls are made but not counted toward the graded sample.
    assert session.list_tools_calls == 7
    assert result.details["rounds"] == 5
    assert result.category == "latency"


async def test_run_latency_check_discards_warmup():
    session = FakeSession(tools=[])
    result = await run_latency_check(session, rounds=4, warmup=3)
    assert session.list_tools_calls == 7  # 3 warm-up + 4 timed
    assert result.details["rounds"] == 4


async def test_run_latency_check_no_warmup_when_zero():
    session = FakeSession(tools=[])
    await run_latency_check(session, rounds=3, warmup=0)
    assert session.list_tools_calls == 3


async def test_run_latency_check_handles_probe_failure():
    session = FakeSession(list_tools_raises=RuntimeError("connection reset"))
    result = await run_latency_check(session, rounds=5)
    assert result.passed is False
    assert result.score == 0.0
    assert "failed" in result.note.lower()
