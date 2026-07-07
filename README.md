# Agent QA

Agent QA is the reputation layer for MCP tools. It checks whether a public MCP server is reliable, remembers what it found as verifiable memory on Walrus, and lets any agent recall a tool's track record before trusting it.

Point it at an MCP endpoint. It connects the way an AI agent would, runs a set of read-only checks, grades the server from A to F, and keeps that verdict, so the next agent does not have to start from a shrug.

## Why it exists

Agents are starting to call tools they have never seen, and every MCP server asks the same thing: trust me. A server can declare a broken schema, crash on bad input, answer slowly, or describe its tools so poorly that an agent picks the wrong one, and you find out only after it fails.

Testing used to mean driving the MCP Inspector by hand, one call at a time, and the result lived nowhere. The next agent, and the next builder listing on OKX.AI, started over. Agent QA does the testing for you and turns the result into memory. A tool graded once is vetted for everyone who asks after.

If you build MCP servers, this shows you where yours breaks before your users do. If your agent depends on someone else's server, this tells you how much you can lean on it, with a record you can check rather than a claim you have to believe.

## Two things it does

**Grade a tool now.** Hand it a URL. It runs five read-only checks and returns a graded report in a few seconds.

**Recall a tool's reputation before trusting it.** Every verdict is remembered as a portable, tamper-evident fact on Walrus. Before your agent uses an unfamiliar server, it asks Agent QA what happened last time and gets the track record back, proof instead of a promise.

## What it checks

Five things, each scored on its own.

1. Connection and handshake. Can the server open a session and list its tools at all.
2. Schema validity. Does every tool declare a valid input schema with its required fields. An agent reads that schema to build its calls, so a broken schema breaks the caller.
3. Malformed input handling. When the server gets bad input, such as a missing field or the wrong type, does it reject it cleanly, or does it crash or quietly return a wrong answer. This probe is read-only. It sends only input that a correct server rejects before it does any real work, so it never triggers a side effect.
4. Latency. How fast the server answers over repeated calls, reported as p50 and p95.
5. Description quality. Are the tool names and descriptions clear enough for an AI to pick the right tool and call it correctly. The scoring rubric is written down in the code, so every grade is defensible.

The report gives a score for each category, a per-tool breakdown, an overall grade, and a short list of the top issues found.

## Reputation memory

The grade is only half the point. Agent QA remembers it.

Each verdict becomes one compact fact, stored on Walrus through MemWal and sealed so it cannot be quietly edited after the fact. The memory is shared, one registry that every agent reads and writes, so a server checked by one person carries that record for the next. It is portable, the same history is there from any machine, and it is verifiable, because it lives on Walrus rather than in a database someone can rewrite.

Any agent can recall it before it acts. Ask for a server by URL and you get back what Agent QA has recorded over time: the grade, the latency, the known issues. If nothing is remembered yet, the record is empty, and grading the server starts it.

This is the part agents rely on at runtime. A grader you run once is a developer tool. A memory an agent checks before it trusts a tool is infrastructure.

The memory layer is optional. With no account configured, Agent QA still grades every endpoint exactly as before, and the reputation panel simply reports that memory is off.

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
4. Read the grade, the category scores, the per-tool results, the defect log, and the endpoint's remembered reputation.

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

It returns the full report as JSON, and remembers the verdict in the reputation memory.

To read a server's remembered track record without grading it again, ask the reputation endpoint:

```
GET /reputation?q=https://your-server.example/mcp
```

`GET /health` returns a liveness check. `GET /` serves the browser interface.

### As MCP tools

The service is also an MCP server, so any MCP client or AI agent can call it. It exposes two tools.

- `evaluate_mcp_endpoint`, with a single `endpoint_url` parameter, grades a server now and returns the full report.
- `recall_tool_reputation`, with a single `query` parameter, returns what Agent QA has remembered about a server, so an agent can check a tool's reputation before it trusts it.

## Proof, not trust

A server asks your agent to trust it. Agent QA checks it instead, and keeps the proof where the next agent can find it.
