// Reputation memory sidecar for Agent QA.
//
// Agent QA (Python) grades MCP endpoints. This service turns each verdict into
// portable, verifiable memory on Walrus through the Avow SDK, and lets any agent
// recall a tool's track record before trusting it. One shared registry: every
// grade is written under a single namespace, so a tool vetted once is vetted for
// everyone.
//
// It degrades gracefully. With no MemWal credentials the Avow memory client is a
// no-op, so remember silently skips and recall returns nothing, and Agent QA
// keeps working, just without the reputation layer.

import { createServer } from "node:http";
import { createMemory } from "avow-sdk";

const PORT = Number(process.env.MEMORY_SVC_PORT || 4000);
const HOST = process.env.MEMORY_SVC_HOST || "0.0.0.0";
// One shared reputation space for the whole registry (see the note above).
const REGISTRY_ID = process.env.AGENTQA_REGISTRY_ID || "agentqa-global-registry";
const MAX_BODY = 64 * 1024;

// Default to the relayer that currently serves encryption. The other published
// relayer host has an intermittently unavailable Seal sidecar, so pin the
// working one here while still allowing an override.
process.env.MEMWAL_SERVER_URL = process.env.MEMWAL_SERVER_URL || "https://relayer.memwal.ai";

// Reads MEMWAL_PRIVATE_KEY / MEMWAL_ACCOUNT_ID / MEMWAL_SERVER_URL from the env.
const memory = createMemory();

function send(res, status, body) {
  res.writeHead(status, { "content-type": "application/json" });
  res.end(JSON.stringify(body));
}

function readJson(req) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    req.on("data", (c) => {
      size += c.length;
      if (size > MAX_BODY) {
        reject(new Error("request body too large"));
        req.destroy();
        return;
      }
      chunks.push(c);
    });
    req.on("end", () => {
      const raw = Buffer.concat(chunks).toString("utf8").trim();
      if (!raw) return resolve({});
      try {
        resolve(JSON.parse(raw));
      } catch {
        reject(new Error("invalid json body"));
      }
    });
    req.on("error", reject);
  });
}

const server = createServer(async (req, res) => {
  try {
    const url = new URL(req.url, `http://${req.headers.host || "localhost"}`);

    if (req.method === "GET" && url.pathname === "/health") {
      return send(res, 200, { status: "ok", enabled: memory.enabled });
    }

    // Remember one verdict as a durable fact in the shared registry.
    if (req.method === "POST" && url.pathname === "/remember") {
      const body = await readJson(req);
      const text = typeof body.text === "string" ? body.text.trim() : "";
      if (!text) return send(res, 400, { error: "text is required" });
      await memory.remember(REGISTRY_ID, text);
      return send(res, 200, { ok: true, enabled: memory.enabled });
    }

    // Recall a tool's track record by meaning.
    if (req.method === "GET" && url.pathname === "/recall") {
      const query = (url.searchParams.get("query") || "").trim();
      if (!query) return send(res, 400, { error: "query is required" });
      const limit = Math.min(20, Math.max(1, Number(url.searchParams.get("limit") || 6)));
      const records = memory.enabled ? await memory.recall(REGISTRY_ID, query, limit) : [];
      return send(res, 200, { query, enabled: memory.enabled, records });
    }

    return send(res, 404, { error: "not found" });
  } catch (e) {
    return send(res, 500, { error: String((e && e.message) || e) });
  }
});

server.listen(PORT, HOST, () => {
  const state = memory.enabled ? "live" : "disabled (set MEMWAL_PRIVATE_KEY and MEMWAL_ACCOUNT_ID to enable)";
  console.log(`agent-qa memory-svc listening on http://${HOST}:${PORT} — reputation memory ${state}`);
});
