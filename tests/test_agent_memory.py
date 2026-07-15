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

    def __init__(self, *args, resp=None, fail=False, status=200, **kwargs):
        self._resp = resp if resp is not None else {"ok": True, "enabled": True}
        self._fail = fail
        self._status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None):
        if self._fail:
            raise RuntimeError("sidecar down")
        _FakeClient.posts.append(json)
        return _FakeResp(self._resp, status=self._status)

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


async def test_remember_returns_walrus_receipt(monkeypatch):
    monkeypatch.setattr(mem, "MEMORY_SVC_URL", "http://memory:4000")
    monkeypatch.setattr(
        mem.httpx, "AsyncClient",
        _factory(resp={"ok": True, "enabled": True, "blob_id": "walrus-blob-123"}),
    )
    out = await mem.remember("ada", "s3cret", "note")
    assert out["stored"] is True
    assert out["receipt"] == "walrus-blob-123"


async def test_remember_surfaces_unconfirmed_write(monkeypatch):
    # An ok:false with a reason (relayer down, write timed out) must reach the
    # caller as stored:false plus the reason, never a silent success.
    monkeypatch.setattr(mem, "MEMORY_SVC_URL", "http://memory:4000")
    monkeypatch.setattr(
        mem.httpx, "AsyncClient",
        _factory(resp={"ok": False, "enabled": True, "error": "write not confirmed: timeout"}),
    )
    out = await mem.remember("ada", "s3cret", "note")
    assert out["stored"] is False and out["enabled"] is True
    assert "write not confirmed" in out["note"]
    assert "receipt" not in out


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
    assert out["truncated"] is False


async def test_recall_passes_truncation_through(monkeypatch):
    # When the sidecar could not scan the whole folder, the caller must see it.
    monkeypatch.setattr(mem, "MEMORY_SVC_URL", "http://memory:4000")
    monkeypatch.setattr(
        mem.httpx, "AsyncClient",
        _factory(resp={"enabled": True, "records": ["a note"], "truncated": True}),
    )
    out = await mem.recall("ada", "s3cret", "anything")
    assert out["truncated"] is True


async def test_recall_graceful_when_down(monkeypatch):
    monkeypatch.setattr(mem, "MEMORY_SVC_URL", "http://memory:4000")
    monkeypatch.setattr(mem.httpx, "AsyncClient", _factory(fail=True))
    out = await mem.recall("ada", "s3cret", "anything")
    assert out == {"query": "anything", "enabled": False, "records": [], "truncated": False}


async def test_recall_disabled_without_url(monkeypatch):
    monkeypatch.setattr(mem, "MEMORY_SVC_URL", "")
    out = await mem.recall("ada", "s3cret", "anything")
    assert out["enabled"] is False and out["records"] == []


async def test_recall_passes_retired_through(monkeypatch):
    monkeypatch.setattr(mem, "MEMORY_SVC_URL", "http://memory:4000")
    monkeypatch.setattr(
        mem.httpx, "AsyncClient",
        _factory(resp={"enabled": True, "records": [], "retired": True}),
    )
    out = await mem.recall("old@example.com", "s3cret", "anything")
    assert out["retired"] is True and out["records"] == []


async def test_recall_passes_locked_through(monkeypatch):
    # Items exist but none decrypt: a wrong passphrase, not an empty memory.
    monkeypatch.setattr(mem, "MEMORY_SVC_URL", "http://memory:4000")
    monkeypatch.setattr(
        mem.httpx, "AsyncClient",
        _factory(resp={"enabled": True, "records": [], "locked": True}),
    )
    out = await mem.recall("ada", "wrong-pass", "anything")
    assert out["locked"] is True and out["records"] == []


async def test_forget_reports_success(monkeypatch):
    _FakeClient.posts = []
    monkeypatch.setattr(mem, "MEMORY_SVC_URL", "http://memory:4000")
    monkeypatch.setattr(mem.httpx, "AsyncClient", _factory(resp={"forgotten": True, "enabled": True}))
    out = await mem.forget("ada@example.com", "s3cret", "project-x")
    assert out["forgotten"] is True and out["enabled"] is True
    assert _FakeClient.posts == [
        {"user": "ada@example.com", "passphrase": "s3cret", "folder": "project-x"}
    ]


async def test_forget_rejected_on_wrong_passphrase(monkeypatch):
    # A 403 from the sidecar (proof of key failed) must surface as not
    # forgotten with the reason, never as an exception.
    monkeypatch.setattr(mem, "MEMORY_SVC_URL", "http://memory:4000")
    monkeypatch.setattr(
        mem.httpx, "AsyncClient",
        _factory(resp={"error": "The passphrase does not open this folder, so it cannot forget it."}, status=403),
    )
    out = await mem.forget("ada", "wrong", "project-x")
    assert out["forgotten"] is False
    assert "does not open this folder" in out["note"]


async def test_forget_graceful_when_down(monkeypatch):
    monkeypatch.setattr(mem, "MEMORY_SVC_URL", "http://memory:4000")
    monkeypatch.setattr(mem.httpx, "AsyncClient", _factory(fail=True))
    out = await mem.forget("ada", "s3cret")
    assert out["forgotten"] is False and "note" in out


async def test_upload_file_ok(monkeypatch):
    _FakeClient.posts = []
    monkeypatch.setattr(mem, "MEMORY_SVC_URL", "http://memory:4000")
    monkeypatch.setattr(
        mem.httpx, "AsyncClient",
        _factory(resp={"ok": True, "enabled": True, "files_enabled": True, "blob_id": "walrus-blob-9", "receipt": "idx-1"}),
    )
    out = await mem.upload_file("ada@example.com", "s3cret", "notes.zip", "QUJD", "project-x", "application/zip")
    assert out["ok"] is True and out["blob_id"] == "walrus-blob-9" and out["receipt"] == "idx-1"
    assert _FakeClient.posts == [{
        "user": "ada@example.com", "passphrase": "s3cret", "folder": "project-x",
        "name": "notes.zip", "contentType": "application/zip", "dataBase64": "QUJD",
    }]


async def test_upload_file_reports_index_failure(monkeypatch):
    # The bytes reached Walrus but the index write failed: ok False, blob id kept.
    monkeypatch.setattr(mem, "MEMORY_SVC_URL", "http://memory:4000")
    monkeypatch.setattr(
        mem.httpx, "AsyncClient",
        _factory(resp={"ok": False, "enabled": True, "blob_id": "walrus-blob-9", "error": "index not confirmed: paused"}),
    )
    out = await mem.upload_file("ada", "s3cret", "f.bin", "QQ==")
    assert out["ok"] is False and out["blob_id"] == "walrus-blob-9"
    assert "index not confirmed" in out["note"]


async def test_list_files_parses(monkeypatch):
    monkeypatch.setattr(mem, "MEMORY_SVC_URL", "http://memory:4000")
    monkeypatch.setattr(
        mem.httpx, "AsyncClient",
        _factory(resp={"enabled": True, "files": [{"name": "a.txt", "blobId": "b1"}, "junk"], "locked": False}),
    )
    out = await mem.list_files("ada", "s3cret", "project-x")
    assert out["enabled"] is True
    assert out["files"] == [{"name": "a.txt", "blobId": "b1"}]  # non-dicts dropped


async def test_download_file_ok(monkeypatch):
    monkeypatch.setattr(mem, "MEMORY_SVC_URL", "http://memory:4000")
    monkeypatch.setattr(mem.httpx, "AsyncClient", _factory(resp={"ok": True, "dataBase64": "QUJD"}))
    out = await mem.download_file("ada", "s3cret", "b1")
    assert out["ok"] is True and out["data_base64"] == "QUJD"


async def test_download_file_locked(monkeypatch):
    monkeypatch.setattr(mem, "MEMORY_SVC_URL", "http://memory:4000")
    monkeypatch.setattr(
        mem.httpx, "AsyncClient",
        _factory(resp={"ok": False, "locked": True, "error": "The passphrase does not open this file."}),
    )
    out = await mem.download_file("ada", "wrong", "b1")
    assert out["ok"] is False and out["locked"] is True and "does not open" in out["note"]


async def test_file_ops_graceful_when_down(monkeypatch):
    monkeypatch.setattr(mem, "MEMORY_SVC_URL", "http://memory:4000")
    monkeypatch.setattr(mem.httpx, "AsyncClient", _factory(fail=True))
    up = await mem.upload_file("ada", "s3cret", "f", "QQ==")
    assert up["ok"] is False and "note" in up
    ls = await mem.list_files("ada", "s3cret")
    assert ls == {"enabled": False, "files": [], "locked": False}
    dl = await mem.download_file("ada", "s3cret", "b1")
    assert dl["ok"] is False and "note" in dl
