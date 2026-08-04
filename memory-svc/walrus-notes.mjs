// Notes stored directly on Walrus, with no relayer in the path.
//
// The unit of storage is a QUILT: one Walrus blob carrying many independently
// addressable patches. Batching is mandatory rather than clever. Walrus charges
// per blob, near enough flat up to 64 KB (1 KB and 64 KB both cost 0.030516 WAL
// at 5 epochs on mainnet), so one blob per note would cost about 900x what one
// quilt of a thousand notes costs. The flush cadence is therefore the only real
// cost dial.
//
// Retention is always the 53-epoch maximum, roughly two years. That is not
// generosity, it is arithmetic: 53 epochs costs 8.04x what 5 epochs costs while
// lasting 10.6x as long, so the longest term is the cheapest per day. It also
// pushes renewal two years out instead of ten weeks, which matters because
// every one of our 36 testnet file blobs expired unnoticed and became
// permanently unreadable.
//
// One quilt is shared by every namespace flushed in that cycle. Each patch is
// separately encrypted under its own user's passphrase before it gets here, so
// sharing a quilt leaks nothing beyond the hashed namespace, and one flush
// covers the whole service rather than costing per user.

import { readFileSync, writeFileSync, renameSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { ensureClients } from "./walrus-client.mjs";

// The maximum Walrus allows. See the note above on why longest is cheapest.
const EPOCHS = Number(process.env.WALRUS_NOTE_EPOCHS || 53);
// Deletable blobs can be removed before expiry, which keeps a real delete on
// the table for forget and lets storage be reclaimed. Permanent ones cannot.
const DELETABLE = process.env.WALRUS_NOTE_DELETABLE !== "false";
// Marks our note quilts on a wallet that also holds file blobs, so a recovery
// scan can tell them apart without reading their contents.
const KIND = "agent-memory-notes";
const SCHEMA = "1";
const DATA_DIR = process.env.AGENT_MEMORY_DATA_DIR || "./data";
const INDEX_PATH = join(DATA_DIR, "quilts.json");

// Quilt identifiers must be unique within a quilt and cannot begin with "_".
const identifierFor = (namespace, i) => `${namespace}-${i}`;

// --- the quilt index --------------------------------------------------------
//
// Every quilt this service has written, so a rebuild knows what to read without
// having to decode Move structs off chain. This sidecar is the only writer, so
// the file is authoritative for anything it wrote; recoverQuilts() is the
// fallback for when the file itself is gone.

function readIndex() {
  try {
    const parsed = JSON.parse(readFileSync(INDEX_PATH, "utf8"));
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

// Atomic via temp file and rename, so a crash mid-write cannot leave the index
// half-written and unparseable, which would look exactly like "no quilts".
function writeIndex(entries) {
  mkdirSync(DATA_DIR, { recursive: true });
  const tmp = INDEX_PATH + ".tmp";
  writeFileSync(tmp, JSON.stringify(entries, null, 2), "utf8");
  renameSync(tmp, INDEX_PATH);
}

function appendIndex(entry) {
  const entries = readIndex();
  entries.push(entry);
  writeIndex(entries);
  return entry;
}

// --- writing ----------------------------------------------------------------

// Write one quilt holding every pending item, across every namespace.
//
// `batches` is [{ namespace, items: [ciphertext, ...] }]. Returns the index
// entry on success. Throws on failure, so the caller keeps the items pending
// and retries next cycle: an item may only leave the buffer once Walrus has
// actually confirmed it.
async function flush(batches) {
  const { enabled, walrus, keypair, error } = await ensureClients();
  if (!enabled) throw new Error(error || "Walrus is not configured (set WALRUS_SUI_KEY)");

  const blobs = [];
  for (const { namespace, items } of batches) {
    items.forEach((contents, i) => {
      blobs.push({
        contents: Buffer.from(contents, "utf8"),
        identifier: identifierFor(namespace, i),
        // The tag is how a patch is found again without reading every patch.
        // Namespaces are already an unkeyed sha256 prefix (avow-<hex>), so
        // putting one in the clear in the quilt index reveals no identity.
        tags: { ns: namespace },
      });
    });
  }
  if (!blobs.length) return null;

  const at = new Date().toISOString();
  const { blobId, blobObject } = await walrus.writeQuilt({
    blobs,
    epochs: EPOCHS,
    deletable: DELETABLE,
    signer: keypair,
    // Attributes ride in the same transaction as the quilt write, so this
    // costs nothing extra. Kept small and fixed-size: the namespace list is
    // deliberately NOT stored, because it grows without bound and a rebuild
    // has to read the quilt anyway.
    attributes: { kind: KIND, v: SCHEMA, at, n: String(blobs.length) },
  });

  const entry = {
    blobId,
    blobObjectId: blobObject.id,
    endEpoch: Number(blobObject.storage.end_epoch),
    at,
    count: blobs.length,
    namespaces: batches.map((b) => b.namespace),
  };
  appendIndex(entry);

  // Stamp the blob id onto the object as a second transaction. It cannot go in
  // the write above, because the id is only known once the quilt is written.
  // This is what makes the wallet self-describing: a recovery scan can list
  // owned objects, read attributes, and learn which blob to fetch without
  // decoding a Move struct. Best-effort on purpose, since the quilt is already
  // durable and the local index already has everything needed for normal use.
  try {
    await walrus.executeWriteBlobAttributesTransaction({
      blobObjectId: blobObject.id,
      attributes: { blob: blobId, end: String(entry.endEpoch) },
      signer: keypair,
    });
  } catch (e) {
    console.warn(
      `walrus-notes: quilt ${blobId} stored, but stamping its recovery ` +
        `attributes failed (${String(e?.message || e)}); the local index still has it`
    );
  }

  return entry;
}

// --- reading ----------------------------------------------------------------

// Read back every note in a quilt, grouped by namespace.
//
// Returns a Map of namespace -> [ciphertext, ...], or null when the blob is
// gone. An expired or deleted blob is not an error worth throwing over: it is
// the expected state for anything past its end epoch, and a rebuild wants to
// skip it and carry on rather than abort over one dead quilt.
async function readQuilt(blobId) {
  const { enabled, walrus, error } = await ensureClients();
  if (!enabled) throw new Error(error || "Walrus is not configured (set WALRUS_SUI_KEY)");

  const blob = await walrus.getBlob({ blobId });
  if (!(await blob.exists())) return null;

  const byNamespace = new Map();
  for (const file of await blob.files()) {
    const tags = await file.getTags();
    const ns = tags?.ns;
    if (!ns) continue;
    const list = byNamespace.get(ns) ?? [];
    list.push(await file.text());
    byNamespace.set(ns, list);
  }
  return byNamespace;
}

// Rebuild the list of our quilts from chain, for when the local index is lost.
//
// Lists the Blob objects this wallet owns and reads each one's attributes,
// which is why flush stamps the blob id there: object ids alone are not enough
// to read a blob, and the alternative is decoding the Move struct, whose BCS
// types this package does not export.
async function recoverQuilts() {
  const { enabled, walrus, sui, address, error } = await ensureClients();
  if (!enabled) throw new Error(error || "Walrus is not configured (set WALRUS_SUI_KEY)");

  const blobType = await walrus.getBlobType();
  const found = [];
  let cursor = null;
  do {
    const page = await sui.listOwnedObjects({ owner: address, type: blobType, cursor });
    for (const obj of page.objects ?? []) {
      let attrs = null;
      try {
        attrs = await walrus.readBlobAttributes({ blobObjectId: obj.objectId });
      } catch {
        continue; // unreadable attributes: not one we can identify, skip it
      }
      // The same wallet also holds file blobs. Only note quilts belong here.
      if (attrs?.kind !== KIND || attrs?.v !== SCHEMA || !attrs?.blob) continue;
      found.push({
        blobId: attrs.blob,
        blobObjectId: obj.objectId,
        endEpoch: Number(attrs.end || 0),
        at: attrs.at || null,
        count: Number(attrs.n || 0),
        namespaces: null, // unknown until the quilt is read
      });
    }
    cursor = page.hasNextPage ? page.cursor : null;
  } while (cursor);
  return found;
}

// --- renewal ----------------------------------------------------------------

// The current Walrus epoch, needed to tell how close a blob is to expiry.
async function currentEpoch() {
  const { enabled, walrus } = await ensureClients();
  if (!enabled) return null;
  const state = await walrus.systemState();
  return Number(state?.committee?.epoch ?? 0);
}

// Extend any quilt whose expiry is closer than `withinEpochs`.
//
// Expiry is the failure mode that has already cost us data: an expired blob
// cannot be recovered or extended, only rewritten from bytes we still hold. At
// 53 epochs this should do nothing for about two years, which is exactly the
// point. It still runs, because a renewal job that only starts mattering later
// has to exist before then, not after.
async function renewExpiring(withinEpochs = 5) {
  const { enabled, walrus, keypair } = await ensureClients();
  if (!enabled) return { checked: 0, renewed: 0, epoch: null };

  const epoch = await currentEpoch();
  if (!epoch) return { checked: 0, renewed: 0, epoch: null };

  const entries = readIndex();
  let renewed = 0;
  let changed = false;
  for (const q of entries) {
    if (!q.endEpoch || q.endEpoch - epoch > withinEpochs) continue;
    try {
      await walrus.executeExtendBlobTransaction({
        blobObjectId: q.blobObjectId,
        epochs: EPOCHS,
        signer: keypair,
      });
      q.endEpoch = q.endEpoch + EPOCHS;
      changed = true;
      renewed++;
      // Keep the on-chain copy in step, so a recovery scan does not think a
      // renewed quilt is still about to expire.
      await walrus
        .executeWriteBlobAttributesTransaction({
          blobObjectId: q.blobObjectId,
          attributes: { end: String(q.endEpoch) },
          signer: keypair,
        })
        .catch(() => {});
    } catch (e) {
      console.warn(
        `walrus-notes: could not extend quilt ${q.blobId}: ${String(e?.message || e)}`
      );
    }
  }
  if (changed) writeIndex(entries);
  return { checked: entries.length, renewed, epoch };
}

export {
  flush,
  readQuilt,
  readIndex,
  writeIndex,
  recoverQuilts,
  renewExpiring,
  currentEpoch,
  EPOCHS,
  DELETABLE,
  KIND,
};
