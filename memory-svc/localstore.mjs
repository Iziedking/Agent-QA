// Local encrypted store, in two tiers.
//
// It holds opaque per-namespace items: the ciphertext this sidecar already
// encrypted, plus plaintext gen markers. The sidecar cannot read the encrypted
// items without the passphrase.
//
// PENDING (DATA_DIR/<ns>.jsonl) is the write-ahead buffer. A remember lands
// here first and returns as soon as the append is durable, so a write never
// waits on the network. The flush drains it to Walrus.
//
// CACHE (DATA_DIR/cache/<ns>.jsonl) is what has already reached Walrus. Items
// move here on a successful flush instead of being deleted, and recall reads
// from here rather than from Walrus.
//
// Keeping the cache is what makes recall viable at all. A namespace's items are
// spread across every quilt that ever held it, so reading one folder from
// Walrus means reading hundreds of quilts. Because a Walrus blob is immutable,
// a local copy of it can never go stale, so serving reads from disk is not a
// consistency risk. Walrus stays the durable source of truth: if this volume is
// lost, rehydrate rebuilds the cache from the quilts on chain.
//
// Both tiers live under a directory that should be a mounted volume so they
// survive restarts (AGENT_MEMORY_DATA_DIR).

import {
  mkdirSync, appendFileSync, readFileSync, readdirSync, writeFileSync, renameSync, unlinkSync,
} from "node:fs";
import { join } from "node:path";

const DATA_DIR = process.env.AGENT_MEMORY_DATA_DIR || "./data";
const CACHE_DIR = join(DATA_DIR, "cache");
let _ready = false;

function ensureDir() {
  if (_ready) return;
  mkdirSync(DATA_DIR, { recursive: true });
  mkdirSync(CACHE_DIR, { recursive: true });
  _ready = true;
}

// The cache directory specifically, without the _ready short-circuit.
//
// Memoizing "the directory exists" is only safe while nothing removes it, and
// the case the cache exists for is precisely the one where it has gone: a
// replaced volume, a remount, an operator clearing it. ensureDir would return
// early on its stale flag and every write would then fail ENOENT. mkdir with
// recursive is idempotent and cheap, and this runs a handful of times a day.
function ensureCacheDir() {
  mkdirSync(CACHE_DIR, { recursive: true });
}

// Namespaces are already short safe tokens (avow-<hex>); sanitize hard anyway.
function safeName(namespace) {
  return String(namespace).replace(/[^a-zA-Z0-9_-]/g, "_").slice(0, 80) || "default";
}

function fileFor(namespace) {
  return join(DATA_DIR, safeName(namespace) + ".jsonl");
}

function cacheFileFor(namespace) {
  return join(CACHE_DIR, safeName(namespace) + ".jsonl");
}

// Append one item (a string) to a namespace. JSON-encoded per line so any
// content is stored intact.
export function appendItem(namespace, item) {
  ensureDir();
  appendFileSync(fileFor(namespace), JSON.stringify(item) + "\n", "utf8");
}

// Every namespace holding local items. Namespaces are already safe tokens
// (avow-<hex>), so the sanitize in fileFor is a no-op for real ones and the
// file name maps back to the namespace unchanged.
export function listNamespaces() {
  try {
    return readdirSync(DATA_DIR)
      .filter((f) => f.endsWith(".jsonl"))
      .map((f) => f.slice(0, -".jsonl".length));
  } catch {
    return [];
  }
}

// Drop specific items from a namespace, used once they are confirmed upstream
// so this store stays a buffer rather than a second copy of everything.
//
// Re-reads before rewriting, so items appended while a drain was in flight are
// kept: only what was actually confirmed is removed. The rewrite goes to a temp
// file and is renamed over the original, which is atomic on POSIX, so a crash
// mid-write leaves the previous file intact rather than a truncated one.
export function removeItems(namespace, items) {
  const drop = new Set(items);
  if (!drop.size) return 0;
  const remaining = readItems(namespace).filter((i) => !drop.has(i));
  const path = fileFor(namespace);
  if (!remaining.length) {
    try {
      unlinkSync(path);
    } catch {}
    return drop.size;
  }
  const tmp = path + ".tmp";
  writeFileSync(tmp, remaining.map((i) => JSON.stringify(i) + "\n").join(""), "utf8");
  renameSync(tmp, path);
  return drop.size;
}

// How many items are waiting to go upstream, across every namespace.
export function pendingCount() {
  let n = 0;
  for (const ns of listNamespaces()) n += readItems(ns).length;
  return n;
}

// Read one JSON-lines file into a list of strings. Returns [] if it is missing
// or unreadable, because an absent tier is an empty tier, not an error.
function readLines(path) {
  let raw;
  try {
    raw = readFileSync(path, "utf8");
  } catch {
    return [];
  }
  const out = [];
  for (const line of raw.split("\n")) {
    const t = line.trim();
    if (!t) continue;
    try {
      const v = JSON.parse(t);
      if (typeof v === "string" && v.length) out.push(v);
    } catch {}
  }
  return out;
}

// Read every pending item under a namespace. Returns [] if none.
export function readItems(namespace) {
  return readLines(fileFor(namespace));
}

// --- cache tier: items already durable on Walrus -----------------------------

// Read every cached item under a namespace.
export function readCached(namespace) {
  return readLines(cacheFileFor(namespace));
}

// Every namespace with cached items.
export function listCachedNamespaces() {
  try {
    return readdirSync(CACHE_DIR)
      .filter((f) => f.endsWith(".jsonl"))
      .map((f) => f.slice(0, -".jsonl".length));
  } catch {
    return [];
  }
}

// How many items are held in the cache, across every namespace.
export function cachedCount() {
  let n = 0;
  for (const ns of listCachedNamespaces()) n += readCached(ns).length;
  return n;
}

// Move items from pending to cache, once Walrus has them.
//
// The cache append happens BEFORE the pending removal, deliberately. A crash
// between the two leaves the item in both tiers, and the read path dedupes, so
// the cost is a harmless duplicate. Doing it the other way round would drop the
// item entirely if the process died in the gap.
export function promoteToCache(namespace, items) {
  if (!items.length) return 0;
  ensureCacheDir();
  appendFileSync(
    cacheFileFor(namespace),
    items.map((i) => JSON.stringify(i) + "\n").join(""),
    "utf8"
  );
  return removeItems(namespace, items);
}

// Replace a namespace's cache wholesale, used by the rehydrate path when the
// cache is rebuilt from the quilts on chain. Atomic via temp file and rename.
export function writeCache(namespace, items) {
  ensureCacheDir();
  const path = cacheFileFor(namespace);
  if (!items.length) {
    try {
      unlinkSync(path);
    } catch {}
    return 0;
  }
  const tmp = path + ".tmp";
  writeFileSync(tmp, items.map((i) => JSON.stringify(i) + "\n").join(""), "utf8");
  renameSync(tmp, path);
  return items.length;
}
