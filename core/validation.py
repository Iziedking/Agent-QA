"""Shared input validation.

Both the HTTP layer and the MCP tool accept an endpoint URL, so the rule for
what counts as a valid one lives here in a single place.
"""

from __future__ import annotations

from urllib.parse import urlparse


def validate_mcp_url(url: str) -> str:
    """Return the cleaned URL if it is an absolute http(s) address, else raise.

    Args:
        url: The candidate MCP endpoint URL.

    Returns:
        The trimmed URL.

    Raises:
        ValueError: If the URL is not an absolute http or https URL.
    """
    cleaned = (url or "").strip()
    parsed = urlparse(cleaned)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(
            f"endpoint_url must be an absolute http(s) URL, got: {url!r}"
        )
    return cleaned
