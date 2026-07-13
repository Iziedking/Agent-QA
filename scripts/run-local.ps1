# Run the Portable Agent Memory stack locally for a real coding agent to use.
#
# Starts two long-lived servers and keeps them up until you press Ctrl+C:
#   - the Node memory sidecar on :4000 (encrypts and talks to Walrus)
#   - the FastMCP HTTP server on :9091 (the MCP endpoint your agent connects to)
#
# MemWal credentials are read from the gitignored .env next to this repo. Run
# this in its own terminal, leave it running, then start (or restart) your
# coding agent so it picks up .mcp.json and connects.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

# Load .env (MEMWAL_PRIVATE_KEY, MEMWAL_ACCOUNT_ID) into this process so the
# child servers inherit it. Never printed.
$envFile = Join-Path $root ".env"
if (-not (Test-Path $envFile)) { throw "Missing .env at $envFile (needs MEMWAL_PRIVATE_KEY and MEMWAL_ACCOUNT_ID)." }
foreach ($line in Get-Content $envFile) {
  if ($line -match '^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*)$') {
    $name = $matches[1]
    $val = $matches[2].Trim().Trim('"').Trim("'")
    Set-Item -Path "Env:$name" -Value $val
  }
}

$env:AGENT_QA_MCP_HOST = "127.0.0.1"
$env:AGENT_QA_MCP_PORT = "9091"
$env:AGENT_MEMORY_URL = "http://127.0.0.1:4000"

Write-Host "Starting memory sidecar on :4000 ..." -ForegroundColor Cyan
$sidecar = Start-Process node -ArgumentList "server.mjs" `
  -WorkingDirectory (Join-Path $root "memory-svc") -PassThru -NoNewWindow

Start-Sleep -Seconds 2
Write-Host "Starting MCP HTTP server on :9091 ..." -ForegroundColor Cyan
$mcp = Start-Process python -ArgumentList "-m", "mcp_server" `
  -WorkingDirectory $root -PassThru -NoNewWindow

Write-Host ""
Write-Host "Both servers are running." -ForegroundColor Green
Write-Host "  sidecar : http://127.0.0.1:4000/health" -ForegroundColor Green
Write-Host "  mcp     : http://127.0.0.1:9091/mcp" -ForegroundColor Green
Write-Host "Leave this window open. Press Ctrl+C to stop both." -ForegroundColor Yellow

try {
  Wait-Process -Id $mcp.Id
} finally {
  Stop-Process -Id $sidecar.Id -ErrorAction SilentlyContinue
  Stop-Process -Id $mcp.Id -ErrorAction SilentlyContinue
  Write-Host "Stopped." -ForegroundColor Yellow
}
