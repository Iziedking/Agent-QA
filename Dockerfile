# Agent QA runs as one container: a single uvicorn process that serves the demo
# UI, the REST API, and the MCP endpoint together. Caddy sits in front of it and
# handles HTTPS, so this image only ever speaks plain HTTP on port 9090.

FROM python:3.12-slim

# Keep Python output unbuffered and skip writing .pyc files in the container.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first so this layer caches until the project metadata
# changes, not on every source edit.
COPY pyproject.toml README.md ./
COPY core ./core
COPY service ./service
COPY mcp_server ./mcp_server
COPY web ./web

RUN pip install --no-cache-dir ".[service]"

# Run as a non-root user.
RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 9090

# Liveness probe. A wedged-but-alive process fails this, so the orchestrator can
# see the app is unhealthy instead of forwarding traffic to a dead backend. Uses
# the stdlib so the slim image needs no extra tools.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:9090/health', timeout=3).status == 200 else 1)"]

# One process, all three surfaces: / (UI), /evaluate and /health (REST), /mcp (MCP).
CMD ["uvicorn", "service.app:app", "--host", "0.0.0.0", "--port", "9090"]
