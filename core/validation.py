"""Shared input validation and the SSRF guard.

Both the HTTP layer and the MCP tool accept an endpoint URL, so the rule for
what counts as a valid one lives here in a single place.

The engine makes an outbound connection to whatever URL it is given, so it needs
a guard against server-side request forgery: a caller could otherwise point it at
``127.0.0.1``, a private range, or a cloud metadata address and use the returned
report to probe the host's internal network. :func:`ensure_public_host` resolves
the host and rejects any non-public address.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlparse


class BlockedHostError(ValueError):
    """Raised when a URL host resolves to a non-public address."""


def validate_mcp_url(url: str) -> str:
    """Return the cleaned URL if it is an absolute http(s) address, else raise.

    This is the fast, network-free shape check. It does not resolve the host;
    use :func:`ensure_public_host` for the SSRF guard before connecting.

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


def _address_is_public(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True only for globally routable addresses safe to connect to."""
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


def _private_bypass_enabled() -> bool:
    return os.environ.get("AGENT_QA_ALLOW_PRIVATE_HOSTS", "").lower() in (
        "1",
        "true",
        "yes",
    )


def ensure_public_host(url: str, *, allow_private: bool | None = None) -> None:
    """SSRF guard: reject a URL whose host resolves to a non-public address.

    Resolves the host and raises :class:`BlockedHostError` if any resolved
    address is private, loopback, link-local, reserved, multicast, or
    unspecified. A host that cannot be resolved is allowed through: the
    connection attempt will simply fail and be reported as unreachable, and an
    unresolvable name is not an SSRF vector.

    Set ``AGENT_QA_ALLOW_PRIVATE_HOSTS=1`` (or pass ``allow_private=True``) to
    bypass the guard for local development and testing.
    """
    if allow_private is None:
        allow_private = _private_bypass_enabled()
    if allow_private:
        return

    parsed = urlparse((url or "").strip())
    host = parsed.hostname
    if not host:
        raise BlockedHostError("endpoint_url has no host")

    # An IP literal is checked directly, with no DNS lookup.
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if not _address_is_public(literal):
            raise BlockedHostError(
                f"host {host} is not a public address"
            )
        return

    # A hostname is resolved, and every address it maps to must be public, so a
    # name that points at an internal IP cannot slip through.
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return  # unresolvable; let the connection attempt fail and report it

    for info in infos:
        ip = info[4][0]
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if not _address_is_public(addr):
            raise BlockedHostError(
                f"host {host} resolves to a non-public address ({ip})"
            )
