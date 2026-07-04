# Agent QA

Agent QA tells you whether a public MCP server is reliable enough to trust, and gives it a letter grade from A to F.

You hand it the URL of an MCP endpoint. It connects to that server the same way an AI agent would, runs a set of read-only checks, and returns a report you can read in a few seconds.

## Why it exists

Anyone listing an MCP server on OKX.AI has to test it first. The tool the docs point to is the MCP Inspector, which you drive by hand, one call at a time. Agent QA does that testing for you and hands back a single graded report.

If you build MCP servers, this shows you where yours breaks before your users do. If you depend on someone else's server, this tells you how much you can lean on it.

## What it checks

Five things, each scored on its own.

1. Connection and handshake. Can the server open a session and list its tools at all.
2. Schema validity. Does every tool declare a valid input schema with its required fields. An agent reads that schema to build its calls, so a broken schema breaks the caller.
3. Malformed input handling. When the server gets bad input, such as a missing field or the wrong type, does it reject it cleanly, or does it crash or quietly return a wrong answer. This probe is read-only. It only sends input that a correct server rejects before it does any real work, so it never triggers a side effect.
4. Latency. How fast the server answers over repeated calls, reported as p50 and p95.
5. Description quality. Are the tool names and descriptions clear enough for an AI to pick the right tool and call it correctly. The scoring rubric is written down in the code so every grade is defensible.

The report gives a score for each category, a per-tool breakdown, an overall grade, and a short list of the top issues found.

## How to read the grade

- A, 90 and above. Reliable.
- B, 80 to 89. Minor issues.
- C, 70 to 79. Works, with real gaps.
- D, 60 to 69. Unreliable in places.
- F, below 60. Do not depend on it yet.

An endpoint that cannot be reached scores F, because a server you cannot reach is a server you cannot trust.

## How to use it

### From the browser

1. Open the Agent QA site.
2. Paste the MCP endpoint URL into the box.
3. Press Evaluate.
4. Read the grade, the category scores, the per-tool results, and the defect log.

### From the command line

Run the `agent-qa` command with the endpoint URL:

```
agent-qa https://your-server.example/mcp
```

For machine-readable output, add the JSON flag:

```
agent-qa https://your-server.example/mcp --json
```

The command exits with code 1 when the endpoint cannot be reached, so you can drop it into a script or a continuous integration check and let it fail the build on a bad server.

### From the HTTP API

Send a POST request to `/evaluate` with the endpoint URL:

```
POST /evaluate
{ "endpoint_url": "https://your-server.example/mcp" }
```

It returns the full report as JSON. `GET /health` returns a liveness check. `GET /` serves the browser interface.

### As an MCP tool

The service is also an MCP server. It exposes one tool, `evaluate_mcp_endpoint`, with a single `endpoint_url` parameter, and returns the same report. Any MCP client or AI agent can call it.
