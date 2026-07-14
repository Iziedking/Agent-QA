#!/usr/bin/env node
// agent-memory-connect: the friendly door to Portable Agent Memory.
//
//   agent-memory setup     one-time: identity + blind-typed passphrase into the
//                          OS keychain (Credential Manager / Keychain / libsecret)
//   agent-memory status    show what is configured, never the secret
//   agent-memory reset     remove the stored identity and passphrase
//   agent-memory           (default) run the local MCP proxy an agent connects to
//
// The proxy speaks MCP over stdio to the agent and forwards to the remote
// memory over HTTPS, attaching the identity headers from the keychain at
// request time. No secret ever sits in an agent config file or an env var.

import { createRequire } from "node:module";
import { mkdirSync, readFileSync, writeFileSync, rmSync, existsSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const { Entry } = require("@napi-rs/keyring");

const DEFAULT_URL = "https://agentsqa.xyz/mcp";
const SERVICE = "agent-memory";
const CONFIG_DIR = join(homedir(), ".agent-memory");
const CONFIG_FILE = join(CONFIG_DIR, "config.json");
// Fallback secret location for machines with no OS credential store (headless
// servers). Written 0600 and used only with the user's explicit consent.
const SECRET_FILE = join(CONFIG_DIR, "secret");
const VERSION = "0.1.3";

// --- config (non-secret: identity and endpoint only) ------------------------

function loadConfig() {
  try {
    const cfg = JSON.parse(readFileSync(CONFIG_FILE, "utf8"));
    if (cfg && typeof cfg.user === "string" && cfg.user) return cfg;
  } catch {}
  return null;
}

function saveConfig(cfg) {
  mkdirSync(CONFIG_DIR, { recursive: true });
  writeFileSync(CONFIG_FILE, JSON.stringify(cfg, null, 2) + "\n", "utf8");
}

// Where the passphrase lives: the OS credential store when one exists, else
// the consented fallback file. Returns { pass, source } or nulls.
function getSecret(user) {
  try {
    const pass = new Entry(SERVICE, user).getPassword();
    if (pass) return { pass, source: "keyring" };
  } catch {}
  try {
    const pass = readFileSync(SECRET_FILE, "utf8").trim();
    if (pass) return { pass, source: "file" };
  } catch {}
  return { pass: null, source: null };
}

// --- prompts -----------------------------------------------------------------

function promptVisible(question) {
  return new Promise((resolvePrompt) => {
    process.stdout.write(question);
    const onData = (chunk) => {
      process.stdin.pause();
      process.stdin.off("data", onData);
      resolvePrompt(chunk.toString("utf8").replace(/\r?\n$/, "").trim());
    };
    process.stdin.resume();
    process.stdin.on("data", onData);
  });
}

// Blind-typed: nothing is echoed, like any password prompt.
function promptHidden(question) {
  return new Promise((resolvePrompt) => {
    process.stdout.write(question);
    const stdin = process.stdin;
    stdin.resume();
    if (stdin.isTTY) stdin.setRawMode(true);
    let value = "";
    const onData = (chunk) => {
      for (const ch of chunk.toString("utf8")) {
        if (ch === "\r" || ch === "\n") {
          if (stdin.isTTY) stdin.setRawMode(false);
          stdin.pause();
          stdin.off("data", onData);
          process.stdout.write("\n");
          return resolvePrompt(value);
        }
        if (ch === "\u0003") { // Ctrl+C
          if (stdin.isTTY) stdin.setRawMode(false);
          process.stdout.write("\n");
          process.exit(130);
        }
        if (ch === "\u0008" || ch === "\u007f") value = value.slice(0, -1); // backspace
        else value += ch;
      }
    };
    stdin.on("data", onData);
  });
}

// --- commands ----------------------------------------------------------------

async function setup() {
  console.log("Portable Agent Memory - one-time setup");
  console.log("The passphrase is typed blind and stored in this device's secure");
  console.log("credential store. It never sits in a config file or an env var.\n");

  const existing = loadConfig();
  const user = (await promptVisible(
    `Identity, the address that names your memory, e.g. you@example.com` +
    `${existing ? ` (Enter keeps ${existing.user})` : ""}: `
  )) || (existing ? existing.user : "");
  if (!user) { console.error("An identity is required."); process.exit(1); }

  const defaultUrl = (existing && existing.url) || DEFAULT_URL;
  const urlIn = await promptVisible(
    `Memory endpoint. Press Enter for the hosted service (${defaultUrl}),\n` +
    `or paste your own if you self-host: `
  );
  const url = urlIn || defaultUrl;

  const pass = await promptHidden("Passphrase (blind typed): ");
  if (!pass) { console.error("A passphrase is required."); process.exit(1); }
  const again = await promptHidden("Passphrase again: ");
  if (pass !== again) { console.error("The passphrases do not match. Nothing was stored."); process.exit(1); }

  let storedIn = "the OS credential store";
  try {
    new Entry(SERVICE, user).setPassword(pass);
    // A keyring write supersedes any earlier fallback file.
    if (existsSync(SECRET_FILE)) rmSync(SECRET_FILE);
  } catch {
    // No credential store: normal on a headless server (no Keychain, no
    // Credential Manager, no Secret Service daemon). Offer the honest
    // fallback rather than failing: a file only this account can read.
    console.log("\nThis machine has no OS credential store (common on headless servers).");
    const yn = (await promptVisible("Store the passphrase in ~/.agent-memory/secret, readable only by this account? [y/N]: ")).toLowerCase();
    if (yn !== "y" && yn !== "yes") {
      console.error("Nothing was stored. On this machine, configure your MCP client with the");
      console.error("X-Memory-User and X-Memory-Passphrase headers directly instead.");
      process.exit(1);
    }
    mkdirSync(CONFIG_DIR, { recursive: true });
    writeFileSync(SECRET_FILE, pass + "\n", { encoding: "utf8", mode: 0o600 });
    storedIn = "~/.agent-memory/secret (file permissions 600)";
  }
  saveConfig({ user, url });

  // Verify against the live memory now, so a mistyped passphrase surfaces at
  // setup instead of masquerading as an empty memory later. The default space
  // is probed because the folder signpost convention keeps a note there.
  try {
    const probe = await fetch(new URL("/recall", url), {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ user_key: user, passphrase: pass, query: "setup verification probe", folder: "" }),
      signal: AbortSignal.timeout(30000),
    });
    const d = await probe.json();
    if (d && d.locked) {
      console.log("\nWARNING: this identity has existing notes, and this passphrase opens");
      console.log("NONE of them. If you have used this memory before, the passphrase is");
      console.log("almost certainly mistyped. Run setup again before wiring any agent.");
    } else if (d && Array.isArray(d.records) && d.records.length) {
      console.log("\nPassphrase verified: it opens this identity's existing notes.");
    } else {
      console.log("\nNo existing notes found in the default space. Fine for a brand new");
      console.log("identity; if you expected notes here, re-check the identity spelling.");
    }
  } catch {
    console.log("\nCould not reach the endpoint to verify right now. Stored anyway.");
  }

  const self = resolve(fileURLToPath(import.meta.url));
  // Installed from npm (running out of a node_modules) means npx can find us
  // by name; a repo checkout needs the explicit node path.
  const viaNpm = self.includes("node_modules");
  console.log(`\nStored in ${storedIn}. Wire an agent to this device's memory with one line:\n`);
  if (viaNpm) {
    console.log("  claude code   claude mcp add -s user agent-memory -- npx -y agent-memory-connect");
    console.log('  codex         [mcp_servers.agent-memory] in ~/.codex/config.toml:');
    console.log('                command = "npx"  args = ["-y", "agent-memory-connect"]');
    console.log('  cursor, etc.  { "command": "npx", "args": ["-y", "agent-memory-connect"] }');
  } else {
    console.log(`  claude code   claude mcp add -s user agent-memory -- node "${self}"`);
    console.log(`  cursor, etc.  { "command": "node", "args": ["${self.replace(/\\/g, "\\\\")}"] }`);
  }
  console.log("\n(-s user makes the memory available in every project on this device;");
  console.log("drop it to wire only the current project. Full per-agent guides:");
  console.log("https://agentsqa.xyz, DOCS, Wire your agent.)");
  console.log("\nEvery agent on this device now shares the same memory, and none");
  console.log("of them ever sees the passphrase.");
}

function status() {
  const cfg = loadConfig();
  if (!cfg) { console.log("Not set up. Run: agent-memory setup"); return; }
  const { source } = getSecret(cfg.user);
  const where =
    source === "keyring" ? "stored in the OS credential store"
    : source === "file" ? "stored in ~/.agent-memory/secret (file permissions 600)"
    : "MISSING - run setup again";
  console.log(`identity   ${cfg.user}`);
  console.log(`endpoint   ${cfg.url || DEFAULT_URL}`);
  console.log(`passphrase ${where}`);
}

function reset() {
  const cfg = loadConfig();
  if (cfg) { try { new Entry(SERVICE, cfg.user).deletePassword(); } catch {} }
  if (existsSync(SECRET_FILE)) rmSync(SECRET_FILE);
  if (existsSync(CONFIG_FILE)) rmSync(CONFIG_FILE);
  console.log("Removed the stored identity and passphrase from this device.");
}

// --- the proxy: stdio on the agent side, HTTPS with headers on ours ----------

async function serve() {
  const cfg = loadConfig();
  if (!cfg) {
    console.error("agent-memory: not set up on this device. Run: agent-memory setup");
    process.exit(1);
  }
  const { pass } = getSecret(cfg.user);
  if (!pass) {
    console.error("agent-memory: no stored passphrase on this device. Run: agent-memory setup");
    process.exit(1);
  }

  const { Client } = await import("@modelcontextprotocol/sdk/client/index.js");
  const { StreamableHTTPClientTransport } = await import("@modelcontextprotocol/sdk/client/streamableHttp.js");
  const { Server } = await import("@modelcontextprotocol/sdk/server/index.js");
  const { StdioServerTransport } = await import("@modelcontextprotocol/sdk/server/stdio.js");
  const { ListToolsRequestSchema, CallToolRequestSchema } = await import("@modelcontextprotocol/sdk/types.js");

  const remote = new Client({ name: "agent-memory-connect", version: VERSION });
  const transport = new StreamableHTTPClientTransport(new URL(cfg.url || DEFAULT_URL), {
    requestInit: {
      headers: {
        "X-Memory-User": cfg.user,
        "X-Memory-Passphrase": pass,
      },
    },
  });
  await remote.connect(transport);

  const server = new Server(
    { name: "Portable Agent Memory", version: VERSION },
    { capabilities: { tools: {} }, instructions: remote.getInstructions() }
  );
  server.setRequestHandler(ListToolsRequestSchema, async () => remote.listTools());
  // A confirmed Walrus write can take tens of seconds; give calls real room.
  server.setRequestHandler(CallToolRequestSchema, async (req) =>
    remote.callTool(req.params, undefined, { timeout: 120000 })
  );

  await server.connect(new StdioServerTransport());
  // When the agent that spawned us goes away, so do we.
  process.stdin.on("end", () => process.exit(0));
  process.stdin.on("close", () => process.exit(0));
}

// --- entry -------------------------------------------------------------------

const cmd = (process.argv[2] || "").toLowerCase();
if (cmd === "setup") await setup();
else if (cmd === "status") status();
else if (cmd === "reset") reset();
else if (cmd === "" || cmd === "serve") await serve();
else {
  console.error(`Unknown command "${cmd}". Use: setup | status | reset | (no command runs the proxy)`);
  process.exit(1);
}
