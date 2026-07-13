#!/usr/bin/env bash
# Run the Portable Agent Memory stack locally (Git Bash friendly).
#
# Starts two long-lived servers and keeps them up until you press Ctrl+C:
#   - the Node memory sidecar on :4000 (encrypts and talks to Walrus)
#   - the FastMCP HTTP server on :9091 (the MCP endpoint your agent connects to)
#
# MemWal credentials are read from the gitignored .env at the repo root. Run this
# in its own terminal, leave it open, then start (or restart) your coding agent
# so it picks up .mcp.json and connects.

set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [ ! -f .env ]; then
  echo "Missing .env at $ROOT/.env (needs MEMWAL_PRIVATE_KEY and MEMWAL_ACCOUNT_ID)" >&2
  exit 1
fi

# Load MemWal creds (never printed) so the child servers inherit them.
set -a
# shellcheck disable=SC1091
source .env
set +a

export AGENT_QA_MCP_HOST=127.0.0.1
export AGENT_QA_MCP_PORT=9091
export AGENT_MEMORY_URL=http://127.0.0.1:4000

echo "Starting memory sidecar on :4000 ..."
( cd memory-svc && node server.mjs ) &
SIDECAR=$!

sleep 2
echo "Starting MCP HTTP server on :9091 ..."
python -m mcp_server &
MCP=$!

cleanup() {
  echo
  echo "Stopping..."
  kill "$SIDECAR" "$MCP" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo
echo "Both servers running:"
echo "  sidecar : http://127.0.0.1:4000/health"
echo "  mcp     : http://127.0.0.1:9091/mcp"
echo "Leave this window open. Press Ctrl+C to stop both."
wait
