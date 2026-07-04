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
from core.validation import validate_mcp_url

mcp = FastMCP(
    name="Agent QA",
    instructions=(
        "Use this server to test the reliability of a public MCP endpoint. "
        "Call evaluate_mcp_endpoint with the endpoint URL to get a graded report."
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
    grade from A to F, and the top issues found. The probe is read-only and
    never sends input designed to cause a side effect on the target server.
    """
    url = validate_mcp_url(endpoint_url)
    report = await evaluate(url)
    return report.to_dict()


# Register the tool while keeping ``evaluate_mcp_endpoint`` a plain callable,
# so it can be unit tested directly without going through the transport.
mcp.tool(evaluate_mcp_endpoint)


def run() -> None:
    """Run the MCP server over HTTP (``agent-qa-mcp`` / ``python -m mcp_server``).

    Host and port can be overridden with AGENT_QA_MCP_HOST and
    AGENT_QA_MCP_PORT. The endpoint is served at the /mcp path.
    """
    host = os.environ.get("AGENT_QA_MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("AGENT_QA_MCP_PORT", "9091"))
    mcp.run(transport="http", host=host, port=port)
