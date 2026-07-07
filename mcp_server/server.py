"""Agent QA as an MCP server.

Exposes one tool, ``evaluate_mcp_endpoint``, that takes a public MCP endpoint
URL and returns a graded reliability report. The tool calls the same engine the
HTTP service and the CLI use, so all three agree on every grade.

The tool's own name and description are written to pass Agent QA's description
quality check. A reliability tool that could not describe itself well would be a
poor advertisement for the service.

Run it as an HTTP MCP server with ``agent-qa-mcp`` or ``python -m mcp_server``.
"""

from __future__ import annotations

import os
from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import Field

from core.report import evaluate
from core.reputation import recall_reputation, remember_verdict
from core.validation import validate_mcp_url

mcp = FastMCP(
    name="Agent QA",
    instructions=(
        "Use this server to check whether a public MCP endpoint is reliable "
        "before trusting it. Call evaluate_mcp_endpoint to grade an endpoint now, "
        "or recall_tool_reputation to look up its remembered track record first."
    ),
)


async def evaluate_mcp_endpoint(
    endpoint_url: Annotated[
        str,
        Field(
            description=(
                "Public URL of the MCP endpoint to evaluate, "
                "for example https://example.com/mcp"
            )
        ),
    ],
) -> dict[str, Any]:
    """Evaluate the reliability of a public MCP server and return a graded report.

    Give this tool the URL of an MCP endpoint in endpoint_url. It opens a real
    protocol session against that server and runs five read-only checks:
    connection and handshake, schema validity, malformed input handling,
    latency measured as p50 and p95, and description quality. It returns a
    report with a score per category, a per-tool breakdown, an overall letter
    grade from A to F, and the top issues found. The probe is read-only: it
    sends only inputs a correct server rejects before doing any work, so it does
    not exercise a tool's side-effecting paths.
    """
    url = validate_mcp_url(endpoint_url)
    report = await evaluate(url)
    # Remember this verdict so the endpoint builds a track record agents can
    # recall later. Runs in the background and never blocks or breaks the report.
    remember_verdict(report)
    return report.to_dict()


async def recall_tool_reputation(
    query: Annotated[
        str,
        Field(
            description=(
                "The MCP endpoint URL or a natural-language question about a tool's "
                "reliability, for example 'is https://example.com/mcp reliable'."
            )
        ),
    ],
) -> dict[str, Any]:
    """Recall Agent QA's remembered track record for an MCP endpoint before trusting it.

    Call this first, before your agent uses an unfamiliar MCP server. It returns
    the verdicts Agent QA has recorded for that endpoint over time (grade, latency,
    known issues), pulled from a shared, tamper-evident reputation memory on
    Walrus. If nothing is remembered yet, the records list is empty and you can
    call evaluate_mcp_endpoint to grade it now. This is a read of past judgments,
    not a fresh probe.
    """
    recalled = await recall_reputation(query)
    found = len(recalled["records"])
    if not recalled["enabled"]:
        note = "Reputation memory is not configured, so no track record is available."
    elif found:
        note = f"Found {found} remembered verdict(s) for this query."
    else:
        note = "No verdict remembered yet. Grade it with evaluate_mcp_endpoint to start its record."
    return {
        "query": recalled["query"],
        "records": recalled["records"],
        "memory_enabled": recalled["enabled"],
        "note": note,
    }


# Register the tools while keeping the functions plain callables, so they can be
# unit tested directly without going through the transport.
mcp.tool(evaluate_mcp_endpoint)
mcp.tool(recall_tool_reputation)


def run() -> None:
    """Run the MCP server over HTTP (``agent-qa-mcp`` / ``python -m mcp_server``).

    Host and port can be overridden with AGENT_QA_MCP_HOST and
    AGENT_QA_MCP_PORT. The endpoint is served at the /mcp path.
    """
    host = os.environ.get("AGENT_QA_MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("AGENT_QA_MCP_PORT", "9091"))
    mcp.run(transport="http", host=host, port=port)
