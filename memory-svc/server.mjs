// Portable, private agent memory sidecar.
//
// Gives any agent a private memory on Walrus, organised as user -> folder ->
// items, through the Avow SDK. Every item is encrypted under a key derived from
// the user's passphrase before it is stored, so at rest it is unreadable. On
// recall, the sidecar pulls a folder's items, decrypts them transiently with the
// passphrase supplied on that request, ranks them against the query, and returns
// the best matches. The passphrase and the plaintext are never stored.
//
// It degrades gracefully. With no MemWal credentials the Avow memory client is a
// no-op, so remember reports it could not store and recall returns nothing.

import { createServer } from "node:http";
import { scryptSync, randomBytes, createCipheriv, createDecipheriv, createHash } from "node:crypto";
import { createMemory } from "avow-sdk";

const PORT = Number(process.env.MEMORY_SVC_PORT || 4000);
const HOST = process.env.MEMORY_SVC_HOST || "0.0.0.0";
const MAX_BODY = 512 * 1024;
// How many raw items to pull from a folder before decrypting and ranking. A
// personal folder stays well under this.
const FETCH_LIMIT = Number(process.env.AGENT_MEMORY_FETCH_LIMIT || 100);

process.env.MEMWAL_SERVER_URL = process.env.MEMWAL_SERVER_URL || "https://relayer.memwal.ai";
const memory = createMemory();

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

// The Avow namespace helper truncates its input to the first 12 characters, so a
// raw "user::folder" string collapses different folders (and near-identical
// users) into one namespace. Hash the scope so the distinguishing bits land in
// those first 12 characters and every (user, folder) gets its own space.
function scopeOf(user, folder) {
  const f = (folder || "").trim();
  const label = f ? `${user}::${f}` : user;
  return createHash("sha256").update(`agent-mem-scope:${label}`).digest("hex");
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
      return send(res, 200, { status: "ok", enabled: memory.enabled });
    }

    // Remember one item, encrypted, in this user's folder.
    if (req.method === "POST" && url.pathname === "/remember") {
      const body = await readJson(req);
      const user = (body.user || "").toString().trim();
      const passphrase = (body.passphrase || "").toString();
      const text = (body.text || "").toString().trim();
      const folder = (body.folder || "").toString().trim();
      if (!user) return send(res, 400, { error: "user is required" });
      if (!passphrase) return send(res, 400, { error: "passphrase is required" });
      if (!text) return send(res, 400, { error: "text is required" });
      const blob = encrypt(keyFor(user, passphrase), text);
      await memory.remember(scopeOf(user, folder), blob);
      return send(res, 200, { ok: true, enabled: memory.enabled });
    }

    // Recall from this user's folder: pull, decrypt, rank, return the best.
    if (req.method === "POST" && url.pathname === "/recall") {
      const body = await readJson(req);
      const user = (body.user || "").toString().trim();
      const passphrase = (body.passphrase || "").toString();
      const query = (body.query || "").toString().trim();
      const folder = (body.folder || "").toString().trim();
      const limit = Math.min(50, Math.max(1, Number(body.limit || 8)));
      if (!user) return send(res, 400, { error: "user is required" });
      if (!passphrase) return send(res, 400, { error: "passphrase is required" });
      if (!query) return send(res, 400, { error: "query is required" });
      if (!memory.enabled) return send(res, 200, { enabled: false, records: [] });

      const scope = scopeOf(user, folder);
      const raw = await memory.recall(scope, query, FETCH_LIMIT);
      const key = keyFor(user, passphrase);
      const items = raw.map((b) => decrypt(key, b)).filter((t) => t && t.length);
      const qTokens = tokenize(query);
      const ranked = items
        .map((text) => ({ text, score: relevance(qTokens, text) }))
        .sort((a, b) => b.score - a.score)
        .slice(0, limit)
        .map((x) => x.text);
      return send(res, 200, { enabled: true, records: ranked });
    }

    return send(res, 404, { error: "not found" });
  } catch (e) {
    return send(res, 500, { error: String((e && e.message) || e) });
  }
});

server.listen(PORT, HOST, () => {
  const state = memory.enabled ? "live" : "disabled (set MEMWAL_PRIVATE_KEY and MEMWAL_ACCOUNT_ID to enable)";
  console.log(`agent-memory-svc listening on http://${HOST}:${PORT} — memory ${state}, encrypted per user`);
});
