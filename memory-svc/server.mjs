// Portable, private agent memory sidecar.
//
// Gives any agent a private memory on Walrus, organised as user -> folder ->
// items, through the MemWal client. Every item is encrypted under a key derived
// from the user's passphrase before it is stored, so at rest it is unreadable.
// On recall, the sidecar pulls a folder's items, decrypts them transiently with
// the passphrase supplied on that request, ranks them against the query, and
// returns the best matches. The passphrase and the plaintext are never stored.
//
// Reliability contract: a remember reply of ok:true means the relayer confirmed
// the write reached Walrus, and the reply carries the blob id as a receipt. A
// failed or timed-out write reports ok:false with the reason, never a silent
// success. A recall that could not scan the whole folder says so via truncated.
//
// It degrades gracefully. With no MemWal credentials the client is absent, so
// remember reports it could not store and recall returns nothing.

import { createServer } from "node:http";
import { scryptSync, randomBytes, createCipheriv, createDecipheriv, createHash } from "node:crypto";
import { MemWal } from "@mysten-incubation/memwal";

const PORT = Number(process.env.MEMORY_SVC_PORT || 4000);
const HOST = process.env.MEMORY_SVC_HOST || "0.0.0.0";
const MAX_BODY = 512 * 1024;
// First-pass pull size per folder. When the relayer reports more items than
// this, recall refetches up to FETCH_MAX so growth past one page does not
// silently drop older memories out of reach.
const FETCH_LIMIT = Number(process.env.AGENT_MEMORY_FETCH_LIMIT || 100);
const FETCH_MAX = Number(process.env.AGENT_MEMORY_FETCH_MAX || 500);
// How long to wait for the relayer to confirm a write before reporting failure.
// Kept under the HTTP client's own remember timeout so the caller always gets
// a definite answer from us rather than a transport timeout.
const REMEMBER_TIMEOUT_MS = Number(process.env.AGENT_MEMORY_REMEMBER_TIMEOUT_MS || 45000);
const RECALL_ATTEMPTS = 3;

const SERVER_URL = process.env.MEMWAL_SERVER_URL || "https://relayer.memwal.ai";

// Identities this service refuses to serve, comma separated. Retiring an
// identity revokes read and write access through this service; the ciphertext
// on Walrus stays sealed under its passphrase until it expires.
const RETIRED = new Set(
  (process.env.AGENT_MEMORY_RETIRED_USERS || "")
    .split(",").map((s) => s.trim().toLowerCase()).filter(Boolean)
);
function isRetired(user) {
  return RETIRED.has(user.toLowerCase());
}

// The MemWal client is used directly (not through a convenience wrapper) so
// write failures surface as errors we can report, instead of being logged and
// swallowed upstream. Without credentials the sidecar runs in disabled mode.
function createClient() {
  const key = process.env.MEMWAL_PRIVATE_KEY;
  const accountId = process.env.MEMWAL_ACCOUNT_ID;
  if (!key || !accountId) return null;
  try {
    return MemWal.create({ key, accountId, serverUrl: SERVER_URL });
  } catch (e) {
    console.error("agent-memory-svc: could not create the MemWal client:", e?.message || e);
    return null;
  }
}
const client = createClient();

// --- crypto: passphrase -> key, AES-256-GCM per item -----------------------

// A stable per-user salt, so the same user and passphrase derive the same key.
function keyFor(userKey, passphrase) {
  const salt = createHash("sha256").update(`agent-memory:${userKey}`).digest();
  return scryptSync(passphrase, salt, 32);
}

// Encrypt a string into "enc1:<base64(iv|tag|ciphertext)>".
function encrypt(key, plaintext) {
  const iv = randomBytes(12);
  const cipher = createCipheriv("aes-256-gcm", key, iv);
  const ct = Buffer.concat([cipher.update(plaintext, "utf8"), cipher.final()]);
  const tag = cipher.getAuthTag();
  return "enc1:" + Buffer.concat([iv, tag, ct]).toString("base64");
}

// Decrypt one item, or null if it is not ours or the passphrase is wrong.
function decrypt(key, blob) {
  if (typeof blob !== "string" || !blob.startsWith("enc1:")) return null;
  try {
    const raw = Buffer.from(blob.slice(5), "base64");
    const iv = raw.subarray(0, 12);
    const tag = raw.subarray(12, 28);
    const ct = raw.subarray(28);
    const decipher = createDecipheriv("aes-256-gcm", key, iv);
    decipher.setAuthTag(tag);
    return Buffer.concat([decipher.update(ct), decipher.final()]).toString("utf8");
  } catch {
    return null; // wrong passphrase or a different user's item
  }
}

// --- ranking: lightweight lexical overlap ----------------------------------

function tokenize(text) {
  return (text.toLowerCase().match(/[a-z0-9]+/g) || []).filter((t) => t.length > 2);
}

function relevance(queryTokens, text) {
  const set = new Set(tokenize(text));
  let score = 0;
  for (const q of queryTokens) if (set.has(q)) score += 1;
  return score;
}

// --- storage scope ----------------------------------------------------------

// Relayer namespaces are short, so a raw "user::folder" string would collapse
// different folders (and near-identical users) into one space. Hash the scope
// so the distinguishing bits land in the characters that are kept, and every
// (user, folder) pair gets its own namespace. The "avow-" prefix and 12-char
// slice match the format existing data was written under; changing either
// orphans every stored memory.
function labelOf(user, folder) {
  const f = (folder || "").trim();
  return f ? `${user}::${f}` : user;
}

function scopeOf(user, folder) {
  return createHash("sha256").update(`agent-mem-scope:${labelOf(user, folder)}`).digest("hex");
}

// --- folder generations: how forget works -----------------------------------
//
// Stored items carry no timestamps, so a folder cannot be forgotten by
// filtering "items before X". Instead each folder has a generation number,
// kept as plaintext "gen:N" markers in a control namespace. Data lives in a
// namespace derived from (user, folder, generation); forgetting bumps the
// generation, which moves the folder to a fresh namespace. The old ciphertext
// stays on Walrus until it expires, but this service never serves it again.
// Generation 0 uses the original namespace format, so folders written before
// this feature keep working unchanged.

function ctlNamespaceOf(user, folder) {
  const h = createHash("sha256").update(`agent-mem-ctl:${labelOf(user, folder)}`).digest("hex");
  return `avow-${h.slice(0, 12)}`;
}

function dataNamespaceOf(user, folder, generation) {
  if (!generation) return `avow-${scopeOf(user, folder).slice(0, 12)}`;
  const h = createHash("sha256")
    .update(`agent-mem-scope:${labelOf(user, folder)}::gen${generation}`).digest("hex");
  return `avow-${h.slice(0, 12)}`;
}

// The generation lookup costs one relayer round trip, so cache it briefly.
// A single sidecar instance serves all traffic, so this cache is authoritative
// enough; forget invalidates it immediately.
const GEN_TTL_MS = 30000;
const genCache = new Map(); // label -> { gen, at }
async function generationOf(user, folder) {
  const label = labelOf(user, folder);
  const hit = genCache.get(label);
  if (hit && Date.now() - hit.at < GEN_TTL_MS) return hit.gen;
  const { blobs } = await pullFolder(ctlNamespaceOf(user, folder), "generation marker", 50);
  let gen = 0;
  for (const b of blobs) {
    const m = /^gen:(\d{1,9})$/.exec(String(b).trim());
    if (m) gen = Math.max(gen, Number(m[1]));
  }
  genCache.set(label, { gen, at: Date.now() });
  return gen;
}

// --- relayer access ---------------------------------------------------------

// Store one encrypted item and wait for the relayer to confirm it reached
// Walrus. Returns the receipt; throws with the reason when the write fails.
async function storeConfirmed(namespace, blob) {
  return client.rememberAndWait(blob, namespace, { timeoutMs: REMEMBER_TIMEOUT_MS });
}

// Pull a folder's raw items with a couple of retries: the relayer occasionally
// drops a request, and a stalled lookup must never sink a live answer. Returns
// { blobs, total } where total is the relayer's count for the namespace.
async function pullFolder(namespace, query, limit) {
  let lastErr;
  for (let attempt = 0; attempt < RECALL_ATTEMPTS; attempt++) {
    try {
      const r = await client.recall({ query, limit, namespace });
      const blobs = (r.results ?? []).map((x) => x.text).filter(Boolean);
      return { blobs, total: Number(r.total ?? blobs.length) };
    } catch (e) {
      lastErr = e;
    }
  }
  throw lastErr ?? new Error("recall failed");
}

// --- http -------------------------------------------------------------------

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
      if (size > MAX_BODY) { reject(new Error("request body too large")); req.destroy(); return; }
      chunks.push(c);
    });
    req.on("end", () => {
      const raw = Buffer.concat(chunks).toString("utf8").trim();
      if (!raw) return resolve({});
      try { resolve(JSON.parse(raw)); } catch { reject(new Error("invalid json body")); }
    });
    req.on("error", reject);
  });
}

const server = createServer(async (req, res) => {
  try {
    const url = new URL(req.url, `http://${req.headers.host || "localhost"}`);

    if (req.method === "GET" && url.pathname === "/health") {
      return send(res, 200, { status: "ok", enabled: client !== null });
    }

    // Remember one item, encrypted, in this user's folder. Replies only after
    // the relayer confirms the write, and carries the Walrus blob id back as a
    // receipt. A failure is reported as ok:false with the reason.
    if (req.method === "POST" && url.pathname === "/remember") {
      const body = await readJson(req);
      const user = (body.user || "").toString().trim().toLowerCase();
      const passphrase = (body.passphrase || "").toString();
      const text = (body.text || "").toString().trim();
      const folder = (body.folder || "").toString().trim().toLowerCase();
      if (!user) return send(res, 400, { error: "user is required" });
      if (!passphrase) return send(res, 400, { error: "passphrase is required" });
      if (!text) return send(res, 400, { error: "text is required" });
      if (!client) return send(res, 200, { ok: false, enabled: false });
      if (isRetired(user)) {
        return send(res, 200, { ok: false, enabled: true, error: "This identity is retired on this service." });
      }
      const blob = encrypt(keyFor(user, passphrase), text);
      try {
        const gen = await generationOf(user, folder);
        const receipt = await storeConfirmed(dataNamespaceOf(user, folder, gen), blob);
        return send(res, 200, { ok: true, enabled: true, blob_id: receipt.blob_id || "" });
      } catch (e) {
        return send(res, 200, {
          ok: false,
          enabled: true,
          error: `write not confirmed: ${String(e?.message || e)}`,
        });
      }
    }

    // Recall from this user's folder: pull, decrypt, rank, return the best.
    // When the relayer reports more items than the first pull, refetch up to
    // FETCH_MAX so the whole folder is scanned; past that, say so honestly.
    if (req.method === "POST" && url.pathname === "/recall") {
      const body = await readJson(req);
      const user = (body.user || "").toString().trim().toLowerCase();
      const passphrase = (body.passphrase || "").toString();
      const query = (body.query || "").toString().trim();
      const folder = (body.folder || "").toString().trim().toLowerCase();
      const limit = Math.min(50, Math.max(1, Number(body.limit || 8)));
      if (!user) return send(res, 400, { error: "user is required" });
      if (!passphrase) return send(res, 400, { error: "passphrase is required" });
      if (!query) return send(res, 400, { error: "query is required" });
      if (!client) return send(res, 200, { enabled: false, records: [] });
      if (isRetired(user)) {
        return send(res, 200, { enabled: true, records: [], retired: true, scanned: 0, total: 0, truncated: false });
      }

      const namespace = dataNamespaceOf(user, folder, await generationOf(user, folder));
      let { blobs, total } = await pullFolder(namespace, query, FETCH_LIMIT);
      if (total > blobs.length && blobs.length >= FETCH_LIMIT) {
        ({ blobs, total } = await pullFolder(namespace, query, Math.min(total, FETCH_MAX)));
      }
      const key = keyFor(user, passphrase);
      const items = blobs.map((b) => decrypt(key, b)).filter((t) => t && t.length);
      const qTokens = tokenize(query);
      const ranked = items
        .map((text) => ({ text, score: relevance(qTokens, text) }))
        .sort((a, b) => b.score - a.score)
        .slice(0, limit)
        .map((x) => x.text);
      return send(res, 200, {
        enabled: true,
        records: ranked,
        scanned: items.length,
        total,
        truncated: total > blobs.length,
        // The folder holds notes but this passphrase opens none of them: a
        // wrong passphrase, not an empty memory. Callers must not confuse
        // the two, so the difference is stated explicitly.
        locked: blobs.length > 0 && items.length === 0,
      });
    }

    // Forget a folder: bump its generation so this service never serves the
    // old notes again. Requires proof of key: when the folder holds notes, the
    // supplied passphrase must decrypt at least one, so knowing someone's
    // identity string alone cannot wipe their folder's visibility. Honest
    // semantics: the old ciphertext stays on Walrus until it expires, sealed
    // under the passphrase; it is no longer reachable through this service.
    if (req.method === "POST" && url.pathname === "/forget") {
      const body = await readJson(req);
      const user = (body.user || "").toString().trim().toLowerCase();
      const passphrase = (body.passphrase || "").toString();
      const folder = (body.folder || "").toString().trim().toLowerCase();
      if (!user) return send(res, 400, { error: "user is required" });
      if (!passphrase) return send(res, 400, { error: "passphrase is required" });
      if (!client) return send(res, 200, { forgotten: false, enabled: false });
      if (isRetired(user)) {
        return send(res, 200, { forgotten: false, enabled: true, error: "This identity is retired on this service." });
      }
      const gen = await generationOf(user, folder);
      const { blobs } = await pullFolder(dataNamespaceOf(user, folder, gen), "proof of key", FETCH_LIMIT);
      if (!blobs.length) {
        // Nothing stored, nothing to forget; do not burn a generation on it.
        return send(res, 200, { forgotten: true, enabled: true, note: "The folder was already empty." });
      }
      const key = keyFor(user, passphrase);
      if (!blobs.some((b) => decrypt(key, b))) {
        return send(res, 403, { error: "The passphrase does not open this folder, so it cannot forget it." });
      }
      try {
        await storeConfirmed(ctlNamespaceOf(user, folder), `gen:${gen + 1}`);
        return send(res, 200, { forgotten: true, enabled: true });
      } catch (e) {
        return send(res, 200, {
          forgotten: false,
          enabled: true,
          error: `forget not confirmed: ${String(e?.message || e)}`,
        });
      } finally {
        // Even a timed-out marker write can land late on the relayer, so the
        // cached generation is stale either way.
        genCache.delete(labelOf(user, folder));
      }
    }

    return send(res, 404, { error: "not found" });
  } catch (e) {
    return send(res, 500, { error: String((e && e.message) || e) });
  }
});

server.listen(PORT, HOST, () => {
  const state = client ? "live" : "disabled (set MEMWAL_PRIVATE_KEY and MEMWAL_ACCOUNT_ID to enable)";
  console.log(`agent-memory-svc listening on http://${HOST}:${PORT} - memory ${state}, encrypted per user`);
});
