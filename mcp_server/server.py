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

from core.agent_memory import download_file as download_file_memory
from core.agent_memory import forget as forget_memory
from core.agent_memory import list_files as list_files_memory
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
        "not just what.\n\n"
        "Files are separate from notes. recall never returns files. To work with "
        "the user's stored files, use list_files to see what is in a folder and "
        "fetch_file to retrieve one by name."
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
    elif result.get("retired"):
        note = "This identity is retired on this server, so nothing can be recalled."
    elif result.get("locked"):
        note = (
            "This folder holds notes, but the configured passphrase does not open "
            "any of them. The passphrase is almost certainly wrong; the memory is "
            "NOT empty. Tell the user to fix X-Memory-Passphrase in the MCP "
            "configuration, or to re-run the connector setup, before continuing."
        )
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
        "locked": bool(result.get("locked", False)),
        "note": note,
    }


async def forget(
    folder: Annotated[
        str,
        Field(
            description=(
                "The folder to forget: the project or task's folder name. Leave "
                "empty for the default space."
            )
        ),
    ] = "",
) -> dict[str, Any]:
    """Forget a folder of the user's memory, permanently. Confirm with the user first.

    After this, no recall will return the folder's notes again; the folder
    starts fresh and new notes work immediately. This is irreversible, so call
    it only when the user explicitly asks to forget or wipe a folder, never on
    your own judgement. The server verifies the configured passphrase actually
    opens the folder before honouring it, so an identity string alone cannot
    wipe anything. Honest semantics: the old encrypted notes stay on Walrus
    until their storage expires, sealed under the passphrase; they are simply
    never served again.
    """
    user_key, passphrase = _get_identity()
    if not user_key or not passphrase:
        return {"forgotten": False, "memory_enabled": False, "note": _CONFIGURE}
    result = await forget_memory(user_key, passphrase, folder)
    out: dict[str, Any] = {
        "forgotten": result["forgotten"],
        "memory_enabled": result["enabled"],
    }
    if result.get("note"):
        out["note"] = result["note"]
    return out


async def list_files(
    folder: Annotated[
        str,
        Field(
            description=(
                "The folder to list files from: the project or task's folder name. "
                "Leave empty for the default space. Files are separate from notes, "
                "so recall does not find them; use this to see stored files."
            )
        ),
    ] = "",
) -> dict[str, Any]:
    """List the files the user has stored in a folder (name and size).

    Files are stored separately from notes, so ``recall`` never returns them.
    Call this to see which files exist, then ``fetch_file`` to retrieve one.
    Returns each file's ``name`` and ``size``. An empty list means no files in
    that folder yet; ``locked`` true means the passphrase does not open them.
    """
    user_key, passphrase = _get_identity()
    if not user_key or not passphrase:
        return {"files": [], "memory_enabled": False, "note": _CONFIGURE}
    result = await list_files_memory(user_key, passphrase, folder)
    files = [
        {"name": f.get("name"), "size": f.get("size"), "content_type": f.get("contentType")}
        for f in result.get("files", [])
    ]
    if not result["enabled"]:
        note = "Memory is not configured on the server, so files cannot be listed."
    elif result.get("locked"):
        note = "This folder holds files, but the configured passphrase opens none of them."
    elif files:
        note = f"{len(files)} file(s) in this folder. Use fetch_file with a name to retrieve one."
    else:
        note = "No files stored in this folder yet."
    return {"files": files, "memory_enabled": result["enabled"], "locked": bool(result.get("locked", False)), "note": note}


# A fetched file is returned as base64 for the agent to decode and save. Cap the
# inline size so a huge blob does not flood the model's context; larger files
# should be pulled from the web console or the REST API instead.
_FETCH_MAX_BYTES = int(os.environ.get("AGENT_MEMORY_MCP_FETCH_MAX_BYTES", str(3 * 1024 * 1024)))


async def fetch_file(
    name: Annotated[
        str,
        Field(description="The exact file name to retrieve, as shown by list_files."),
    ],
    folder: Annotated[
        str,
        Field(description="The folder the file is in. Leave empty for the default space."),
    ] = "",
) -> dict[str, Any]:
    """Retrieve one of the user's stored files, decrypted, by name.

    Files live separately from notes. Call ``list_files`` first to find the name.
    Returns the file's bytes as base64 in ``data_base64`` along with its
    ``name`` and ``content_type``; to save it, decode the base64 and write the
    bytes to disk (for example ``echo <data_base64> | base64 -d > <name>``).
    Very large files are not inlined; retrieve those from the web console or the
    REST /file/download endpoint instead.
    """
    user_key, passphrase = _get_identity()
    if not user_key or not passphrase:
        return {"ok": False, "memory_enabled": False, "note": _CONFIGURE}
    listing = await list_files_memory(user_key, passphrase, folder)
    match = next((f for f in listing.get("files", []) if f.get("name") == name), None)
    if not match:
        if listing.get("locked"):
            return {"ok": False, "note": "The passphrase does not open this folder's files."}
        return {"ok": False, "note": f"No file named {name!r} in that folder. Use list_files to see the exact names."}
    size = int(match.get("size") or 0)
    if size > _FETCH_MAX_BYTES:
        return {
            "ok": False, "name": name, "size": size,
            "note": (
                f"{name} is {size} bytes, too large to return inline. Download it from the "
                "web console at agentsqa.xyz or via the REST /file/download endpoint."
            ),
        }
    result = await download_file_memory(user_key, passphrase, str(match.get("blobId", "")))
    if not result.get("ok"):
        return {"ok": False, "name": name, "note": result.get("note") or "The file could not be retrieved."}
    return {
        "ok": True,
        "name": name,
        "content_type": match.get("contentType"),
        "size": size,
        "data_base64": result.get("data_base64", ""),
        "note": "Decode data_base64 and write the bytes to disk to save the file.",
    }


# Register the tools while keeping the functions plain callables, so they can be
# unit tested directly without going through the transport.
mcp.tool(remember)
mcp.tool(recall)
mcp.tool(forget)
mcp.tool(list_files)
mcp.tool(fetch_file)


def run() -> None:
    """Run the MCP server over HTTP (``agent-qa-mcp`` / ``python -m mcp_server``).

    Host and port can be overridden with AGENT_QA_MCP_HOST and
    AGENT_QA_MCP_PORT. The endpoint is served at the /mcp path.
    """
    host = os.environ.get("AGENT_QA_MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("AGENT_QA_MCP_PORT", "9091"))
    mcp.run(transport="http", host=host, port=port)
