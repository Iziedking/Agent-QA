"""Tests for the FastMCP memory server.

Covers the two tools' behavior and a self-consistency check: the memory tools
should themselves pass the schema and description quality checks, so an AI can
pick and call them correctly.
"""

import pytest

import mcp_server.server as srv
from core.description_checks import score_tool_description
from core.schema_checks import check_tool_schema
from mcp_server.server import fetch_file, forget, list_files, mcp, recall, remember


def _identity(monkeypatch, user="ada@example.com", passphrase="s3cret"):
    monkeypatch.setattr(srv, "_get_identity", lambda: (user, passphrase))


async def test_remember_tool_stores(monkeypatch):
    captured = {}
    _identity(monkeypatch)

    async def fake_remember(user_key, passphrase, content, folder=""):
        captured.update(user=user_key, passphrase=passphrase, content=content, folder=folder)
        return {"stored": True, "enabled": True}

    monkeypatch.setattr(srv, "remember_memory", fake_remember)
    out = await remember("prefers dark mode", "project-x")
    assert out["stored"] is True
    assert captured == {
        "user": "ada@example.com", "passphrase": "s3cret",
        "content": "prefers dark mode", "folder": "project-x",
    }


async def test_remember_tool_returns_receipt(monkeypatch):
    _identity(monkeypatch)

    async def fake_remember(user_key, passphrase, content, folder=""):
        return {"stored": True, "enabled": True, "receipt": "walrus-blob-123"}

    monkeypatch.setattr(srv, "remember_memory", fake_remember)
    out = await remember("a decision", "project-x")
    assert out["stored"] is True
    assert out["receipt"] == "walrus-blob-123"


async def test_remember_tool_reports_unconfirmed_write(monkeypatch):
    _identity(monkeypatch)

    async def fake_remember(user_key, passphrase, content, folder=""):
        return {"stored": False, "enabled": True, "note": "write not confirmed: timeout"}

    monkeypatch.setattr(srv, "remember_memory", fake_remember)
    out = await remember("a decision", "project-x")
    assert out["stored"] is False
    assert "write not confirmed" in out["note"]
    assert "receipt" not in out


async def test_recall_tool_returns_records(monkeypatch):
    _identity(monkeypatch)

    async def fake_recall(user_key, passphrase, query, folder=""):
        return {"query": query, "enabled": True, "records": ["prefers dark mode"]}

    monkeypatch.setattr(srv, "recall_memory", fake_recall)
    out = await recall("what do I prefer", "project-x")
    assert out["records"] == ["prefers dark mode"]
    assert out["memory_enabled"] is True
    assert "Recalled" in out["note"]
    assert out["truncated"] is False


async def test_recall_tool_flags_incomplete_scan(monkeypatch):
    _identity(monkeypatch)

    async def fake_recall(user_key, passphrase, query, folder=""):
        return {"query": query, "enabled": True, "records": ["a note"], "truncated": True}

    monkeypatch.setattr(srv, "recall_memory", fake_recall)
    out = await recall("anything", "project-x")
    assert out["truncated"] is True
    assert "incomplete" in out["note"]


async def test_recall_tool_graceful_when_memory_off(monkeypatch):
    _identity(monkeypatch)

    async def fake_recall(user_key, passphrase, query, folder=""):
        return {"query": query, "enabled": False, "records": []}

    monkeypatch.setattr(srv, "recall_memory", fake_recall)
    out = await recall("anything")
    assert out["memory_enabled"] is False
    assert "not configured on the server" in out["note"]


async def test_recall_tool_reports_retired_identity(monkeypatch):
    _identity(monkeypatch)

    async def fake_recall(user_key, passphrase, query, folder=""):
        return {"query": query, "enabled": True, "records": [], "retired": True}

    monkeypatch.setattr(srv, "recall_memory", fake_recall)
    out = await recall("anything")
    assert out["records"] == []
    assert "retired" in out["note"]


async def test_recall_tool_warns_on_wrong_passphrase(monkeypatch):
    # A locked folder must read as "wrong passphrase", never as "empty memory",
    # or an agent on a freshly set-up device silently starts from zero.
    _identity(monkeypatch)

    async def fake_recall(user_key, passphrase, query, folder=""):
        return {"query": query, "enabled": True, "records": [], "locked": True}

    monkeypatch.setattr(srv, "recall_memory", fake_recall)
    out = await recall("anything", "project-x")
    assert out["locked"] is True
    assert out["records"] == []
    assert "passphrase" in out["note"] and "NOT empty" in out["note"]


async def test_forget_tool_forwards_folder(monkeypatch):
    captured = {}
    _identity(monkeypatch)

    async def fake_forget(user_key, passphrase, folder=""):
        captured.update(user=user_key, passphrase=passphrase, folder=folder)
        return {"forgotten": True, "enabled": True}

    monkeypatch.setattr(srv, "forget_memory", fake_forget)
    out = await forget("project-x")
    assert out["forgotten"] is True
    assert captured == {"user": "ada@example.com", "passphrase": "s3cret", "folder": "project-x"}


async def test_forget_tool_surfaces_refusal(monkeypatch):
    _identity(monkeypatch)

    async def fake_forget(user_key, passphrase, folder=""):
        return {"forgotten": False, "enabled": True, "note": "The passphrase does not open this folder."}

    monkeypatch.setattr(srv, "forget_memory", fake_forget)
    out = await forget("project-x")
    assert out["forgotten"] is False
    assert "does not open this folder" in out["note"]


async def test_tools_require_configured_identity(monkeypatch):
    # With no headers configured, the tools must not call the backend and must
    # tell the user to configure their identity, never leaking anything.
    monkeypatch.setattr(srv, "_get_identity", lambda: ("", ""))
    r = await remember("something")
    assert r["stored"] is False and "X-Memory-User" in r["note"]
    q = await recall("something")
    assert q["memory_enabled"] is False and "X-Memory-User" in q["note"]
    f = await forget("something")
    assert f["forgotten"] is False and "X-Memory-User" in f["note"]


async def test_list_files_tool(monkeypatch):
    _identity(monkeypatch)

    async def fake_list(user_key, passphrase, folder=""):
        return {"enabled": True, "locked": False, "files": [
            {"name": "a.zip", "size": 1024, "blobId": "walrus:b1", "contentType": "application/zip"},
        ]}

    monkeypatch.setattr(srv, "list_files_memory", fake_list)
    out = await list_files("project-x")
    assert out["memory_enabled"] is True
    assert out["files"] == [{"name": "a.zip", "size": 1024, "content_type": "application/zip"}]
    assert "1 file" in out["note"]


async def test_fetch_file_tool_returns_bytes(monkeypatch):
    _identity(monkeypatch)

    async def fake_list(user_key, passphrase, folder=""):
        return {"enabled": True, "files": [{"name": "a.txt", "size": 3, "blobId": "walrus:b1"}]}

    async def fake_download(user_key, passphrase, blob_id):
        assert blob_id == "walrus:b1"
        return {"ok": True, "data_base64": "QUJD"}

    monkeypatch.setattr(srv, "list_files_memory", fake_list)
    monkeypatch.setattr(srv, "download_file_memory", fake_download)
    out = await fetch_file("a.txt", "project-x")
    assert out["ok"] is True and out["data_base64"] == "QUJD" and out["name"] == "a.txt"


async def test_fetch_file_tool_missing_name(monkeypatch):
    _identity(monkeypatch)

    async def fake_list(user_key, passphrase, folder=""):
        return {"enabled": True, "files": []}

    monkeypatch.setattr(srv, "list_files_memory", fake_list)
    out = await fetch_file("nope.zip")
    assert out["ok"] is False and "No file named" in out["note"]


async def test_file_tools_require_identity(monkeypatch):
    monkeypatch.setattr(srv, "_get_identity", lambda: ("", ""))
    lf = await list_files()
    assert lf["memory_enabled"] is False and "X-Memory-User" in lf["note"]
    ff = await fetch_file("a.zip")
    assert ff["ok"] is False and "X-Memory-User" in ff["note"]


async def test_tools_are_registered():
    tools = await mcp.get_tools()
    for name in ("remember", "recall", "forget", "list_files", "fetch_file"):
        assert name in tools


async def _tool_as_dict(name):
    tools = await mcp.get_tools()
    mcp_tool = tools[name].to_mcp_tool()
    return {
        "name": mcp_tool.name,
        "description": mcp_tool.description,
        "inputSchema": mcp_tool.inputSchema,
    }


@pytest.mark.parametrize("name", ["remember", "recall", "forget", "list_files", "fetch_file"])
async def test_own_tools_pass_description_check(name):
    tool = await _tool_as_dict(name)
    result = score_tool_description(tool)
    assert result.passed, f"tool {name} failed its own description check: {result.note}"


@pytest.mark.parametrize("name", ["remember", "recall", "forget", "list_files", "fetch_file"])
async def test_own_tools_pass_schema_check(name):
    tool = await _tool_as_dict(name)
    result = check_tool_schema(tool)
    assert result.passed, f"tool {name} failed its own schema check: {result.note}"
