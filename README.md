# Agent QA

Agent QA is a private, portable memory for AI agents. Your notes are encrypted under your own passphrase, kept on Walrus, and recalled by any agent on any device. Install it once as an MCP server and your agent stops forgetting you.

One memory, every agent, owned by you.

## Why it exists

Agents are stateless. A coding agent forgets the project between sessions. A trading agent forgets its own trades. A wallet agent forgets what it rescued last week. The memory products that do exist keep your context inside one vendor, readable by them, unreachable from any other agent.

Agent QA moves the memory to you. Every note is encrypted under a key derived from your passphrase before it reaches storage, the ciphertext lives on Walrus, and any agent you authorise recalls the same memory from anywhere. The operator cannot read your notes. A vendor cannot hold them hostage. A wrong passphrase decrypts nothing.

Memory is quality assurance for agents: an agent that remembers its decisions, its trades, and its mistakes is a better agent every session.

## What you get

- **Two tools, any agent.** `remember` stores one note; `recall` brings back what is relevant. Any MCP client carries them: Claude Code, Cursor, Codex, or a custom agent.
- **Real receipts.** A write reports stored only after Walrus confirms it, and returns the blob id it lives under. A failed write says so with the reason; it never pretends.
- **Folders.** Memory is organised as you, then folders, then notes. One folder per project keeps recall scoped: the coding agent reads `project-x`, the trading agent reads `dex-trading`, and the same person owns both.
- **Honest recall.** Results are pulled, decrypted transiently, ranked against the query, and returned. When a folder holds more than could be scanned, the reply says so instead of quietly dropping the rest.
- **The session ritual.** The server instructs every connected agent: recall the project folder at session start, remember decisions the moment they happen, close with a handoff digest. Memory that is written reliably is memory that recalls reliably.

## Wire your agent

The endpoint is `https://agentsqa.xyz/mcp` (or your own deployment). Identity and passphrase ride as HTTP headers set in the client configuration, never as tool arguments, so the secret never enters the model's context.

For Claude Code:

```
claude mcp add --transport http agent-memory https://agentsqa.xyz/mcp \
  --header "X-Memory-User: you@example.com" \
  --header "X-Memory-Passphrase: your-passphrase"
```

Per-agent configuration, a hook that guarantees recall on session start, and the note conventions that recall well are all in [docs/agent-setup.md](docs/agent-setup.md).

## The console

The site serves a memory console, the human door to the same memory your agents use. Enter your identity, passphrase, and folder, then remember and recall directly in the browser. A confirmed write shows its Walrus receipt.

## HTTP API

Two routes do the work.

```
POST /remember
{
  "user_key": "you@example.com",
  "passphrase": "your-passphrase",
  "content": "2026-07-14: bought 0.5 ETH at 3,842 USDC. Stop at 3,690.",
  "folder": "dex-trading"
}
```

The reply reports `stored`, and when the write is confirmed it carries `receipt`, the Walrus blob id.

```
POST /recall
{
  "user_key": "you@example.com",
  "passphrase": "your-passphrase",
  "query": "past trades on ETH and how they went",
  "folder": "dex-trading",
  "limit": 8
}
```

The reply carries `records`, the decrypted best matches, and `truncated`, true when the folder holds more than could be scanned.

```
POST /forget
{
  "user_key": "you@example.com",
  "passphrase": "your-passphrase",
  "folder": "dex-trading"
}
```

Forget retires a folder permanently: no recall returns its notes again, and the folder starts fresh for new writes. It is honoured only when the passphrase actually opens the folder, so an identity string alone cannot wipe anything. Walrus is immutable, so this is revocation rather than erasure: the old ciphertext stays sealed under your passphrase until its storage expires, and the service never serves it again.

`GET /health` returns a liveness check. `GET /` serves the console. `/mcp` is the MCP endpoint with the tools `remember`, `recall`, and `forget`.

## Ownership, plainly

- The passphrase is the only key. It is sent over HTTPS with each call, used transiently to encrypt or decrypt, and never stored.
- Lose the passphrase and the memory stays sealed, permanently. A reset path for you would be a reading path for someone else.
- Each confirmed write returns a Walrus blob id, so a note is not just stored but provable.
- Forgetting is revocation, not erasure, because immutable storage offers nothing stronger, and we say so. Key destruction is the only true delete.
- The service in front is stateless. Point your agents at the hosted endpoint or run your own from the published compose files; the memory model does not change.

## Self-hosting

The stack is two containers: the app (console, REST API, and the MCP endpoint on one port) and a memory sidecar that encrypts and talks to Walrus. `docker-compose.yml` runs it with its own Caddy for HTTPS. `docker-compose.proxied.yml` runs it behind an ingress proxy you already have. The sidecar needs `MEMWAL_PRIVATE_KEY` and `MEMWAL_ACCOUNT_ID` in a gitignored `.env`.
