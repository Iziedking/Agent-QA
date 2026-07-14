# Wire your agent to Portable Agent Memory

One memory, every agent, every device, owned by you. Any agent that speaks MCP over HTTP can carry the same memory, whether it writes code, rescues wallets, swaps tokens, or trades. It needs three things:

- the endpoint: `https://agentsqa.xyz/mcp` (or your own deployment)
- a user string that names you, such as an email address
- a passphrase only you know

The user string and passphrase travel as HTTP headers set in your MCP client's configuration, never as tool arguments, so the passphrase never enters the model's context. Every note is encrypted under a key derived from your passphrase before it reaches Walrus. Whoever holds the passphrase holds the memory; a wrong passphrase decrypts nothing. Pick a strong one and connect only over HTTPS.

## Claude Code

One command:

```
claude mcp add --transport http agent-memory https://agentsqa.xyz/mcp \
  --header "X-Memory-User: you@example.com" \
  --header "X-Memory-Passphrase: your-passphrase"
```

Or per project, in a `.mcp.json` at the repo root, with the passphrase drawn from the environment so the file stays shareable:

```json
{
  "mcpServers": {
    "agent-memory": {
      "type": "http",
      "url": "https://agentsqa.xyz/mcp",
      "headers": {
        "X-Memory-User": "you@example.com",
        "X-Memory-Passphrase": "${AGENT_MEMORY_PASSPHRASE}"
      }
    }
  }
}
```

### Make recall automatic

Claude Code reads the memory server's instructions on connect, so it already knows the ritual below. To guarantee the session starts from memory rather than relying on the model to think of it, add a `SessionStart` hook in `.claude/settings.json`:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "echo Recall this project's folder from agent-memory before doing anything else."
          }
        ]
      }
    ]
  }
}
```

The hook's output lands in the model's context at the start of every session, so the first thing the agent does is pick up where the last session, on any machine, left off.

## Cursor

In `~/.cursor/mcp.json` (or the project's `.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "agent-memory": {
      "url": "https://agentsqa.xyz/mcp",
      "headers": {
        "X-Memory-User": "you@example.com",
        "X-Memory-Passphrase": "your-passphrase"
      }
    }
  }
}
```

## Any other MCP client

Point it at `https://agentsqa.xyz/mcp` over streamable HTTP and attach the two headers to every request. That is the whole integration; check your agent's documentation for where remote MCP servers and their headers are configured. Custom agents built on an MCP SDK pass the headers when constructing the HTTP transport.

## The session ritual

The server tells every connected agent to work this way; it is what makes the memory reliable rather than occasional.

1. **Start by recalling.** First thing in a session, `recall` with the project or task as the folder. The agent continues from where any previous session left off, on any machine, in any agent.
2. **Remember at the moment it matters.** A decision, a stated preference, an action taken on the user's behalf (a trade placed, a wallet recovered, a config changed) gets a `remember` right then, not at the end. Sessions end unexpectedly; notes written at the moment survive that.
3. **Close with a handoff digest.** Before the session ends, one note: what changed, the state things were left in, what comes next. Recall first, then write the digest so it supersedes earlier notes instead of duplicating them.

## Writing notes that recall well

Every note should stand alone for a stranger:

- Start with the date. "2026-07-14: chose Caddy over nginx because certificates renew themselves."
- Name concrete things: file paths, commands, amounts, addresses, endpoints.
- State why, not just what. The reasoning is what the next session actually needs.
- One folder per project or task, the same name every session. Folders are isolated from each other, so recall stays scoped and fast.

## What the tools guarantee

- `remember` returns `stored: true` only after Walrus confirms the write, along with the blob id as a receipt. A failed write says so with the reason; it never pretends.
- `recall` decrypts, ranks, and returns the best matches. If a folder holds more than could be scanned, `truncated: true` says the answer may be incomplete.
- `forget` retires a folder permanently: no recall returns its notes again, and the folder starts fresh for new writes. It is honoured only when the supplied passphrase actually decrypts a note in the folder, so knowing someone's identity string alone cannot wipe anything. Agents are instructed to call it only when the user explicitly asks.
- A wrong passphrase, or someone else's, returns nothing. There is nothing to return; the ciphertext never decrypts.

## Forgetting, honestly

Walrus is immutable storage, so nothing can reach in and erase a written blob; it expires when its paid storage period lapses. `forget` is therefore implemented as revocation, not erasure: each folder carries a generation number, forgetting bumps it, and the service never serves the old generation again. The old ciphertext remains on Walrus until expiry, sealed under your passphrase, unreadable without it. This is the same reason losing your passphrase is permanent: destroyed key, dead data. Deletion by key destruction is the only deletion immutable storage can offer, and we say so rather than pretend otherwise.

Operators have one more lever: `AGENT_MEMORY_RETIRED_USERS`, a comma-separated list of identities the service refuses to serve entirely, for retiring a compromised or abandoned identity. Retired identities get nothing back on recall and cannot write.

## Self-hosting

The whole stack is two containers: the app (UI, REST, and the MCP endpoint on one port) and the memory sidecar that encrypts and talks to Walrus. `docker-compose.yml` runs it with its own Caddy for HTTPS; `docker-compose.proxied.yml` runs it behind an ingress proxy you already have. The sidecar needs `MEMWAL_PRIVATE_KEY` and `MEMWAL_ACCOUNT_ID` in a gitignored `.env`. Your agents then point at your domain instead of agentsqa.xyz, and nothing about the memory changes: it is still encrypted under each user's passphrase before it leaves the box.
