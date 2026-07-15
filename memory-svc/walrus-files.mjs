// Walrus blob storage for files, kept separate from the memory-item path.
//
// Files are too large for the memory relayer, so their encrypted bytes go
// straight to Walrus as blobs, funded by this service's own Sui wallet. The
// module is lazy: nothing loads until the first file operation, so a missing
// key or a Walrus dependency problem can never stop the memory sidecar from
// serving notes.
//
// Configure with WALRUS_SUI_KEY (a funded Sui private key). Optional overrides:
// WALRUS_NETWORK (testnet|mainnet), WALRUS_SUI_RPC, WALRUS_UPLOAD_RELAY,
// WALRUS_EPOCHS.

const NETWORK = process.env.WALRUS_NETWORK || "testnet";
const RPC =
  process.env.WALRUS_SUI_RPC ||
  (NETWORK === "mainnet" ? "https://fullnode.mainnet.sui.io" : "https://rpc-testnet.suiscan.xyz");
const RELAY =
  process.env.WALRUS_UPLOAD_RELAY ||
  (NETWORK === "mainnet"
    ? "https://upload-relay.mainnet.walrus.space"
    : "https://upload-relay.testnet.walrus.space");
const EPOCHS = Number(process.env.WALRUS_EPOCHS || 5);
const TIP_MAX_MIST = Number(process.env.WALRUS_TIP_MAX_MIST || 1_000_000);

let _state = null; // resolved once: { enabled, putBlob?, getBlob?, error? }

async function ensure() {
  if (_state) return _state;
  const secret = process.env.WALRUS_SUI_KEY;
  if (!secret) return (_state = { enabled: false });
  try {
    const [{ Ed25519Keypair }, { SuiJsonRpcClient, JsonRpcHTTPTransport }, { WalrusClient }] =
      await Promise.all([
        import("@mysten/sui/keypairs/ed25519"),
        import("@mysten/sui/jsonRpc"),
        import("@mysten/walrus"),
      ]);
    const kp = Ed25519Keypair.fromSecretKey(secret.trim());
    const suiClient = new SuiJsonRpcClient({
      transport: new JsonRpcHTTPTransport({ url: RPC }),
      network: NETWORK,
    });
    const walrus = new WalrusClient({
      network: NETWORK,
      suiClient,
      uploadRelay: { host: RELAY, sendTip: { max: TIP_MAX_MIST } },
    });
    _state = {
      enabled: true,
      address: kp.getPublicKey().toSuiAddress(),
      async putBlob(bytes) {
        const blob = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
        const { blobId } = await walrus.writeBlob({ blob, deletable: false, epochs: EPOCHS, signer: kp });
        return blobId;
      },
      async getBlob(blobId) {
        const out = await walrus.readBlob({ blobId });
        return Buffer.from(out);
      },
    };
    console.log(`walrus-files: enabled on ${NETWORK}, wallet ${_state.address}`);
  } catch (e) {
    console.error("walrus-files: init failed:", e?.message || e);
    _state = { enabled: false, error: String(e?.message || e) };
  }
  return _state;
}

export { ensure };
