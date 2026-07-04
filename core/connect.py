"""Check 1 — Connection & handshake, plus the shared session opener.

Opens a real MCP client session against a target URL using the official SDK, so
Agent QA speaks the actual protocol rather than guessing at HTTP shapes. Modern
MCP servers use the Streamable HTTP transport; older ones use HTTP+SSE. We try
Streamable HTTP first and fall back to SSE, so one opener works across both.

:func:`open_mcp_session` is the async context manager the rest of the engine
builds on. :func:`tool_to_dict` normalizes an SDK ``Tool`` into the plain dict
shape the pure checks consume.
"""

from __future__ import annotations

import contextlib
from contextlib import AsyncExitStack
from typing import Any, AsyncIterator

from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamablehttp_client

from .models import CATEGORY_CONNECTION, CheckResult

# Default per-attempt connect timeout, seconds. Kept short so a dead endpoint
# fails fast instead of hanging the whole report.
DEFAULT_TIMEOUT = 15.0


def tool_to_dict(tool: Any) -> dict[str, Any]:
    """Normalize an SDK ``Tool`` (or already-dict) into a plain dict.

    The pure checks consume ``{"name", "description", "inputSchema"}``; this is
    the single adapter between the live SDK objects and that shape.
    """
    if isinstance(tool, dict):
        return {
            "name": tool.get("name"),
            "description": tool.get("description"),
            "inputSchema": tool.get("inputSchema"),
        }
    return {
        "name": getattr(tool, "name", None),
        "description": getattr(tool, "description", None),
        "inputSchema": getattr(tool, "inputSchema", None),
    }


@contextlib.asynccontextmanager
async def open_mcp_session(
    url: str, timeout: float = DEFAULT_TIMEOUT
) -> AsyncIterator[tuple[ClientSession, str]]:
    """Open and initialize an MCP session, yielding ``(session, transport)``.

    Tries Streamable HTTP first, then HTTP+SSE. The session is initialized
    (protocol handshake complete) before it is yielded.

    Raises:
        The last transport error if every transport fails to connect.
    """
    errors: list[str] = []

    # (transport label, factory returning an async ctx mgr of streams)
    transports = (
        ("streamable-http", lambda: streamablehttp_client(url, timeout=timeout)),
        ("sse", lambda: sse_client(url, timeout=timeout)),
    )

    for label, factory in transports:
        try:
            async with AsyncExitStack() as stack:
                streams = await stack.enter_async_context(factory())
                # Streamable HTTP yields a 3-tuple (read, write, get_session_id);
                # SSE yields a 2-tuple (read, write). Take the first two.
                read_stream, write_stream = streams[0], streams[1]
                session = await stack.enter_async_context(
                    ClientSession(read_stream, write_stream)
                )
                await session.initialize()
                yield session, label
                return
        except Exception as exc:  # noqa: BLE001 - collect and try next transport
            errors.append(f"{label}: {type(exc).__name__}: {exc}")
            continue

    raise ConnectionError(
        "Could not open an MCP session on any transport. "
        + " | ".join(errors)
    )


def connection_success_result(transport: str, tool_count: int) -> CheckResult:
    """Build the passing connection CheckResult after a successful handshake."""
    return CheckResult(
        category=CATEGORY_CONNECTION,
        name="handshake",
        passed=True,
        score=100.0,
        note=(
            f"Handshake succeeded over {transport}; "
            f"server listed {tool_count} tool(s)."
        ),
        details={"transport": transport, "tool_count": tool_count},
    )


def connection_failed_result(error: str) -> CheckResult:
    """Build the failing connection CheckResult when no session could open."""
    return CheckResult(
        category=CATEGORY_CONNECTION,
        name="handshake",
        passed=False,
        score=0.0,
        note=f"Could not connect or complete handshake: {error}",
        details={"error": error},
    )
