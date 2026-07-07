"""Tests for core.validation: URL shape check and the SSRF guard."""

import httpx
import pytest

from core.validation import (
    BlockedHostError,
    _PublicHostTransport,
    _resolve_and_validate,
    ensure_public_host,
    guarded_http_client_factory,
    validate_mcp_url,
)


def test_accepts_http_and_https():
    assert validate_mcp_url("http://example.com/mcp") == "http://example.com/mcp"
    assert validate_mcp_url("https://example.com/mcp") == "https://example.com/mcp"


def test_strips_surrounding_whitespace():
    assert validate_mcp_url("  https://example.com/mcp  ") == "https://example.com/mcp"


@pytest.mark.parametrize("bad", ["ftp://example.com", "example.com/mcp", "", "   ", "mcp"])
def test_rejects_non_http_urls(bad):
    with pytest.raises(ValueError):
        validate_mcp_url(bad)


# --- SSRF guard ------------------------------------------------------------

@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:9/mcp",           # loopback
        "http://10.0.0.5/mcp",              # private
        "http://192.168.1.1/mcp",           # private
        "http://169.254.169.254/latest",    # cloud metadata (link-local)
        "http://[::1]/mcp",                 # IPv6 loopback
        "http://0.0.0.0/mcp",               # unspecified
    ],
)
def test_ensure_public_host_blocks_non_public_ips(url):
    with pytest.raises(BlockedHostError):
        ensure_public_host(url)


def test_ensure_public_host_allows_public_ip_literal():
    ensure_public_host("http://8.8.8.8/mcp")  # global, must not raise


def test_ensure_public_host_allows_unresolvable_host():
    # An unresolvable name is not an SSRF vector; the connection will just fail.
    ensure_public_host("http://nonexistent.invalid.example/mcp")


def test_ensure_public_host_bypass_flag():
    ensure_public_host("http://127.0.0.1/mcp", allow_private=True)


def test_ensure_public_host_env_bypass(monkeypatch):
    monkeypatch.setenv("AGENT_QA_ALLOW_PRIVATE_HOSTS", "1")
    ensure_public_host("http://127.0.0.1/mcp")


# --- resolve-and-pin (DNS-rebinding guard) ---------------------------------

def test_resolve_and_validate_returns_public_literal():
    assert _resolve_and_validate("8.8.8.8", 443) == "8.8.8.8"


def test_resolve_and_validate_blocks_private_literal():
    with pytest.raises(BlockedHostError):
        _resolve_and_validate("127.0.0.1", 80)


def test_resolve_and_validate_unresolvable_returns_none():
    assert _resolve_and_validate("nonexistent.invalid.example", 80) is None


# --- guarded httpx client factory ------------------------------------------

def test_guarded_factory_routes_through_pinning_transport():
    client = guarded_http_client_factory()
    # Redirects are followed (MCP servers often redirect to a trailing slash),
    # but every hop goes through the pinning transport, so a redirect aimed at an
    # internal address is refused there rather than by disabling redirects.
    assert client.follow_redirects is True
    assert isinstance(client._transport, _PublicHostTransport)


def test_guarded_factory_honors_explicit_timeout():
    timeout = httpx.Timeout(5.0)
    client = guarded_http_client_factory(timeout=timeout)
    assert client.timeout == timeout
