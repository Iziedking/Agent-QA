# Agent QA — automated reliability reports for MCP endpoints

Hand it a public MCP endpoint URL; it returns a structured reliability report:
schema validity, malformed-input handling, latency, description quality, and an
overall letter grade. Built for the OKX.AI AI Genesis Hackathon as an **A2MCP**
Agent Service Provider (primary lane: Revenue Rocket).

Every other ASP builder must test their MCP server before listing, and the only
tool the docs point them to is the manual MCP Inspector. Agent QA sells the
automated version of that work — the reliability layer the agent economy runs on.

## Status

- **Step 1 — core reliability engine: DONE.** Five checks, fully unit-tested
  (41 tests), verified end-to-end over the real protocol against a live server.
- Steps 2–6 (FastAPI wrap → FastMCP wrap → deploy → Inspector verify → list on
  OKX.AI) are next; see `PLAN.md` / `Guide.md`.

## The five checks

| # | Check | Module | What it catches |
|---|-------|--------|-----------------|
| 1 | Connection & handshake | `core/connect.py` | Endpoint unreachable / not speaking MCP |
| 2 | Schema validity | `core/schema_checks.py` | Missing/invalid input schema, bad `required` |
| 3 | Malformed-input handling | `core/fuzz_checks.py` | Crashes or silently accepts invalid input |
| 4 | Latency (p50/p95) | `core/latency_checks.py` | Slow or unstable response times |
| 5 | Description quality | `core/description_checks.py` | Tools an AI can't pick/call correctly |

Design: every check is a **pure function on data** plus a **thin async wrapper**,
so each is unit-tested against known-good/known-bad inputs with no live server.
`core/report.py` assembles them into one `Report` that renders as JSON (machines)
or text (humans). The malformed-input probe is **strictly read-only**: it only
sends inputs that violate a tool's own schema (rejected before any business
logic runs), and it skips no-argument tools rather than risk a side effect.

## Install

```bash
pip install -e ".[dev]"        # core engine + test deps
pip install -e ".[dev,service]" # also FastAPI + FastMCP for Steps 2-3
```

## Use

```bash
# CLI
agent-qa https://your-endpoint.example/mcp
agent-qa https://your-endpoint.example/mcp --json
python -m core https://your-endpoint.example/mcp

# Library
python -c "from core.report import evaluate_sync; print(evaluate_sync(URL).to_text())"
```

## Test

```bash
python -m pytest -q
```

## Grading

Overall score is a weighted mean over the categories that ran (weights in
`core/report.py`), renormalized when a category is absent. Letter grade:
A ≥ 90, B ≥ 80, C ≥ 70, D ≥ 60, else F. The description rubric is documented in
full in `core/description_checks.py` so every grade is defensible.
```
