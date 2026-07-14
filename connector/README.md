# agent-memory-connect

The friendly door to Portable Agent Memory. Set up once per device, then wire any MCP agent with one line and no secrets in any config file.

```
npx agent-memory-connect setup
```

Or from a checkout of the repo:

```
npm install
node bin.mjs setup
```

## What setup asks

Three things, and only the first two need thought:

1. **Identity.** The address that names your memory, such as an email. Not a secret; the same identity reaches the same memory from every device and every agent.
2. **Passphrase.** The only key to your notes. Typed blind (nothing shows while you type), confirmed twice, and stored in the operating system's credential store: Credential Manager on Windows, Keychain on macOS, the Secret Service on desktop Linux. It never sits in a config file, an env var, or a shell history.
3. **Endpoint.** Just press Enter. The default is the hosted service at `https://agentsqa.xyz/mcp`; you only type something here if you run your own deployment.

Setup then verifies the passphrase against your existing notes on the spot, so a typo surfaces immediately instead of masquerading as an empty memory. A brand new identity simply reports that no notes exist yet.

On a headless server with no credential store, setup says so and offers the honest fallback: a file at `~/.agent-memory/secret` readable only by your account (permissions 600), written only with your explicit yes. `status` always tells you which store holds the passphrase.

Then add the printed line to any agent, for example:

```
claude mcp add -s user agent-memory -- npx -y agent-memory-connect
```

`-s user` makes the memory available in every project on the device; drop it to wire only the current project directory.

The agent talks to a local MCP proxy over stdio. The proxy reads the passphrase from the credential store at runtime and attaches the identity headers to every HTTPS request to the memory endpoint. Every agent on the device shares the same memory; none of them ever sees the passphrase.

Other commands:

- `npx agent-memory-connect status` shows the configured identity and endpoint, and whether a passphrase is held. Never the value.
- `npx agent-memory-connect reset` removes the stored identity and passphrase from the device.

Self-hosters pass their own endpoint during setup; everything else is identical.
