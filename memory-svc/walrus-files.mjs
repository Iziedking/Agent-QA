// Walrus blob storage for files, kept separate from the memory-item path.
//
// Files are too large to batch into a note quilt, so their encrypted bytes go
// straight to Walrus as their own blob, funded by this service's Sui wallet.
// The module is lazy: nothing loads until the first file operation, so a missing
// key or a Walrus dependency problem can never stop the memory sidecar from
// serving notes.
//
// The keypair and clients come from walrus-client.mjs, shared with the note
// path, so there is one wallet and one place where the transport is configured.
//
// Configure with WALRUS_SUI_KEY (a funded Sui private key). Optional overrides:
// WALRUS_NETWORK (testnet|mainnet), WALRUS_SUI_RPC, WALRUS_UPLOAD_RELAY,
// WALRUS_EPOCHS.

import { ensureClients } from "./walrus-client.mjs";

// Files default to the 53-epoch maximum for the same reason notes do: it is
// cheaper per day than a short term and it pushes renewal two years out. The
// old default of 5 epochs is why all 36 testnet file blobs expired and became
// permanently unreadable, so this is a deliberate correction, not a tweak.
const EPOCHS = Number(process.env.WALRUS_EPOCHS || 53);

let _state = null; // resolved once: { enabled, putBlob?, getBlob?, error? }

async function ensure() {
  if (_state) return _state;
  const shared = await ensureClients();
  if (!shared.enabled) {
    return (_state = { enabled: false, error: shared.error });
  }
  const { walrus, keypair, address, network } = shared;
  _state = {
    enabled: true,
    address,
    async putBlob(bytes) {
      const blob = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
      const { blobId } = await walrus.writeBlob({
        blob,
        deletable: false,
        epochs: EPOCHS,
        signer: keypair,
      });
      return blobId;
    },
    async getBlob(blobId) {
      const out = await walrus.readBlob({ blobId });
      return Buffer.from(out);
    },
  };
  console.log(`walrus-files: enabled on ${network}, ${EPOCHS} epochs, wallet ${address}`);
  return _state;
}

export { ensure };
