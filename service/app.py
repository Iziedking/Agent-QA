"""FastAPI application for Agent QA.

Exposes two endpoints:

* ``GET  /health``   — liveness probe for hosting platforms and monitoring.
* ``POST /evaluate`` — accepts a target MCP endpoint URL and returns the full
  reliability report produced by :func:`core.report.evaluate`.

The layer is deliberately thin: it validates the request shape, calls the core
engine, and returns the result. All reliability logic lives in ``core`` so it
stays independently testable and reusable by the MCP wrapper (Step 3).

A note on status codes: an *unreachable* target is not an API error. The
evaluation still succeeded — it determined the endpoint is down — so ``/evaluate``
returns ``200`` with a report whose ``reachable`` is ``false`` and ``grade`` is
``F``. Callers gate on the report body, not the HTTP status.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

from core import __version__ as core_version
from core.report import evaluate
from core.validation import validate_mcp_url

# The browser-facing demo UI is a single self-contained file served same-origin,
# so the page can call POST /evaluate without any CORS configuration.
WEB_DIR = Path(__file__).resolve().parent.parent / "web"

app = FastAPI(
    title="Agent QA",
    description="Automated reliability reports for public MCP endpoints.",
    version=core_version,
)


class EvaluateRequest(BaseModel):
    """Request body for ``POST /evaluate``."""

    endpoint_url: str = Field(
        ...,
        description="Public URL of the MCP endpoint to evaluate.",
        examples=["https://example.com/mcp"],
    )

    @field_validator("endpoint_url")
    @classmethod
    def _must_be_http_url(cls, value: str) -> str:
        return validate_mcp_url(value)


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "agent-qa"
    version: str = core_version


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    """Serve the browser-facing demo UI (the Reliability Bench)."""
    return FileResponse(WEB_DIR / "index.html")


@app.get("/health", response_model=HealthResponse, tags=["meta"])
async def health() -> HealthResponse:
    """Return a simple liveness signal."""
    return HealthResponse()


@app.post("/evaluate", tags=["evaluation"])
async def evaluate_endpoint(request: EvaluateRequest) -> dict:
    """Evaluate an MCP endpoint and return its reliability report.

    The response is the report object rendered as JSON (see
    :meth:`core.models.Report.to_dict`). A malformed request body yields a
    ``422`` from FastAPI's validation before the engine runs.
    """
    report = await evaluate(request.endpoint_url)
    return report.to_dict()


def run() -> None:
    """Run the service with uvicorn (``agent-qa-serve`` / ``python -m service``)."""
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=9090)
