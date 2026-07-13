"""Portable agent memory as an MCP server.

Exposes two tools, ``remember`` and ``recall``, that give any agent a private,
portable memory on Walrus. One person's agent, on any device, remembers only that
person's context, addressed by a stable ``user_key``. Install this server once and
your Claude Code, Codex, Cursor, or custom agent carries the same memory across
machines instead of starting from zero every session.

The tools' own names and descriptions are written so an AI can pick and call them
correctly. Run it as an HTTP MCP server with ``agent-qa-mcp`` or
``python -m mcp_server``.
"""

from __future__ import annotations

import os
from typing import Annotated, Any

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_headers
from pydantic import Field

from core.agent_memory import recall as recall_memory
from core.agent_memory import remember as remember_memory

mcp = FastMCP(
    name="Portable Agent Memory",
    instructions=(
        "Give your agent a memory that follows the user across devices and across "
        "agents. Configure the user's identity and passphrase in your MCP client as "
        "the X-Memory-User and X-Memory-Passphrase headers, so the secret never "
        "enters the conversation. Then call remember to store something worth "
        "keeping, and recall to bring back what is relevant before you act."
    ),
)


def _get_identity() -> tuple[str, str]:
    """Read the user's identity and passphrase from the connection headers.

    These come from the MCP client's configuration, not from tool arguments, so
    the passphrase never travels through the model. Returns empty strings when the
    headers are absent (or outside an HTTP request, as in unit tests).
    """
    try:
        headers = get_http_headers(include_all=True)
    except Exception:  # noqa: BLE001 - no request context (e.g. a unit test)
        headers = {}
    user = (headers.get("x-memory-user") or "").strip()
    passphrase = headers.get("x-memory-passphrase") or ""
    return user, passphrase


_CONFIGURE = (
    "Set X-Memory-User and X-Memory-Passphrase in your MCP client configuration "
    "to enable memory. The passphrase is kept out of the conversation on purpose."
)


async def remember(
    content: Annotated[
        str,
        Field(description="The information to remember, written as a clear standalone note."),
    ],
    folder: Annotated[
        str,
        Field(
            description=(
                "Optional folder or project name to file this memory under, so it "
                "can be recalled by folder later. Leave empty for the default space."
            )
        ),
    ] = "",
) -> dict[str, Any]:
    """Store one thing in the user's private, portable memory on Walrus.

    Call this whenever something is worth carrying into future sessions: a
    preference, a decision, a fact about the project, or context the user will
    expect you to remember next time. Write ``content`` as a clear standalone note,
    and give an optional ``folder`` to organise it by project. The user's identity
    and passphrase come from your MCP client configuration, not from these
    arguments, so the memory is encrypted for that user without the secret ever
    entering the conversation. It persists across devices and across agents, so the
    same person recalls it anywhere. Persisting to Walrus can take a few seconds.
    """
    user_key, passphrase = _get_identity()
    if not user_key or not passphrase:
        return {"stored": False, "memory_enabled": False, "note": _CONFIGURE}
    return await remember_memory(user_key, passphrase, content, folder)


async def recall(
    query: Annotated[
        str,
        Field(description="What you want to remember, in natural language."),
    ],
    folder: Annotated[
        str,
        Field(
            description=(
                "Optional folder or project name to recall from, so you get that "
                "project's memory. Leave empty for the default space."
            )
        ),
    ] = "",
) -> dict[str, Any]:
    """Recall what is relevant from the user's portable memory before you act.

    Call this at the start of a session, or whenever you need context the user has
    given before, so you continue instead of starting from zero. Pass a natural
    language ``query`` and an optional ``folder`` to scope it to one project. The
    user's identity and passphrase come from your MCP client configuration, not
    from these arguments, so it decrypts that user's own memory without the secret
    entering the conversation. The same person reaches this memory from any device
    or any agent. An empty list means nothing relevant is remembered yet.
    """
    user_key, passphrase = _get_identity()
    if not user_key or not passphrase:
        return {"query": query, "records": [], "memory_enabled": False, "note": _CONFIGURE}
    result = await recall_memory(user_key, passphrase, query, folder)
    found = len(result["records"])
    if not result["enabled"]:
        note = "Memory is not configured on the server, so nothing can be recalled yet."
    elif found:
        note = f"Recalled {found} relevant item(s) from the user's memory."
    else:
        note = "Nothing relevant is remembered yet."
    return {
        "query": result["query"],
        "records": result["records"],
        "memory_enabled": result["enabled"],
        "note": note,
    }


# Register the tools while keeping the functions plain callables, so they can be
# unit tested directly without going through the transport.
mcp.tool(remember)
mcp.tool(recall)


def run() -> None:
    """Run the MCP server over HTTP (``agent-qa-mcp`` / ``python -m mcp_server``).

    Host and port can be overridden with AGENT_QA_MCP_HOST and
    AGENT_QA_MCP_PORT. The endpoint is served at the /mcp path.
    """
    host = os.environ.get("AGENT_QA_MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("AGENT_QA_MCP_PORT", "9091"))
    mcp.run(transport="http", host=host, port=port)
