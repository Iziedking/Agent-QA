"""FastAPI application for Agent QA.

Exposes:

* ``GET  /health``   is a liveness probe for hosting platforms and monitoring.
* ``POST /evaluate`` accepts a target MCP endpoint URL and returns the full
  reliability report produced by :func:`core.report.evaluate`.
* ``GET  /`` serves the browser UI, and ``/mcp`` is the mounted MCP endpoint.

The layer is deliberately thin: it validates the request shape, calls the core
engine, and returns the result. All reliability logic lives in ``core`` so it
stays independently testable and reusable by the MCP server.

A note on status codes: an unreachable target is not an API error. The
evaluation still succeeded, it just determined the endpoint is down, so
``/evaluate`` returns ``200`` with a report whose ``reachable`` is ``false`` and
``grade`` is ``F``. Callers gate on the report body, not the HTTP status.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from core import __version__ as core_version
from core.agent_memory import download_file as download_file_memory
from core.agent_memory import forget as forget_memory
from core.agent_memory import list_files as list_files_memory
from core.agent_memory import recall as recall_memory
from core.agent_memory import remember as remember_memory
from core.agent_memory import upload_file as upload_file_memory
from mcp_server.server import mcp as mcp_instance

# The browser-facing demo UI is a single self-contained file served same-origin,
# so the page can call POST /evaluate without any CORS configuration.
WEB_DIR = Path(__file__).resolve().parent.parent / "web"

# The only request body this service takes is a short URL, so a large body is
# either a mistake or an attempt to exhaust memory. Cap it well above any real
# request. This is public and unauthenticated, so the ceiling matters.
MAX_BODY_BYTES = int(os.environ.get("AGENT_QA_MAX_BODY_BYTES", str(1024 * 1024)))
# File uploads carry base64 bytes and need a much larger ceiling than a note or
# a URL. Only the upload path gets it; every other route keeps the tight cap.
FILE_MAX_BODY_BYTES = int(os.environ.get("AGENT_QA_FILE_MAX_BODY_BYTES", str(13 * 1024 * 1024)))
_FILE_UPLOAD_PATH = "/file/upload"


class _BodyTooLarge(Exception):
    """Raised mid-stream when a request body exceeds :data:`MAX_BODY_BYTES`."""


class MaxBodySizeMiddleware:
    """Reject request bodies larger than a ceiling, before buffering them.

    An oversized declared ``Content-Length`` is refused up front. A body with no
    declared length is counted as it streams and aborted the moment it crosses
    the ceiling, so a chunked upload cannot slip past the header check.
    """

    def __init__(self, app, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # The file upload path carries base64 file bytes, so it gets a larger
        # ceiling; every other route keeps the tight cap.
        cap = FILE_MAX_BODY_BYTES if scope.get("path") == _FILE_UPLOAD_PATH else self.max_bytes

        headers = dict(scope.get("headers") or [])
        declared = headers.get(b"content-length")
        if declared is not None:
            try:
                if int(declared) > cap:
                    await self._send_413(send)
                    return
            except ValueError:
                pass  # unparseable length; the streaming counter still guards it

        received = 0
        started = False

        async def counting_receive():
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > cap:
                    raise _BodyTooLarge
            return message

        async def tracking_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, tracking_send)
        except _BodyTooLarge:
            if started:
                raise  # response already began; cannot cleanly replace it
            await self._send_413(send)

    async def _send_413(self, send) -> None:
        response = JSONResponse(
            {"detail": "Request body too large."}, status_code=413
        )
        await response(  # a Response is itself an ASGI app
            {"type": "http"}, self._empty_receive, send
        )

    @staticmethod
    async def _empty_receive():
        return {"type": "http.request", "body": b"", "more_body": False}

# Build the MCP server as an ASGI app and mount it into this process, so a single
# container serves the UI, the REST API, and the MCP endpoint under one domain.
# The MCP app carries its own lifespan (it starts the session manager), which the
# parent app must run, so we hand that lifespan to FastAPI.
mcp_app = mcp_instance.http_app(path="/")

app = FastAPI(
    title="Portable Agent Memory",
    description="A private, portable memory for agents, on Walrus.",
    version=core_version,
    lifespan=mcp_app.lifespan,
    # Free up /docs for the product documentation page; the OpenAPI schema
    # stays at /openapi.json for anyone who wants it.
    docs_url=None,
    redoc_url=None,
)

# Guard the request body before it is buffered or parsed.
app.add_middleware(MaxBodySizeMiddleware, max_bytes=MAX_BODY_BYTES)


class RememberRequest(BaseModel):
    """Request body for ``POST /remember``."""

    user_key: str = Field(..., max_length=256, examples=["ada@example.com"])
    passphrase: str = Field(..., max_length=512)
    content: str = Field(..., max_length=8192)
    folder: str = Field("", max_length=256)


class RecallRequest(BaseModel):
    """Request body for ``POST /recall``."""

    user_key: str = Field(..., max_length=256)
    passphrase: str = Field(..., max_length=512)
    query: str = Field(..., max_length=2048)
    folder: str = Field("", max_length=256)
    limit: int = Field(8, ge=1, le=50)


class ForgetRequest(BaseModel):
    """Request body for ``POST /forget``."""

    user_key: str = Field(..., max_length=256)
    passphrase: str = Field(..., max_length=512)
    folder: str = Field("", max_length=256)


class FileUploadRequest(BaseModel):
    """Request body for ``POST /file/upload``."""

    user_key: str = Field(..., max_length=256)
    passphrase: str = Field(..., max_length=512)
    name: str = Field(..., max_length=512)
    folder: str = Field("", max_length=256)
    content_type: str = Field("application/octet-stream", max_length=200)
    # base64 of the file bytes; the middleware caps the whole request instead.
    data_base64: str = Field(...)


class FileListRequest(BaseModel):
    """Request body for ``POST /file/list``."""

    user_key: str = Field(..., max_length=256)
    passphrase: str = Field(..., max_length=512)
    folder: str = Field("", max_length=256)


class FileDownloadRequest(BaseModel):
    """Request body for ``POST /file/download``."""

    user_key: str = Field(..., max_length=256)
    passphrase: str = Field(..., max_length=512)
    blob_id: str = Field(..., max_length=256)


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "agent-memory"
    version: str = core_version


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    """Serve the browser-facing console UI."""
    return FileResponse(WEB_DIR / "index.html")


@app.get("/docs", include_in_schema=False)
async def docs() -> FileResponse:
    """Serve the same page as ``/``; the client opens the docs panel on this path.

    This makes ``agentsqa.xyz/docs`` a stable, shareable link straight into the
    documentation.
    """
    return FileResponse(WEB_DIR / "index.html")


@app.get("/health", response_model=HealthResponse, tags=["meta"])
async def health() -> HealthResponse:
    """Return a simple liveness signal."""
    return HealthResponse()


@app.post("/remember", tags=["memory"])
async def remember_endpoint(request: RememberRequest) -> dict:
    """Store one item, encrypted, in a user's private folder on Walrus.

    Returns whether it was stored. A malformed body yields a ``422`` from
    FastAPI's validation before anything runs.
    """
    return await remember_memory(
        request.user_key, request.passphrase, request.content, request.folder
    )


@app.post("/recall", tags=["memory"])
async def recall_endpoint(request: RecallRequest) -> dict:
    """Recall and decrypt relevant items from a user's folder.

    An empty ``records`` list means nothing relevant is remembered yet, the
    passphrase is wrong, or the memory layer is not configured (``memory_enabled``).
    """
    recalled = await recall_memory(
        request.user_key, request.passphrase, request.query, request.folder, request.limit
    )
    return {
        "query": recalled["query"],
        "records": recalled["records"],
        "memory_enabled": recalled["enabled"],
        "truncated": bool(recalled.get("truncated", False)),
        "retired": bool(recalled.get("retired", False)),
        "locked": bool(recalled.get("locked", False)),
    }


@app.post("/forget", tags=["memory"])
async def forget_endpoint(request: ForgetRequest) -> dict:
    """Forget a folder: the service stops serving its notes, permanently.

    The memory service verifies the passphrase opens the folder before
    honouring the request, so an identity string alone cannot wipe anything.
    """
    return await forget_memory(request.user_key, request.passphrase, request.folder)


@app.post("/file/upload", tags=["files"])
async def file_upload_endpoint(request: FileUploadRequest) -> dict:
    """Encrypt a file and store it on Walrus, indexed in the folder.

    The bytes are encrypted under the passphrase before they leave the server,
    stored as a Walrus blob, and recorded in the folder's file index so they
    can be listed and downloaded from any machine.
    """
    return await upload_file_memory(
        request.user_key, request.passphrase, request.name,
        request.data_base64, request.folder, request.content_type,
    )


@app.post("/file/list", tags=["files"])
async def file_list_endpoint(request: FileListRequest) -> dict:
    """List the files stored in a folder, decrypted metadata only."""
    return await list_files_memory(request.user_key, request.passphrase, request.folder)


@app.post("/file/download", tags=["files"])
async def file_download_endpoint(request: FileDownloadRequest) -> dict:
    """Fetch a file's Walrus blob and decrypt it under the passphrase."""
    return await download_file_memory(request.user_key, request.passphrase, request.blob_id)


# Mount the MCP endpoint last, so the explicit routes above take precedence and
# the MCP protocol is served at /mcp on the same domain.
app.mount("/mcp", mcp_app)


def run() -> None:
    """Run the service with uvicorn (``agent-qa-serve`` / ``python -m service``)."""
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=9090)
