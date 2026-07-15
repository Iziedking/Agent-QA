// 0G Storage blob backend, a failover for Walrus. Same shape as walrus-files:
// putBlob(bytes) -> rootHash, getBlob(rootHash) -> bytes. Content-addressed,
// funded by this service's own 0G wallet.
//
// Lazy: nothing loads until the first 0G operation, so a missing key or a
// dependency problem can never stop the sidecar from serving notes or Walrus.
//
// Configure with ZERO_G_PRIVATE_KEY (a funded 0G EVM key). Optional overrides:
// ZERO_G_RPC (chain RPC), ZERO_G_INDEXER (storage indexer).

const RPC = process.env.ZERO_G_RPC || "https://evmrpc-testnet.0g.ai";
const INDEXER = process.env.ZERO_G_INDEXER || "https://indexer-storage-testnet-turbo.0g.ai";

let _state = null; // { enabled, putBlob?, getBlob?, error? }

async function ensure() {
  if (_state) return _state;
  const secret = process.env.ZERO_G_PRIVATE_KEY;
  if (!secret) return (_state = { enabled: false });
  try {
    const [{ ethers }, { createRequire }] = await Promise.all([
      import("ethers"),
      import("node:module"),
    ]);
    // The 0G SDK's ESM build does not resolve cleanly under Node's strict
    // loader, so load it through require (the same workaround the SDK docs use).
    const require = createRequire(import.meta.url);
    const sdk = require("@0gfoundation/0g-storage-ts-sdk");
    const { Indexer, MemData } = sdk;

    const provider = new ethers.JsonRpcProvider(RPC);
    const signer = new ethers.Wallet(secret.trim(), provider);
    const indexer = new Indexer(INDEXER);

    _state = {
      enabled: true,
      address: signer.address,
      async putBlob(bytes) {
        const file = new MemData(bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes));
        const [tree, treeErr] = await file.merkleTree();
        if (treeErr || !tree) throw new Error(`0g merkle failed: ${treeErr}`);
        const rootHash = tree.rootHash();
        const [, upErr] = await indexer.upload(file, RPC, signer);
        if (upErr) throw new Error(`0g upload failed: ${upErr}`);
        return rootHash;
      },
      async getBlob(rootHash) {
        const [blob, dlErr] = await indexer.downloadToBlob(rootHash, { proof: true });
        if (dlErr || !blob) throw new Error(`0g download failed: ${dlErr || "no blob"}`);
        return Buffer.from(await blob.arrayBuffer());
      },
    };
    console.log(`zerog-files: enabled, wallet ${_state.address}`);
  } catch (e) {
    console.error("zerog-files: init failed:", e?.message || e);
    _state = { enabled: false, error: String(e?.message || e) };
  }
  return _state;
}

export { ensure };
