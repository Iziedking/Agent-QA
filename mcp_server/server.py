"""Portable agent memory as an MCP server.

Exposes two tools, ``remember`` and ``recall``, that give any agent a private,
portable memory on Walrus. One person's agent, on any device, remembers only that
person's context, identified by the X-Memory-User and X-Memory-Passphrase headers
from the MCP client's configuration. Install this server once and your Claude
Code, Codex, Cursor, or custom agent carries the same memory across machines
instead of starting from zero every session.

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
        "This user's memory follows them across devices and across agents. Their "
        "identity and passphrase come from the X-Memory-User and X-Memory-Passphrase "
        "headers in your MCP client configuration, so the secret never enters the "
        "conversation.\n\n"
        "Follow this session ritual so the memory stays reliable:\n"
        "1. At the start of a session, call recall with the project or task as the "
        "folder, so you continue from where any previous session (on any machine, in "
        "any agent) left off, instead of starting from zero.\n"
        "2. When a decision is made, a preference is stated, or a fact emerges that "
        "a future session will need, call remember right then, not at the end.\n"
        "3. Before the session ends, or when the work reaches a milestone, remember "
        "one handoff digest: what changed, the state things were left in, and what "
        "comes next. Recall first and write the digest so it supersedes earlier "
        "notes rather than duplicating them.\n\n"
        "Use one folder per project or task, the same name every session. Write "
        "every note so a stranger could act on it alone: start with today's date, "
        "name concrete things (files, commands, amounts, addresses), and state why, "
        "not just what."
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
        Field(
            description=(
                "The note to remember. Write it so a stranger could act on it "
                "alone: start with today's date, name concrete things (files, "
                "commands, amounts, addresses), and state why, not just what."
            )
        ),
    ],
    folder: Annotated[
        str,
        Field(
            description=(
                "Folder to file this under: one folder per project or task, the "
                "same name every session. Leave empty for the default space."
            )
        ),
    ] = "",
) -> dict[str, Any]:
    """Store one thing in the user's private, portable memory on Walrus.

    Call this the moment something is worth carrying into a future session: a
    decision, a preference, a fact about the project, or an action taken on the
    user's behalf. Before the session ends, store one handoff digest (what
    changed, the state things were left in, what comes next); recall first so
    the digest supersedes earlier notes instead of duplicating them. The user's
    identity and passphrase come from your MCP client configuration, not from
    these arguments, so the note is encrypted for that user without the secret
    entering the conversation, and the same person recalls it from any device
    or agent. ``stored`` is true only after Walrus confirmed the write, and
    ``receipt`` is the blob id it lives under; on false, read ``note`` and tell
    the user instead of assuming it saved. Confirmation can take a few seconds.
    """
    user_key, passphrase = _get_identity()
    if not user_key or not passphrase:
        return {"stored": False, "memory_enabled": False, "note": _CONFIGURE}
    result = await remember_memory(user_key, passphrase, content, folder)
    out: dict[str, Any] = {
        "stored": result["stored"],
        "memory_enabled": result["enabled"],
    }
    if result.get("receipt"):
        out["receipt"] = result["receipt"]
    if result.get("note"):
        out["note"] = result["note"]
    return out


async def recall(
    query: Annotated[
        str,
        Field(description="What you want to remember, in natural language."),
    ],
    folder: Annotated[
        str,
        Field(
            description=(
                "Folder to recall from: use the project or task's folder name, "
                "the same one notes were remembered under. Leave empty for the "
                "default space."
            )
        ),
    ] = "",
) -> dict[str, Any]:
    """Recall what is relevant from the user's portable memory before you act.

    Call this at the start of every session, scoped to the project's folder, so
    you continue from where any previous session (on any machine, in any agent)
    left off instead of starting from zero. Call it again whenever you need
    context the user has given before, and before remembering a digest, so the
    new note supersedes rather than duplicates. The user's identity and
    passphrase come from your MCP client configuration, not from these
    arguments, so it decrypts that user's own memory without the secret
    entering the conversation. An empty list means nothing relevant is
    remembered yet. When ``truncated`` is true the folder holds more than could
    be scanned, so treat the answer as incomplete and say so.
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
    if result.get("truncated"):
        note += " The folder holds more than could be scanned, so this may be incomplete."
    return {
        "query": result["query"],
        "records": result["records"],
        "memory_enabled": result["enabled"],
        "truncated": bool(result.get("truncated", False)),
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
