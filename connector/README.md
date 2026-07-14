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

Setup asks for your identity and your passphrase. The passphrase is typed blind, confirmed twice, and stored in the operating system's credential store: Credential Manager on Windows, Keychain on macOS, libsecret on Linux. It never sits in a config file, an env var, or a shell history.

Then add the printed line to any agent, for example:

```
claude mcp add agent-memory -- npx -y agent-memory-connect
```

The agent talks to a local MCP proxy over stdio. The proxy reads the passphrase from the credential store at runtime and attaches the identity headers to every HTTPS request to the memory endpoint. Every agent on the device shares the same memory; none of them ever sees the passphrase.

Other commands:

- `npx agent-memory-connect status` shows the configured identity and endpoint, and whether a passphrase is held. Never the value.
- `npx agent-memory-connect reset` removes the stored identity and passphrase from the device.

Self-hosters pass their own endpoint during setup; everything else is identical.
