# Try Portable Agent Memory

Your coding agent forgets everything when the session ends. This gives it a
memory that persists, follows you between machines and between agents, and is
encrypted so that only your passphrase can read it.

Free to try. No account, no wallet, no signup.

## Setup, two commands

```bash
npm install -g agent-memory-connect
agent-memory setup
```

Then wire your agent to it:

```bash
claude mcp add -s user agent-memory -- agent-memory
```

Install it globally rather than running it through `npx`. `npx` re-resolves the
package from the registry every single launch, which measured **16.5 seconds**
against **4.0 seconds** for the installed binary on Windows. Some clients give
an MCP server only 30 seconds to start and will drop the connection, and Codex
does exactly that.

The first command asks three things:

- **Identity** — an address that names your memory, like `you@example.com`. It
  is a label, not an account; nothing is emailed to it.
- **Endpoint** — press Enter for the hosted service.
- **Passphrase** — typed blind, twice. This is the only thing that can decrypt
  your notes.

It stores the passphrase in your OS credential store (Keychain on macOS,
Credential Manager on Windows, Secret Service on Linux), then checks it against
the live service so a typo surfaces now rather than looking like an empty memory
a week later.

**The passphrase is not recoverable.** Nobody, including whoever runs the
server, can read your notes or reset it. Put it in your password manager before
you carry on.

Not on Claude Code? Same setup, different second step:

```toml
# ~/.codex/config.toml
[mcp_servers.agent-memory]
command = "agent-memory"
# Codex allows 30s for a server to start and is strict about it. Raise this if
# you see "MCP client for agent-memory timed out", which usually means the
# command is still going through npx.
startup_timeout_sec = 60
```

```json
// Cursor and most other MCP clients
{ "command": "agent-memory" }
```

On Windows some clients cannot launch a `.cmd` shim directly. If yours reports
the command as not found, wrap it:

```toml
command = "cmd"
args = ["/c", "agent-memory"]
```

`-s user` makes the memory available in every project on the machine. Drop it to
wire just the current one.

## What to actually try

Talk normally. Your agent gets five tools (`remember`, `recall`, `forget`,
`list_files`, `fetch_file`) and decides when to use them.

**1. Store something, then prove it survives a restart.**

> Remember that I prefer tabs over spaces and that this project deploys with
> `make ship`, not `npm run deploy`.

Quit the session. Start a fresh one. Ask:

> What do you know about how I like to work?

That is the whole product in one move. If it comes back, everything else is
detail.

**2. Prove it crosses tools.** Wire a second agent on the same machine with the
same identity, then ask *it* what it knows about you. Same memory, different
agent.

**3. Prove it crosses machines.** Run `setup` on a second computer with the same
identity and passphrase. Your notes are there.

**4. Try to break the encryption.** Run `npx agent-memory-connect setup` again
with the same identity but a *wrong* passphrase. Recall should return nothing
and say the folder is locked — not silently pretend your memory is empty. That
distinction is the thing worth checking hardest, because a memory that returns
"nothing here" when it means "I can't read this" would be actively dangerous.

**5. Use folders.** Ask it to remember something "in the `work` folder" and
something else in `personal`, then recall from one. Folders are separate spaces
under the same identity.

## Where to be sceptical

Things worth poking at, honestly:

- **Search quality.** Recall ranks your notes against the query with keyword
  relevance, not embeddings, because the server never sees your plaintext and so
  cannot embed it. Short factual notes work well; long rambling ones less so.
- **Latency.** A recall is typically about a second. Tell us if you see worse.
- **Agents forgetting to call it.** The most common disappointment is not the
  memory failing, it is your agent never thinking to use it. If that happens,
  say "check your memory first" and see whether the tool itself was fine.
- **Nothing is versioned.** `forget` retires a folder rather than surgically
  deleting one note.

## Under the hood, briefly

Notes are encrypted on the server with a key derived from your passphrase, then
batched into [Walrus](https://walrus.xyz) on Sui mainnet with a two-year storage
term. What is stored is ciphertext; the passphrase never leaves your machine and
is never written to a config file. Writes return as soon as they are durable
locally and reach Walrus on the next batch, so a note is never lost waiting on a
chain.

Source: <https://github.com/Iziedking/Agent-QA>. Self-hosting is a supported path
if you would rather not trust the hosted service; paste your own endpoint at
setup.

## If something breaks

| Symptom | What it means |
|---|---|
| "the folder is locked" | The passphrase does not match the one the notes were written with. Re-run `setup`. |
| "no OS credential store" | Normal on headless servers. It offers a `600` file at `~/.agent-memory/secret`; decline and use the `X-Memory-User` / `X-Memory-Passphrase` headers directly if you would rather. |
| Agent says memory is unavailable | Check <https://agentsqa.xyz/health>. If that is fine, reconnect the MCP server in your client. |
| Recall returns nothing you expected | Check the identity spelling first. A typo silently creates a different, empty memory. |

`npx agent-memory-connect status` shows what identity and endpoint this machine
is wired to. `reset` clears it.

## Leaving a review

If it works and you want to say so publicly, the honest version: the review has
to come from a real purchase on the OKX marketplace, which is more work than
trying it.

It needs an OKX agentic wallet, your own user-role agent identity, and one cent
of USDT0 on X Layer:

```bash
onchainos wallet login                       # AK login with your OKX API keys
onchainos agent create --role user           # your reviewer identity; note the id
```

Then buy once through the task flow and review that task:

```bash
onchainos agent feedback-submit \
  --agent-id 5800 \
  --creator-id <your agent id> \
  --task-id <the task you bought> \
  --score 5.00 \
  --description "what you actually thought"
```

The service is agent `5800` on <https://okx.ai>. If that is more ceremony than
you fancy, plain feedback in a message is just as useful to us and considerably
less faff.

Honest note on why it is that strict: OKX ties reviews to paid tasks so ratings
cannot be manufactured. Which is the right call, and it does mean genuine
reviews are hard-won.
