"""Reputation memory: remember each verdict, recall a tool's track record.

Agent QA grades an MCP endpoint, then remembers the verdict as a portable,
verifiable fact on Walrus through the memory sidecar (which wraps the Avow SDK).
Any agent can later recall a tool's history before trusting it.

This module is the thin, defensive client between the Python engine and that
sidecar. Every call degrades gracefully: if the sidecar is down or memory is
unconfigured, remembering is skipped and recall returns nothing, so grading is
never blocked or broken by the memory layer.

Writes happen in the background so a slow Walrus write never delays a report;
reads are awaited with a short timeout.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from .models import Report

# Where the memory sidecar lives. Same host in dev, the compose service name in
# the container network. Empty disables the reputation layer entirely.
MEMORY_SVC_URL = os.environ.get("AGENT_QA_MEMORY_URL", "http://127.0.0.1:4000").rstrip("/")

# A Walrus write can take a while; a recall should stay snappy.
REMEMBER_TIMEOUT = float(os.environ.get("AGENT_QA_REMEMBER_TIMEOUT", "45"))
RECALL_TIMEOUT = float(os.environ.get("AGENT_QA_RECALL_TIMEOUT", "20"))

# Hold references to background write tasks so they are not garbage collected
# before they finish.
_pending: set[asyncio.Task[Any]] = set()


def format_fact(report: "Report") -> str:
    """Render a report into one compact, recall-friendly line."""
    date = datetime.now(timezone.utc).date().isoformat()
    scores = report.category_scores or {}
    parts: list[str] = []
    for key, label in (
        ("schema", "schema"),
        ("fuzz", "malformed-input"),
        ("latency", "latency"),
        ("description", "description"),
    ):
        if key in scores:
            parts.append(f"{label} {scores[key]:.0f}")
    detail = ", ".join(parts)
    reach = "reachable" if report.reachable else "unreachable"
    tool_count = len(report.tools)
    top = report.top_issues[0] if report.top_issues else "no blocking issues"
    return (
        f"MCP endpoint {report.url} graded {report.grade} "
        f"({report.overall_score:.0f}/100) on {date}: {reach}, {detail}, "
        f"{tool_count} tool(s). Top issue: {top}."
    )


async def _post_remember(text: str) -> None:
    if not MEMORY_SVC_URL:
        return
    try:
        async with httpx.AsyncClient(timeout=REMEMBER_TIMEOUT) as client:
            await client.post(f"{MEMORY_SVC_URL}/remember", json={"text": text})
    except Exception:  # noqa: BLE001 - the memory layer must never break grading
        pass


def remember_verdict(report: "Report") -> None:
    """Schedule a background write of this verdict to the reputation memory.

    Returns immediately. If there is no running event loop or the sidecar is
    disabled, this is a no-op.
    """
    if not MEMORY_SVC_URL:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(_post_remember(format_fact(report)))
    _pending.add(task)
    task.add_done_callback(_pending.discard)


async def recall_reputation(query: str, limit: int = 6) -> dict[str, Any]:
    """Recall a tool's remembered track record. Always returns a well-formed dict."""
    result: dict[str, Any] = {"query": query, "enabled": False, "records": []}
    if not MEMORY_SVC_URL:
        return result
    try:
        async with httpx.AsyncClient(timeout=RECALL_TIMEOUT) as client:
            resp = await client.get(
                f"{MEMORY_SVC_URL}/recall", params={"query": query, "limit": limit}
            )
            resp.raise_for_status()
            body = resp.json()
            result["enabled"] = bool(body.get("enabled", False))
            records = body.get("records") or []
            result["records"] = [r for r in records if isinstance(r, str)]
    except Exception:  # noqa: BLE001 - a memory outage must not surface as an error
        pass
    return result


async def memory_status() -> dict[str, Any]:
    """Report whether the reputation memory is reachable and enabled."""
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
