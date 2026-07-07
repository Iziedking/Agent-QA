# Agent QA memory sidecar

A small Node service that gives Agent QA a **reputation memory**: every grade it
produces becomes a portable, verifiable fact on Walrus, and any agent can recall
a tool's track record before trusting it. It wraps the [Avow SDK](https://github.com/Iziedking/avow)
(`createMemory` on MemWal, which is Walrus plus Seal).

Agent QA calls this service internally. It is never exposed to the internet; only
the Python app reaches it over the private network.

## Why a separate service

The Avow SDK is TypeScript on the Sui and Walrus stack, and Agent QA is Python.
Rather than couple them in one process, this sidecar exposes the two memory
operations Agent QA needs over plain HTTP, and the Python app calls them.

## API

- `GET /health` returns `{ status, enabled }`. `enabled` is false when no MemWal
  credentials are set, in which case the service runs but stores nothing.
- `POST /remember` with `{ "text": "..." }` writes one verdict to the shared
  registry.
- `GET /recall?query=...&limit=6` returns `{ query, enabled, records }`, the
  matching verdicts by meaning.

All writes and reads use one shared registry namespace, so a tool graded once is
vetted for every agent.

## Configuration

- `MEMWAL_PRIVATE_KEY`, `MEMWAL_ACCOUNT_ID` from [memory.walrus.xyz](https://memory.walrus.xyz).
  Without them the service degrades to a no-op and Agent QA still grades as usual.
- `MEMORY_SVC_PORT` (default 4000), `MEMORY_SVC_HOST` (default 0.0.0.0).
- `AGENTQA_REGISTRY_ID` (default `agentqa-global-registry`), the shared namespace.

## Run locally

```
cd memory-svc
npm install
MEMWAL_PRIVATE_KEY=... MEMWAL_ACCOUNT_ID=... npm start
curl -s http://127.0.0.1:4000/health
```

The Avow SDK is vendored as a tarball under `vendor/`, so the install and the
container build are self-contained. To refresh it, re-pack from the Avow repo:

```
cd <avow>/packages/sdk && npm pack --pack-destination <agent-qa>/memory-svc/vendor
```
