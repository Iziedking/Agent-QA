"""Portable, private agent memory client.

A thin, defensive client to the Node memory sidecar, which keeps each user's
memory encrypted on Walrus, organised as ``user -> folder -> items``. The user's
passphrase travels with each call so the sidecar can decrypt transiently; it is
never stored here or there. Any agent that has the user's key and passphrase, on
any device, reaches the same memory.

Every call degrades gracefully: if the sidecar is down or memory is unconfigured,
``remember`` reports it could not store, and ``recall`` returns nothing, so the
caller is never broken by the memory layer.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

MEMORY_SVC_URL = os.environ.get("AGENT_MEMORY_URL", "http://127.0.0.1:4000").rstrip("/")

# A Walrus write can take a while; a recall stays snappier.
REMEMBER_TIMEOUT = float(os.environ.get("AGENT_MEMORY_REMEMBER_TIMEOUT", "60"))
RECALL_TIMEOUT = float(os.environ.get("AGENT_MEMORY_RECALL_TIMEOUT", "30"))


async def remember(
    user_key: str, passphrase: str, content: str, folder: str = ""
) -> dict[str, Any]:
    """Store one item, encrypted, in this user's folder. Returns a status dict.

    Awaited rather than fire-and-forget, because the caller wants to know the
    memory actually landed. Never raises.
    """
    result: dict[str, Any] = {"stored": False, "enabled": False}
    if not MEMORY_SVC_URL:
        result["note"] = "Memory is not configured."
        return result
    if not user_key or not passphrase or not content:
        result["note"] = "A user key, a passphrase, and content are all required."
        return result
    try:
        async with httpx.AsyncClient(timeout=REMEMBER_TIMEOUT) as client:
            resp = await client.post(
                f"{MEMORY_SVC_URL}/remember",
                json={"user": user_key, "passphrase": passphrase, "text": content, "folder": folder},
            )
            resp.raise_for_status()
            body = resp.json()
            result["enabled"] = bool(body.get("enabled", False))
            result["stored"] = bool(body.get("ok", False)) and result["enabled"]
            if result["stored"] and body.get("blob_id"):
                # The Walrus blob id the sidecar got back when the relayer
                # confirmed the write: a receipt the caller can surface.
                result["receipt"] = str(body["blob_id"])
            if not result["enabled"]:
                result["note"] = "Memory backend is not configured on the server."
            elif not result["stored"]:
                result["note"] = str(body.get("error") or "The write was not confirmed.")
    except Exception as exc:  # noqa: BLE001 - the memory layer must never raise into the agent
        result["note"] = f"Could not reach the memory service: {exc}"
    return result


async def recall(
    user_key: str, passphrase: str, query: str, folder: str = "", limit: int = 8
) -> dict[str, Any]:
    """Recall relevant items from this user's folder, decrypted. Always well-formed.

    ``truncated`` is True when the folder holds more items than the sidecar
    could scan, so the caller knows the answer may be incomplete.
    """
    result: dict[str, Any] = {"query": query, "enabled": False, "records": [], "truncated": False}
    if not MEMORY_SVC_URL or not user_key or not passphrase or not query:
        return result
    try:
        async with httpx.AsyncClient(timeout=RECALL_TIMEOUT) as client:
            resp = await client.post(
                f"{MEMORY_SVC_URL}/recall",
                json={
                    "user": user_key, "passphrase": passphrase,
                    "query": query, "folder": folder, "limit": limit,
                },
            )
            resp.raise_for_status()
            body = resp.json()
            result["enabled"] = bool(body.get("enabled", False))
            records = body.get("records") or []
            result["records"] = [r for r in records if isinstance(r, str)]
            result["truncated"] = bool(body.get("truncated", False))
    except Exception:  # noqa: BLE001 - a memory outage must not surface as an error
        pass
    return result


async def memory_status() -> dict[str, Any]:
    """Report whether the memory sidecar is reachable and configured."""
    status: dict[str, Any] = {"reachable": False, "enabled": False}
    if not MEMORY_SVC_URL:
        return status
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{MEMORY_SVC_URL}/health")
            resp.raise_for_status()
            body = resp.json()
            status["reachable"] = True
            status["enabled"] = bool(body.get("enabled", False))
    except Exception:  # noqa: BLE001
        pass
    return status
