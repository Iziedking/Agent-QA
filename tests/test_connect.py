"""Tests for core.connect.open_mcp_session.

The key regression: an exception raised by the caller's body after a successful
connect must propagate unchanged, not be swallowed by the connect error handler
and mislabeled as a connection failure.
"""

import contextlib

import pytest

import core.connect as connect_mod


class _FakeSession:
    """Minimal stand-in for mcp.ClientSession as an async context manager."""

    def __init__(self, read, write):
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.closed = True
        return False

    async def initialize(self):
        return None


def _ok_streamable():
    @contextlib.asynccontextmanager
    async def factory(url, timeout=15.0):
        yield (object(), object(), lambda: None)

    return factory


def _boom_factory(message):
    @contextlib.asynccontextmanager
    async def factory(url, timeout=15.0):
        raise RuntimeError(message)
        yield  # pragma: no cover

    return factory


async def test_caller_exception_propagates_unchanged(monkeypatch):
    monkeypatch.setattr(connect_mod, "streamablehttp_client", _ok_streamable())
    monkeypatch.setattr(connect_mod, "ClientSession", _FakeSession)

    # Before the fix this raised ConnectionError / RuntimeError instead of the
    # caller's own ValueError, because the yield sat inside the connect except.
    with pytest.raises(ValueError, match="boom from body"):
        async with connect_mod.open_mcp_session("http://x/mcp") as (session, transport):
            assert transport == "streamable-http"
            raise ValueError("boom from body")


async def test_successful_session_yields_and_cleans_up(monkeypatch):
    monkeypatch.setattr(connect_mod, "streamablehttp_client", _ok_streamable())
    monkeypatch.setattr(connect_mod, "ClientSession", _FakeSession)

    captured = {}
    async with connect_mod.open_mcp_session("http://x/mcp") as (session, transport):
        captured["session"] = session
        captured["transport"] = transport
    assert captured["transport"] == "streamable-http"
    assert captured["session"].closed is True


async def test_all_transports_fail_raises_connection_error(monkeypatch):
    monkeypatch.setattr(connect_mod, "streamablehttp_client", _boom_factory("no route sh"))
    monkeypatch.setattr(connect_mod, "sse_client", _boom_factory("no route sse"))

    with pytest.raises(ConnectionError) as excinfo:
        async with connect_mod.open_mcp_session("http://x/mcp"):
            pass  # pragma: no cover - never reached
    assert "no route sh" in str(excinfo.value)
    assert "no route sse" in str(excinfo.value)


async def test_falls_back_to_sse_when_streamable_fails(monkeypatch):
    monkeypatch.setattr(connect_mod, "streamablehttp_client", _boom_factory("sh down"))

    @contextlib.asynccontextmanager
    async def sse_ok(url, timeout=5.0):
        yield (object(), object())

    monkeypatch.setattr(connect_mod, "sse_client", sse_ok)
    monkeypatch.setattr(connect_mod, "ClientSession", _FakeSession)

    async with connect_mod.open_mcp_session("http://x/mcp") as (session, transport):
        assert transport == "sse"
