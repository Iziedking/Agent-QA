"""Tests for core.agent_memory: the graceful, per-user, encrypted memory client.

The sidecar is faked, so these run without a network or a live relayer. The
contract under test is that the memory layer never raises into the agent and
always returns well-formed values.
"""

import core.agent_memory as mem


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
    posts: list[dict] = []

    def __init__(self, *args, resp=None, fail=False, **kwargs):
        self._resp = resp if resp is not None else {"ok": True, "enabled": True}
        self._fail = fail

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None):
        if self._fail:
            raise RuntimeError("sidecar down")
        _FakeClient.posts.append(json)
        return _FakeResp(self._resp)

    async def get(self, url, params=None):
        if self._fail:
            raise RuntimeError("sidecar down")
        return _FakeResp(self._resp)


def _factory(**canned):
    def make(*args, **kwargs):
        return _FakeClient(*args, **canned, **kwargs)
    return make


async def test_remember_encrypts_under_user_and_folder(monkeypatch):
    _FakeClient.posts = []
    monkeypatch.setattr(mem, "MEMORY_SVC_URL", "http://memory:4000")
    monkeypatch.setattr(mem.httpx, "AsyncClient", _factory(resp={"ok": True, "enabled": True}))
    out = await mem.remember("ada@example.com", "s3cret", "prefers dark mode", "project-x")
    assert out["stored"] is True and out["enabled"] is True
    assert _FakeClient.posts == [
        {"user": "ada@example.com", "passphrase": "s3cret", "text": "prefers dark mode", "folder": "project-x"}
    ]


async def test_remember_reports_when_backend_off(monkeypatch):
    monkeypatch.setattr(mem, "MEMORY_SVC_URL", "http://memory:4000")
    monkeypatch.setattr(mem.httpx, "AsyncClient", _factory(resp={"ok": True, "enabled": False}))
    out = await mem.remember("ada", "s3cret", "note")
    assert out["stored"] is False and out["enabled"] is False


async def test_remember_graceful_when_down(monkeypatch):
    monkeypatch.setattr(mem, "MEMORY_SVC_URL", "http://memory:4000")
    monkeypatch.setattr(mem.httpx, "AsyncClient", _factory(fail=True))
    out = await mem.remember("ada", "s3cret", "note")
    assert out["stored"] is False and "note" in out


async def test_remember_needs_passphrase(monkeypatch):
    monkeypatch.setattr(mem, "MEMORY_SVC_URL", "http://memory:4000")
    out = await mem.remember("ada", "", "note")
    assert out["stored"] is False


async def test_recall_parses_records(monkeypatch):
    monkeypatch.setattr(mem, "MEMORY_SVC_URL", "http://memory:4000")
    monkeypatch.setattr(
        mem.httpx, "AsyncClient",
        _factory(resp={"enabled": True, "records": ["prefers dark mode", 5, "lives in Lagos"]}),
    )
    out = await mem.recall("ada", "s3cret", "what do I prefer", "project-x")
    assert out["enabled"] is True
    assert out["records"] == ["prefers dark mode", "lives in Lagos"]  # non-strings dropped


async def test_recall_graceful_when_down(monkeypatch):
    monkeypatch.setattr(mem, "MEMORY_SVC_URL", "http://memory:4000")
    monkeypatch.setattr(mem.httpx, "AsyncClient", _factory(fail=True))
    out = await mem.recall("ada", "s3cret", "anything")
    assert out == {"query": "anything", "enabled": False, "records": []}


async def test_recall_disabled_without_url(monkeypatch):
    monkeypatch.setattr(mem, "MEMORY_SVC_URL", "")
    out = await mem.recall("ada", "s3cret", "anything")
    assert out["enabled"] is False and out["records"] == []
