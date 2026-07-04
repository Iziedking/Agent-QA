"""Tests for core.validation: URL shape check and the SSRF guard."""

import pytest

from core.validation import BlockedHostError, ensure_public_host, validate_mcp_url


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
