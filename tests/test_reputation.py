"""Tests for core.reputation: fact formatting and the graceful memory client.

The memory sidecar is faked, so these run without a network or a live relayer.
The contract under test is that the reputation layer never raises into the grader
and always returns well-formed values.
"""

import asyncio

import core.reputation as rep
from core.models import CATEGORY_CONNECTION, CheckResult, Report


def _report() -> Report:
    conn = CheckResult(CATEGORY_CONNECTION, "handshake", True, 100.0, "ok")
    return Report(
        url="https://svc.example/mcp",
        reachable=True,
        connection=conn,
        latency=None,
        tools=[],
        category_scores={
            "connection": 100.0, "schema": 100.0, "fuzz": 100.0,
            "latency": 65.0, "description": 82.0,
        },
        overall_score=90.0,
        grade="A",
        top_issues=["[get_price] latency high"],
    )


class _FakeResp:
    def __init__(self, json_data=None, status=200):
        self._json = json_data or {}
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"status {self.status_code}")

    def json(self):
        return self._json


class _FakeClient:
    """Stands in for httpx.AsyncClient, recording calls and returning canned data."""

    posts: list[dict] = []

    def __init__(self, *args, get_json=None, fail=False, **kwargs):
        self._get_json = get_json or {}
        self._fail = fail

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None):
        if self._fail:
            raise RuntimeError("sidecar down")
        _FakeClient.posts.append(json)
        return _FakeResp()

    async def get(self, url, params=None):
        if self._fail:
            raise RuntimeError("sidecar down")
        return _FakeResp(self._get_json)


def _client_factory(**canned):
    def make(*args, **kwargs):
        return _FakeClient(*args, **canned, **kwargs)
    return make


def test_format_fact_is_compact_and_informative():
    fact = rep.format_fact(_report())
    assert "https://svc.example/mcp" in fact
    assert "graded A" in fact
    assert "latency 65" in fact
    assert "latency high" in fact


async def test_recall_parses_records(monkeypatch):
    monkeypatch.setattr(rep, "MEMORY_SVC_URL", "http://memory:4000")
    monkeypatch.setattr(
        rep.httpx, "AsyncClient",
        _client_factory(get_json={"enabled": True, "records": ["a graded B", 5, "b graded A"]}),
    )
    out = await rep.recall_reputation("is svc reliable")
    assert out["enabled"] is True
    assert out["records"] == ["a graded B", "b graded A"]  # non-strings dropped


async def test_recall_is_graceful_when_sidecar_down(monkeypatch):
    monkeypatch.setattr(rep, "MEMORY_SVC_URL", "http://memory:4000")
    monkeypatch.setattr(rep.httpx, "AsyncClient", _client_factory(fail=True))
    out = await rep.recall_reputation("anything")
    assert out == {"query": "anything", "enabled": False, "records": []}


async def test_recall_disabled_when_no_url(monkeypatch):
    monkeypatch.setattr(rep, "MEMORY_SVC_URL", "")
    out = await rep.recall_reputation("anything")
    assert out["enabled"] is False and out["records"] == []


async def test_remember_posts_the_fact_in_background(monkeypatch):
    _FakeClient.posts = []
    monkeypatch.setattr(rep, "MEMORY_SVC_URL", "http://memory:4000")
    monkeypatch.setattr(rep.httpx, "AsyncClient", _client_factory())
    rep.remember_verdict(_report())
    # Let the scheduled background task run.
    await asyncio.sleep(0)
    await asyncio.gather(*list(rep._pending))
    assert len(_FakeClient.posts) == 1
    assert "graded A" in _FakeClient.posts[0]["text"]


async def test_remember_is_noop_without_url(monkeypatch):
    _FakeClient.posts = []
    monkeypatch.setattr(rep, "MEMORY_SVC_URL", "")
    rep.remember_verdict(_report())
    await asyncio.sleep(0)
    assert _FakeClient.posts == []
