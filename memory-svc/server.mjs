// Portable, private agent memory sidecar.
//
// Gives any agent a private memory on Walrus, organised as user -> folder ->
// items. Every item is encrypted under a key derived from the user's passphrase
// before it is stored, so at rest it is unreadable. On recall, the sidecar
// gathers a folder's items, decrypts them transiently with the passphrase
// supplied on that request, ranks them against the query, and returns the best
// matches. The passphrase and the plaintext are never stored.
//
// STORAGE SHAPE. Walrus is the only backend. A write appends to a local
// write-ahead buffer and returns as soon as that append is durable, so no
// request ever waits on a chain. A timer batches the buffer into one Walrus
// quilt (see walrus-notes.mjs), and items move from the buffer to a local cache
// only once Walrus has confirmed them. Reads are served from buffer plus cache,
// never from the network.
//
// Reads are local by necessity, not laziness. A namespace's items end up spread
// across every quilt that ever held it, so reading one folder from Walrus would
// mean fetching hundreds of blobs. Because a Walrus blob is immutable, a local
// copy can never go stale, so this costs no correctness. If the volume is lost,
// the cache is rebuilt from the quilts on chain.
//
// This replaced the MemWal relayer, which was a single point of failure three
// times: an upload pause on 2026-07-15, a total 502 outage on 2026-07-27, and a
// 401 on 2026-08-04 after it shipped account-bound nonce auth. Walrus itself
// stayed healthy throughout. Encryption was always ours, so nothing was given
// up by dropping it.

import { createServer } from "node:http";
import { scryptSync, randomBytes, createCipheriv, createDecipheriv, createHash } from "node:crypto";
import { ensure as ensureWalrus } from "./walrus-files.mjs";
import { ensure as ensureZeroG } from "./zerog-files.mjs";
import {
  ensureClients as ensureWalrusClients,
  balances as walrusBalances,
  blobAvailable as walrusBlobAvailable,
} from "./walrus-client.mjs";
import {
  flush as flushQuilt,
  readQuilt,
  readIndex as readQuiltIndex,
  writeIndex as writeQuiltIndex,
  recoverQuilts,
  renewExpiring,
} from "./walrus-notes.mjs";
import {
  appendItem as localAppend,
  readItems as localRead,
  listNamespaces as localNamespaces,
  pendingCount as localPending,
  readCached as localCached,
  listCachedNamespaces as localCachedNamespaces,
  cachedCount as localCachedCount,
  promoteToCache as localPromote,
  writeCache as localWriteCache,
} from "./localstore.mjs";
import { AGON_MEMORY_AGENT, runAgonMemoryChallenge } from "./agon-agent.mjs";

const PORT = Number(process.env.MEMORY_SVC_PORT || 4000);
const HOST = process.env.MEMORY_SVC_HOST || "0.0.0.0";
const MAX_BODY = 512 * 1024;
// File uploads carry base64 bytes, so they need a much larger body cap than a
// note. base64 inflates by about a third, so this allows roughly a 9 MB file.
const FILE_MAX_BODY = Number(process.env.AGENT_MEMORY_FILE_MAX_BYTES || 12 * 1024 * 1024);
// Retained so recall's paging logic keeps its shape. Both tiers are local now,
// so a read returns the whole namespace in one pass and the second fetch never
// triggers; they stay because the truncated flag they feed is part of the API.
const FETCH_LIMIT = Number(process.env.AGENT_MEMORY_FETCH_LIMIT || 100);
const FETCH_MAX = Number(process.env.AGENT_MEMORY_FETCH_MAX || 500);
// How often the write-ahead buffer is batched into a Walrus quilt.
//
// This is the only real cost dial. Walrus charges per blob, near enough flat up
// to 64 KB, so one flush costs the same whether it carries one note or a
// thousand: 0.2994 WAL and 0.0100 SUI, measured on mainnet 2026-08-04. Six
// hours means at most four flushes a day (~1.2 WAL/day) and means a note is
// buffer-only for at most six hours. A cycle with an empty buffer writes
// nothing and costs nothing, so the real bill tracks how much is actually
// written rather than the ceiling.
const FLUSH_INTERVAL_MS = Number(process.env.AGENT_MEMORY_FLUSH_INTERVAL_MS || 6 * 3600 * 1000);
// Cap on how many items go into a single quilt, so one enormous backlog cannot
// build a blob past the flat-rate band and turn one cheap write into a dear one.
const FLUSH_MAX_ITEMS = Number(process.env.AGENT_MEMORY_FLUSH_MAX_ITEMS || 2000);
let flushing = false;
let lastFlush = null; // { at, blobId, count } of the most recent successful flush
let lastFlushError = null;
// When the next batch is due. Reported so a buffered note reads as "waiting
// until X" rather than "stuck": without it, a healthy buffer and a broken
// flusher look identical from outside.
let nextFlushAt = null;

// How near expiry a quilt must be before it is extended, in Walrus epochs.
// Quilts are written for 53 epochs (~2 years), so this should sit idle for a
// very long time. It runs daily anyway: the renewal job that saves the data has
// to already exist on the day it is first needed. Every one of our 36 testnet
// file blobs expired unnoticed for want of exactly this.
const RENEW_WITHIN_EPOCHS = Number(process.env.AGENT_MEMORY_RENEW_WITHIN_EPOCHS || 5);
const RENEW_INTERVAL_MS = Number(process.env.AGENT_MEMORY_RENEW_INTERVAL_MS || 24 * 3600 * 1000);

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

// Memory is enabled when there is a funded Sui key to store it with. Without
// one, notes would live only on a container's disk with nothing behind them,
// which is not the product, so the sidecar says so rather than pretending.
// Checked synchronously here so every request path has a cheap answer; the
// clients themselves are built lazily on first use.
const client = process.env.WALRUS_SUI_KEY ? true : null;

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

// Encrypt raw bytes into a single buffer "iv|tag|ciphertext" (no base64; the
// file blob is stored as bytes on Walrus). Same cipher as the text path.
function encryptBytes(key, buf) {
  const iv = randomBytes(12);
  const cipher = createCipheriv("aes-256-gcm", key, iv);
  const ct = Buffer.concat([cipher.update(buf), cipher.final()]);
  return Buffer.concat([iv, cipher.getAuthTag(), ct]);
}

// Decrypt an "iv|tag|ciphertext" buffer; throws if the passphrase is wrong.
function decryptBytes(key, buf) {
  const iv = buf.subarray(0, 12);
  const tag = buf.subarray(12, 28);
  const ct = buf.subarray(28);
  const decipher = createDecipheriv("aes-256-gcm", key, iv);
  decipher.setAuthTag(tag);
  return Buffer.concat([decipher.update(ct), decipher.final()]);
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

// File manifests live in their own namespace, separate from notes, so a normal
// recall of a folder never surfaces file entries and vice versa.
function fileIndexNamespaceOf(user, folder) {
  const h = createHash("sha256").update(`agent-mem-files:${labelOf(user, folder)}`).digest("hex");
  return `avow-${h.slice(0, 12)}`;
}

const FILE_MANIFEST_PREFIX = "aqafile1:";

// --- file blob backends: Walrus primary, 0G fallback ------------------------
//
// A file's encrypted bytes go to Walrus first; if that write fails, they go to
// 0G instead, so a single storage network being down does not lose the upload.
// The stored reference records which backend holds it, as "walrus:<blobId>" or
// "0g:<rootHash>". A bare id with no prefix is a legacy Walrus blob.

async function putEncryptedBlob(encBytes) {
  const bytes = new Uint8Array(encBytes);
  const walrus = await ensureWalrus();
  if (walrus.enabled) {
    try {
      return "walrus:" + (await walrus.putBlob(bytes));
    } catch (e) {
      console.error("walrus blob write failed, trying 0g:", e?.message || e);
    }
  }
  const zerog = await ensureZeroG();
  if (zerog.enabled) {
    return "0g:" + (await zerog.putBlob(bytes));
  }
  throw new Error(
    walrus.enabled ? "walrus failed and 0g is not configured" : "no file storage backend is configured"
  );
}

async function anyFileBackendEnabled() {
  return (await ensureWalrus()).enabled || (await ensureZeroG()).enabled;
}

// Split a stored reference into its backend and id.
function parseRef(ref) {
  const i = String(ref).indexOf(":");
  if (i > 0) {
    const prefix = ref.slice(0, i);
    if (prefix === "walrus" || prefix === "0g") return { store: prefix, id: ref.slice(i + 1) };
  }
  return { store: "walrus", id: ref }; // a bare id is a legacy Walrus blob
}

async function getBlobByRef(ref) {
  const { store, id } = parseRef(ref);
  const backend = store === "0g" ? await ensureZeroG() : await ensureWalrus();
  if (!backend.enabled) throw new Error(`${store} storage is not configured on this server`);
  return backend.getBlob(id);
}

// Can this reference still be fetched? true, false, or null for "cannot tell".
//
// Only Walrus can answer. 0G exposes no equivalent status check, so those come
// back null rather than being guessed at, and null is reported as unknown, never
// as missing.
async function refAvailable(ref) {
  const { store, id } = parseRef(ref);
  if (store !== "walrus") return null;
  try {
    return await walrusBlobAvailable(id);
  } catch {
    return null;
  }
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

// --- storage ----------------------------------------------------------------

// Store one encrypted item.
//
// The write goes to the local write-ahead buffer and returns as soon as that
// append is durable, so a remember never waits on a chain: writes are fast and
// cannot fail because a network is having a bad day. The flush timer is what
// carries it to Walrus. Callers get a receipt marked local, and a later receipt
// naming the quilt once it has been batched.
function storeItem(namespace, blob) {
  localAppend(namespace, blob);
  const digest = createHash("sha256").update(blob).digest("hex").slice(0, 16);
  // "buffered", not "local". The prefix was coined when the local store was an
  // emergency fallback during a relayer outage, so "local:" meant "we could not
  // reach the real store". It now means the opposite: this is the normal, and
  // only, write path, and the note is durable the moment it lands here. Reading
  // the old prefix as a failure is a mistake real users made, including us.
  return { blob_id: "buffered:" + digest, buffered: true };
}

// Batch everything in the buffer into one Walrus quilt.
//
// Items move from buffer to cache only after Walrus confirms the write, and the
// cache append happens before the buffer removal, so a crash at any point
// leaves an item either still pending or duplicated, never lost. A duplicate is
// harmless: the read path dedupes.
//
// Never throws. A failed flush leaves everything pending for the next cycle,
// which is the whole point of a write-ahead buffer.
async function flushToWalrus() {
  if (!client || flushing) return null;
  const batches = [];
  let total = 0;
  for (const namespace of localNamespaces()) {
    const items = localRead(namespace);
    if (!items.length) continue;
    const room = FLUSH_MAX_ITEMS - total;
    if (room <= 0) break;
    const take = items.slice(0, room);
    batches.push({ namespace, items: take });
    total += take.length;
  }
  // Nothing buffered: write nothing, pay nothing. At four cycles a day this is
  // what keeps the bill tracking real traffic instead of the clock.
  if (!total) return null;

  flushing = true;
  try {
    const entry = await flushQuilt(batches);
    for (const { namespace, items } of batches) localPromote(namespace, items);
    lastFlush = { at: entry.at, blobId: entry.blobId, count: entry.count };
    lastFlushError = null;
    console.log(
      `agent-memory-svc: flushed ${entry.count} item(s) across ${batches.length} ` +
        `namespace(s) into quilt ${entry.blobId}, stored to epoch ${entry.endEpoch}`
    );
    return entry;
  } catch (e) {
    lastFlushError = String(e?.message || e);
    console.warn(
      `agent-memory-svc: flush failed, ${total} item(s) stay buffered for the ` +
        `next cycle: ${lastFlushError}`
    );
    return null;
  } finally {
    flushing = false;
  }
}

// Rebuild the local cache from the quilts on Walrus.
//
// This is the disaster path: the volume was lost, or a fresh instance came up
// against a wallet that already holds memory. It reads every quilt this wallet
// owns and rewrites the cache from their contents, which is what makes the
// local cache a cache rather than the only copy. Expired or unreadable quilts
// are skipped rather than fatal, so one dead blob cannot block the rest.
async function rehydrateFromWalrus() {
  if (!client) return { quilts: 0, items: 0, skipped: 0 };
  // Trust the local index when it is there, and fall back to a chain scan when
  // it is not, which is exactly the case this function exists for.
  let quilts = readQuiltIndex();
  if (!quilts.length) {
    quilts = await recoverQuilts();
    if (quilts.length) writeQuiltIndex(quilts);
  }

  const byNamespace = new Map();
  let skipped = 0;
  for (const q of quilts) {
    let contents = null;
    try {
      contents = await readQuilt(q.blobId);
    } catch (e) {
      console.warn(`agent-memory-svc: could not read quilt ${q.blobId}: ${String(e?.message || e)}`);
    }
    if (!contents) {
      skipped++;
      continue;
    }
    for (const [ns, items] of contents) {
      const list = byNamespace.get(ns) ?? [];
      list.push(...items);
      byNamespace.set(ns, list);
    }
  }

  let items = 0;
  for (const [ns, list] of byNamespace) {
    // Order is oldest quilt first, and duplicates can exist where a crash
    // landed between the cache append and the buffer removal, so dedupe while
    // preserving order.
    const unique = [...new Set(list)];
    localWriteCache(ns, unique);
    items += unique.length;
  }
  console.log(
    `agent-memory-svc: rehydrated ${items} item(s) across ${byNamespace.size} ` +
      `namespace(s) from ${quilts.length - skipped} quilt(s), ${skipped} unreadable`
  );
  return { quilts: quilts.length, items, skipped };
}

// Gather a folder's raw items from both local tiers.
//
// Reads never touch the network. The cache holds what Walrus has confirmed, the
// buffer holds what has not been flushed yet, and a note must be recallable the
// instant it is written, so both count. Deduped because a crash between the
// cache append and the buffer removal can leave an item in both.
//
// The signature keeps `query` and `limit` for its callers, though neither is
// used any more: ranking was always done here against the decrypted text, so
// the relayer's query was never what ordered the results.
async function pullFolder(namespace, _query, _limit) {
  const cached = localCached(namespace);
  const pending = localRead(namespace);
  if (!pending.length) return { blobs: cached, total: cached.length };
  const seen = new Set(cached);
  const merged = cached.concat(pending.filter((b) => !seen.has(b)));
  return { blobs: merged, total: merged.length };
}

// --- http -------------------------------------------------------------------

function send(res, status, body) {
  res.writeHead(status, { "content-type": "application/json" });
  res.end(JSON.stringify(body));
}

function readJson(req, maxBytes = MAX_BODY) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    req.on("data", (c) => {
      size += c.length;
      if (size > maxBytes) { reject(new Error("request body too large")); req.destroy(); return; }
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
      // These numbers are what make a storage problem legible from outside.
      // Recall keeps answering from local tiers whatever Walrus is doing, so
      // without them a flush that has been failing for a week looks identical
      // to one that is working: the only symptom is buffered_local never
      // falling and last_flush going stale.
      const quilts = readQuiltIndex();
      return send(res, 200, {
        status: "ok",
        enabled: client !== null,
        buffered_local: localPending(),
        cached_local: localCachedCount(),
        quilts: quilts.length,
        last_flush: lastFlush,
        next_flush_at: nextFlushAt ? new Date(nextFlushAt).toISOString() : null,
        last_flush_error: lastFlushError,
      });
    }

    if (req.method === "GET" && url.pathname === "/agon/v1/agent") {
      return send(res, 200, {
        protocol: "agon-playground/1",
        agent: AGON_MEMORY_AGENT,
        challenge: "/agon/v1/challenge",
        writesPerformed: false,
      });
    }

    // Read-only adversarial proof surface for AGON Arena. It exercises the
    // memory ranking and instruction-quarantine policy without accepting an
    // identity, passphrase, storage mutation, payment, or wallet action.
    if (req.method === "POST" && url.pathname === "/agon/v1/challenge") {
      const body = await readJson(req, 32 * 1024);
      try {
        return send(res, 200, runAgonMemoryChallenge(body));
      } catch (error) {
        return send(res, 400, { error: String(error?.message || error) });
      }
    }

    // Wallet and storage detail, kept off /health because it costs two chain
    // reads. This is where to look when a flush is failing and the reason might
    // be an empty wallet.
    if (req.method === "GET" && url.pathname === "/storage") {
      const quilts = readQuiltIndex();
      let wallet = null;
      let walletError = null;
      try {
        const [state, bal] = await Promise.all([ensureWalrusClients(), walrusBalances()]);
        wallet = {
          address: state.address ?? null,
          network: state.network ?? null,
          sui: bal?.sui ?? null,
          wal: bal?.wal ?? null,
        };
      } catch (e) {
        walletError = String(e?.message || e);
      }
      return send(res, 200, {
        enabled: client !== null,
        buffered_local: localPending(),
        cached_local: localCachedCount(),
        namespaces: localCachedNamespaces().length,
        quilts: quilts.length,
        newest_quilt: quilts.length ? quilts[quilts.length - 1] : null,
        last_flush: lastFlush,
        next_flush_at: nextFlushAt ? new Date(nextFlushAt).toISOString() : null,
        last_flush_error: lastFlushError,
        wallet,
        wallet_error: walletError,
      });
    }

    // Force a flush now rather than waiting for the timer. Useful after a
    // failure has been fixed, and for proving the path end to end without
    // sitting through a six-hour cycle.
    if (req.method === "POST" && url.pathname === "/flush") {
      const entry = await flushToWalrus();
      return send(res, 200, {
        flushed: entry !== null,
        quilt: entry,
        buffered_local: localPending(),
        error: lastFlushError,
      });
    }

    // Rebuild the local cache from the quilts on Walrus. The recovery path for
    // a lost volume; safe to run at any time because it only ever rewrites the
    // cache tier from what Walrus already holds.
    if (req.method === "POST" && url.pathname === "/rehydrate") {
      return send(res, 200, await rehydrateFromWalrus());
    }

    // Remember one item, encrypted, in this user's folder. Replies once the
    // write-ahead append is durable on disk, carrying a local receipt; the
    // flush timer batches it onto Walrus within FLUSH_INTERVAL_MS. A failure is
    // reported as ok:false with the reason, never a silent success.
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
        const receipt = await storeItem(dataNamespaceOf(user, folder, gen), blob);
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
        await storeItem(ctlNamespaceOf(user, folder), `gen:${gen + 1}`);
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

    // Upload a file: encrypt the bytes, store them as a Walrus blob (funded by
    // this service's own wallet), then record an encrypted manifest note in the
    // folder's file index so the file can be listed and fetched later. The blob
    // bytes and the manifest are both sealed under the user's passphrase.
    if (req.method === "POST" && url.pathname === "/file/upload") {
      const body = await readJson(req, FILE_MAX_BODY);
      const user = (body.user || "").toString().trim().toLowerCase();
      const passphrase = (body.passphrase || "").toString();
      const folder = (body.folder || "").toString().trim().toLowerCase();
      const name = (body.name || "").toString().trim();
      const contentType = (body.contentType || "application/octet-stream").toString().slice(0, 200);
      const dataB64 = (body.dataBase64 || "").toString();
      if (!user) return send(res, 400, { error: "user is required" });
      if (!passphrase) return send(res, 400, { error: "passphrase is required" });
      if (!name) return send(res, 400, { error: "name is required" });
      if (!dataB64) return send(res, 400, { error: "dataBase64 is required" });
      if (isRetired(user)) return send(res, 200, { ok: false, error: "This identity is retired on this service." });
      if (!(await anyFileBackendEnabled())) return send(res, 200, { ok: false, files_enabled: false, error: "File storage is not configured on this server." });
      const raw = Buffer.from(dataB64, "base64");
      const key = keyFor(user, passphrase);
      let blobId;
      try {
        // Walrus first, 0G on failure. blobId is a backend-tagged reference.
        blobId = await putEncryptedBlob(encryptBytes(key, raw));
      } catch (e) {
        return send(res, 200, { ok: false, files_enabled: true, error: `blob write failed: ${String(e?.message || e)}` });
      }
      // The manifest note travels through the memory relayer, so a listable
      // index of files rides the same portable, per-user memory as notes.
      if (!client) return send(res, 200, { ok: false, enabled: false, blob_id: blobId, note: "Blob stored, but memory index is disabled." });
      const manifest = FILE_MANIFEST_PREFIX + JSON.stringify({
        v: 1, name, size: raw.length, blobId, contentType, ts: Date.now(),
      });
      try {
        const receipt = await storeItem(fileIndexNamespaceOf(user, folder), encrypt(key, manifest));
        return send(res, 200, { ok: true, enabled: true, files_enabled: true, blob_id: blobId, receipt: receipt.blob_id || "" });
      } catch (e) {
        // The bytes are safely on Walrus; only the index write failed. Return
        // the blobId so the caller can retry the index or download directly.
        return send(res, 200, { ok: false, enabled: true, blob_id: blobId, error: `index not confirmed: ${String(e?.message || e)}` });
      }
    }

    // List the files in a folder: pull the folder's file index, decrypt each
    // manifest, and return the file metadata. Reads only, so this works even
    // when writes are paused.
    if (req.method === "POST" && url.pathname === "/file/list") {
      const body = await readJson(req);
      const user = (body.user || "").toString().trim().toLowerCase();
      const passphrase = (body.passphrase || "").toString();
      const folder = (body.folder || "").toString().trim().toLowerCase();
      if (!user) return send(res, 400, { error: "user is required" });
      if (!passphrase) return send(res, 400, { error: "passphrase is required" });
      if (!client) return send(res, 200, { enabled: false, files: [] });
      if (isRetired(user)) return send(res, 200, { enabled: true, files: [], retired: true });
      const { blobs } = await pullFolder(fileIndexNamespaceOf(user, folder), "file manifest", FETCH_MAX);
      const key = keyFor(user, passphrase);
      const seen = new Set();
      const files = [];
      let scanned = 0;
      for (const b of blobs) {
        const text = decrypt(key, b);
        if (!text || !text.startsWith(FILE_MANIFEST_PREFIX)) continue;
        scanned++;
        try {
          const m = JSON.parse(text.slice(FILE_MANIFEST_PREFIX.length));
          if (m && m.blobId && !seen.has(m.blobId)) {
            seen.add(m.blobId);
            files.push({ name: m.name, size: m.size, blobId: m.blobId, contentType: m.contentType, ts: m.ts });
          }
        } catch {}
      }
      files.sort((a, b) => (b.ts || 0) - (a.ts || 0));

      // Say which of these can actually still be fetched.
      //
      // The index is append-only and encrypted under the user's passphrase, so
      // the server cannot prune it: it cannot read its own contents. What it
      // can do is check each blob and be honest. Without this the index happily
      // lists files that no longer exist anywhere, which is how 36 expired
      // blobs sat there looking retrievable. `available: null` means the
      // question could not be answered, and must not be read as "gone".
      const checked = await Promise.all(
        files.map(async (f) => ({ ...f, available: await refAvailable(f.blobId) }))
      );
      const expired = checked.filter((f) => f.available === false).length;
      return send(res, 200, {
        enabled: true,
        files: checked,
        expired,
        // Surfaced so a caller does not have to count: an expired file is
        // permanently unreadable and can only be restored by re-uploading it.
        note: expired
          ? `${expired} of ${checked.length} file(s) have expired on Walrus and can no longer be downloaded. ` +
            `Re-upload them from a local copy if you still need them.`
          : undefined,
        locked: blobs.length > 0 && scanned === 0,
      });
    }

    // Download a file: fetch its Walrus blob and decrypt it. Needs only the
    // blob id and the passphrase, so it works while writes are paused.
    if (req.method === "POST" && url.pathname === "/file/download") {
      const body = await readJson(req);
      const user = (body.user || "").toString().trim().toLowerCase();
      const passphrase = (body.passphrase || "").toString();
      const blobId = (body.blobId || "").toString().trim();
      if (!user) return send(res, 400, { error: "user is required" });
      if (!passphrase) return send(res, 400, { error: "passphrase is required" });
      if (!blobId) return send(res, 400, { error: "blobId is required" });
      if (isRetired(user)) return send(res, 200, { ok: false, error: "This identity is retired on this service." });
      if (!(await anyFileBackendEnabled())) return send(res, 200, { ok: false, files_enabled: false, error: "File storage is not configured on this server." });
      let enc;
      try {
        // Route to whichever backend the reference names (Walrus or 0G).
        enc = await getBlobByRef(blobId);
      } catch (e) {
        // Distinguish expiry from every other read failure. A lapsed blob is
        // gone for good and no amount of retrying helps, so saying so is worth
        // more than passing back "BlobNotCertifiedError", which reads like a
        // transient fault and invites a pointless retry loop.
        const expired = (await refAvailable(blobId)) === false;
        if (expired) {
          return send(res, 200, {
            ok: false,
            expired: true,
            error:
              "This file's storage term on Walrus has ended, so the bytes are no longer " +
              "retrievable. An expired blob cannot be extended or recovered, only " +
              "re-uploaded from a copy you still hold.",
          });
        }
        return send(res, 200, { ok: false, error: `blob read failed: ${String(e?.message || e)}` });
      }
      try {
        const plain = decryptBytes(keyFor(user, passphrase), enc);
        return send(res, 200, { ok: true, dataBase64: plain.toString("base64") });
      } catch {
        // The passphrase does not open this blob.
        return send(res, 200, { ok: false, locked: true, error: "The passphrase does not open this file." });
      }
    }

    return send(res, 404, { error: "not found" });
  } catch (e) {
    return send(res, 500, { error: String((e && e.message) || e) });
  }
});

server.listen(PORT, HOST, () => {
  const state = client ? "live" : "disabled (set WALRUS_SUI_KEY to enable)";
  console.log(`agent-memory-svc listening on http://${HOST}:${PORT} - memory ${state}, encrypted per user`);
  if (!client) return;

  const pending = localPending();
  const cached = localCachedCount();
  console.log(
    `agent-memory-svc: ${pending} item(s) buffered, ${cached} cached from ` +
      `${readQuiltIndex().length} quilt(s), flushing every ` +
      `${Math.round(FLUSH_INTERVAL_MS / 60000)} min`
  );

  // A cache that is empty while quilts exist means this instance came up
  // against storage it has never read: a replaced volume, or a new deployment
  // pointed at an existing wallet. Recall would answer "nothing remembered",
  // which is the one wrong answer a memory must never give, so rebuild first.
  if (!cached && readQuiltIndex().length) {
    rehydrateFromWalrus().catch((e) =>
      console.warn(`agent-memory-svc: rehydrate on startup failed: ${String(e?.message || e)}`)
    );
  }

  // unref so neither timer holds the process open on shutdown.
  if (FLUSH_INTERVAL_MS > 0) {
    nextFlushAt = Date.now() + FLUSH_INTERVAL_MS;
    setInterval(() => {
      nextFlushAt = Date.now() + FLUSH_INTERVAL_MS;
      flushToWalrus().catch((e) =>
        console.warn(`agent-memory-svc: flush cycle failed: ${String(e?.message || e)}`)
      );
    }, FLUSH_INTERVAL_MS).unref();
  }
  if (RENEW_INTERVAL_MS > 0) {
    setInterval(() => {
      renewExpiring(RENEW_WITHIN_EPOCHS)
        .then((r) => {
          if (r.renewed) {
            console.log(`agent-memory-svc: extended ${r.renewed} quilt(s) nearing expiry`);
          }
        })
        .catch((e) =>
          console.warn(`agent-memory-svc: renewal check failed: ${String(e?.message || e)}`)
        );
    }, RENEW_INTERVAL_MS).unref();
  }
});

// Flush before going down, so a restart or redeploy does not leave freshly
// written notes sitting in a buffer for another six hours. Best-effort and
// time-boxed: the buffer is durable on disk either way, so a slow Walrus write
// must not hold up a shutdown.
for (const signal of ["SIGTERM", "SIGINT"]) {
  process.on(signal, () => {
    const done = () => process.exit(0);
    setTimeout(done, 20000).unref();
    flushToWalrus().then(done, done);
  });
}
