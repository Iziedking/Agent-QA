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

import { mkdirSync, appendFileSync, readFileSync } from "node:fs";
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
