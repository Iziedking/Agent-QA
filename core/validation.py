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
from typing import Any
from urllib.parse import urlparse

import anyio
import httpx


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


def _resolve_and_validate(host: str, port: int) -> str | None:
    """Resolve a host to one vetted public IP, or raise if any address is not.

    An IP literal is checked directly and returned unchanged. A hostname is
    resolved, and every address it maps to must be public; the first public
    address is returned so a caller can pin the connection to that exact IP. An
    unresolvable host returns None: the connection will simply fail and be
    reported, and an unresolvable name is not an SSRF vector.

    Raises:
        BlockedHostError: if the literal, or any resolved address, is private,
            loopback, link-local, reserved, multicast, or unspecified.
    """
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if not _address_is_public(literal):
            raise BlockedHostError(f"host {host} is not a public address")
        return host

    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return None  # unresolvable; let the connection attempt fail and report it

    public_ip: str | None = None
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
        if public_ip is None:
            public_ip = ip
    return public_ip


def ensure_public_host(url: str, *, allow_private: bool | None = None) -> None:
    """SSRF guard: reject a URL whose host resolves to a non-public address.

    This is the pre-flight check, run before dialing. It resolves the host and
    raises :class:`BlockedHostError` if any resolved address is non-public. The
    connection itself is additionally guarded by :class:`_PublicHostTransport`,
    which re-validates and pins the address at dial time so a name cannot resolve
    public here and internal at connect time (DNS rebinding).

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

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    _resolve_and_validate(host, port)  # raises BlockedHostError on a non-public address


class _PublicHostTransport(httpx.AsyncHTTPTransport):
    """An httpx transport that pins every connection to a vetted public IP.

    On each request it resolves the host, rejects it if any address is
    non-public, and dials the vetted IP directly, so the address checked is the
    exact address connected to. This closes the resolve-then-connect (DNS
    rebinding) window that a separate pre-flight check leaves open. Because the
    client follows redirects through this same transport, a redirect to an
    internal address is blocked here too, not just on the first hop.

    TLS SNI, certificate verification, and the ``Host`` header stay bound to the
    original hostname via the ``sni_hostname`` extension. The hostname is
    remembered per pinned IP so a later hop that resolves to that IP (a relative
    redirect against the rewritten URL) still presents the right SNI.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._hostname_by_ip: dict[str, str] = {}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if not _private_bypass_enabled():
            host = request.url.host
            # If this host is an IP we pinned on a prior hop, recover the real
            # hostname so SNI and certificate verification stay correct.
            hostname = self._hostname_by_ip.get(host, host)
            port = request.url.port or (443 if request.url.scheme == "https" else 80)
            pinned = await anyio.to_thread.run_sync(_resolve_and_validate, hostname, port)
            if pinned is not None and pinned != host:
                request.extensions["sni_hostname"] = hostname
                self._hostname_by_ip[pinned] = hostname
                request.url = request.url.copy_with(host=pinned)
        return await super().handle_async_request(request)


def guarded_http_client_factory(
    headers: dict[str, str] | None = None,
    timeout: Any | None = None,
    auth: httpx.Auth | None = None,
) -> httpx.AsyncClient:
    """Build the httpx client the MCP transports use, hardened against SSRF.

    Matches the MCP SDK's client-factory signature so it can be passed straight
    to ``streamablehttp_client`` / ``sse_client``. Connections go through
    :class:`_PublicHostTransport`, so every request, including each redirect hop,
    is validated and pinned to a vetted public IP at dial time. Redirects are
    still followed (many MCP servers redirect a path to its trailing-slash form),
    but a redirect aimed at an internal address is refused by the transport.
    """
    kwargs: dict[str, Any] = {
        "follow_redirects": True,
        "transport": _PublicHostTransport(),
    }
    # Mirror the SDK's defaults: 30s overall, 300s SSE read, when unspecified.
    kwargs["timeout"] = timeout if timeout is not None else httpx.Timeout(30.0, read=300.0)
    if headers is not None:
        kwargs["headers"] = headers
    if auth is not None:
        kwargs["auth"] = auth
    return httpx.AsyncClient(**kwargs)
