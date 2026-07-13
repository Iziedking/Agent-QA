"""Tests for the FastAPI service layer.

The memory client is patched so the tests are fast and deterministic. Here we
verify the HTTP contract only.
"""

import service.app as app_mod
from fastapi.testclient import TestClient

client = TestClient(app_mod.app)


def test_health_ok():
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "agent-memory"


def test_remember_stores(monkeypatch):
    async def fake_remember(user_key, passphrase, content, folder=""):
        return {"stored": True, "enabled": True}

    monkeypatch.setattr(app_mod, "remember_memory", fake_remember)
    resp = client.post(
        "/remember",
        json={"user_key": "ada@example.com", "passphrase": "s3cret", "content": "prefers dark mode"},
    )
    assert resp.status_code == 200
    assert resp.json()["stored"] is True


def test_remember_rejects_missing_fields():
    assert client.post("/remember", json={"user_key": "ada", "content": "x"}).status_code == 422
    assert client.post("/remember", json={"passphrase": "p", "content": "x"}).status_code == 422


def test_recall_returns_records(monkeypatch):
    async def fake_recall(user_key, passphrase, query, folder="", limit=8):
        return {"query": query, "enabled": True, "records": ["prefers dark mode"]}

    monkeypatch.setattr(app_mod, "recall_memory", fake_recall)
    resp = client.post(
        "/recall",
        json={"user_key": "ada@example.com", "passphrase": "s3cret", "query": "what do I prefer"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["records"] == ["prefers dark mode"]
    assert body["memory_enabled"] is True


def test_recall_requires_params():
    # Missing the required fields yields a 422.
    assert client.post("/recall", json={}).status_code == 422


def test_body_size_middleware_rejects_large_body():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    tiny = FastAPI()
    tiny.add_middleware(app_mod.MaxBodySizeMiddleware, max_bytes=100)

    @tiny.post("/echo")
    async def echo() -> dict:
        return {"ok": True}

    c = TestClient(tiny)
    assert c.post("/echo", content=b"x" * 50).status_code == 200
    assert c.post("/echo", content=b"x" * 500).status_code == 413
