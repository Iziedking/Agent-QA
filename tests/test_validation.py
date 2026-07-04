"""Tests for core.validation.validate_mcp_url."""

import pytest

from core.validation import validate_mcp_url


def test_accepts_http_and_https():
    assert validate_mcp_url("http://example.com/mcp") == "http://example.com/mcp"
    assert validate_mcp_url("https://example.com/mcp") == "https://example.com/mcp"


def test_strips_surrounding_whitespace():
    assert validate_mcp_url("  https://example.com/mcp  ") == "https://example.com/mcp"


@pytest.mark.parametrize("bad", ["ftp://example.com", "example.com/mcp", "", "   ", "mcp"])
def test_rejects_non_http_urls(bad):
    with pytest.raises(ValueError):
        validate_mcp_url(bad)
