// Local encrypted fallback store.
//
// When the managed Walrus Memory relayer is unavailable (e.g. its upload pause),
// writes fall here so remember, file indexing, and forget keep working. It holds
// exactly what the relayer would have held: opaque per-namespace items (the
// ciphertext this sidecar already encrypted, plus plaintext gen markers). The
// sidecar cannot read the encrypted items without the passphrase, same as
// before. Recall merges these with the relayer's own results.
//
// Append-only, one JSON-lines file per namespace, under a directory that should
// be a mounted volume so it survives restarts (AGENT_MEMORY_DATA_DIR).

import {
  mkdirSync, appendFileSync, readFileSync, readdirSync, writeFileSync, renameSync, unlinkSync,
} from "node:fs";
import { join } from "node:path";

const DATA_DIR = process.env.AGENT_MEMORY_DATA_DIR || "./data";
let _ready = false;

function ensureDir() {
  if (_ready) return;
  mkdirSync(DATA_DIR, { recursive: true });
  _ready = true;
}

function fileFor(namespace) {
  // Namespaces are already short safe tokens (avow-<hex>); sanitize hard anyway.
  const safe = String(namespace).replace(/[^a-zA-Z0-9_-]/g, "_").slice(0, 80) || "default";
  return join(DATA_DIR, safe + ".jsonl");
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

// Read every item stored under a namespace. Returns [] if none.
export function readItems(namespace) {
  let raw;
  try {
    raw = readFileSync(fileFor(namespace), "utf8");
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
