// One Sui keypair and one Walrus client, shared by the file path and the note
// path. Both used to build their own, which meant two copies of the same wallet
// and two places to get the transport right.
//
// The transport is the part that bites. Sui retired JSON-RPC on public
// fullnodes: `suix_getAllBalances` against https://fullnode.mainnet.sui.io now
// answers -32601 "JSON-RPC on public fullnodes has been deprecated. Please
// migrate to gRPC or GraphQL". So this uses SuiGrpcClient, which speaks
// grpc-web over fetch and needs no extra transport wiring. @mysten/walrus only
// asks for a `ClientWithCoreApi`, which SuiGrpcClient satisfies.
//
// Configure with WALRUS_SUI_KEY (a funded Sui private key). Optional overrides:
// WALRUS_NETWORK (testnet|mainnet), WALRUS_SUI_RPC, WALRUS_UPLOAD_RELAY,
// WALRUS_TIP_MAX_MIST.

const NETWORK = process.env.WALRUS_NETWORK || "testnet";
const RPC =
  process.env.WALRUS_SUI_RPC ||
  (NETWORK === "mainnet"
    ? "https://fullnode.mainnet.sui.io"
    : "https://fullnode.testnet.sui.io");
const RELAY =
  process.env.WALRUS_UPLOAD_RELAY ||
  (NETWORK === "mainnet"
    ? "https://upload-relay.mainnet.walrus.space"
    : "https://upload-relay.testnet.walrus.space");
// The upload relay charges a tip in MIST to publish on our behalf, and refuses
// to run if the quoted tip exceeds this cap. Mainnet quoted 2,579,480 MIST
// (~0.0026 SUI) on 2026-08-04, which blew straight through the old 1,000,000
// default that had only ever been exercised on testnet. This leaves roughly 8x
// headroom for the relay to reprice while still capping a runaway quote.
const TIP_MAX_MIST = Number(process.env.WALRUS_TIP_MAX_MIST || 20_000_000);

let _state = null; // resolved once: { enabled, address?, keypair?, sui?, walrus?, error? }

// Resolve the shared clients, once. Never throws: a missing key or a broken
// dependency must leave the sidecar serving notes from the local store rather
// than failing to start.
async function ensureClients() {
  if (_state) return _state;
  const secret = process.env.WALRUS_SUI_KEY;
  if (!secret) return (_state = { enabled: false });
  try {
    const [{ Ed25519Keypair }, { SuiGrpcClient }, { WalrusClient }] = await Promise.all([
      import("@mysten/sui/keypairs/ed25519"),
      import("@mysten/sui/grpc"),
      import("@mysten/walrus"),
    ]);
    const keypair = Ed25519Keypair.fromSecretKey(secret.trim());
    // baseUrl, not url: SuiGrpcClient takes grpc-web options, and the
    // JSON-RPC client's `url` is silently ignored here.
    const sui = new SuiGrpcClient({ network: NETWORK, baseUrl: RPC });
    const walrus = new WalrusClient({
      network: NETWORK,
      suiClient: sui,
      uploadRelay: { host: RELAY, sendTip: { max: TIP_MAX_MIST } },
    });
    _state = {
      enabled: true,
      network: NETWORK,
      address: keypair.getPublicKey().toSuiAddress(),
      keypair,
      sui,
      walrus,
    };
    console.log(`walrus: enabled on ${NETWORK}, wallet ${_state.address}, rpc ${RPC}`);
  } catch (e) {
    console.error("walrus: init failed:", e?.message || e);
    _state = { enabled: false, error: String(e?.message || e) };
  }
  return _state;
}

// Current SUI and WAL balances, for the health endpoint and the funding check.
// Returns null when Walrus is not configured, so a caller can tell "no wallet"
// from "wallet with nothing in it".
async function balances() {
  const s = await ensureClients();
  if (!s.enabled) return null;
  // `owner`, not `address`: the gRPC client rejects `address` with a bare
  // INVALID_ARGUMENT that says nothing about which field was wrong.
  const { balances: list } = await s.sui.listBalances({ owner: s.address });
  const out = { sui: "0", wal: "0" };
  for (const b of list ?? []) {
    if (b.coinType.endsWith("::sui::SUI")) out.sui = b.balance;
    else if (b.coinType.endsWith("::wal::WAL")) out.wal = b.balance;
  }
  return out;
}

export { ensureClients, balances, NETWORK, RPC, RELAY };
