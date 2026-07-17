// The paid lane. Caddy sends /x402/* here; everything else still goes straight
// to the app. This service takes payment for a call and then forwards it to the
// app unchanged, so the free console and the paid marketplace lane share one
// implementation of the memory itself.
//
// Why this exists at all: an agent that finds us on the OKX marketplace gets a
// URL and nothing else. It cannot read our docs and it has no config file to put
// a passphrase in. The one thing it will do is read the 402 we return and ask its
// own user for whatever we say we need. So the route below answers an unpaid
// request with a manifest naming the parameters it requires, and the buying agent
// collects them, pays, and replays the call with them in the body.
//
// One route, not one per operation, because the marketplace sells one service:
// a single listing describing remember, recall and forget together at a single
// fee. The `operation` parameter picks which one. Splitting this into separate
// paid routes would mean separate listings, each with its own description and its
// own review pass.
//
// The passphrase still comes from the buyer and we still never hold it. The
// tradeoff, and it is a real one, is that a marketplace buyer types their
// passphrase to their own agent, so it passes through that agent's context. The
// MCP path, where the secret sits in a config file and never reaches the model,
// stays the stronger option and stays the default we recommend.

import express from "express";
import { OKXFacilitatorClient } from "@okxweb3/x402-core";
import { x402ResourceServer } from "@okxweb3/x402-core/server";
import { registerExactEvmScheme } from "@okxweb3/x402-evm/exact/server";
import { paymentMiddleware } from "@okxweb3/x402-express";

const PORT = Number(process.env.PORT || 9100);

// Where the app lives on the internal Docker network. Same target the console
// calls, just reached from this side of the paywall.
const APP_URL = process.env.APP_URL || "http://app:9090";

// X Layer mainnet, because that is what the marketplace listing is denominated
// in: a buyer who finds us there expects to pay real USDT0 on 196, and a testnet
// challenge would be unpayable for them. Testnet is eip155:1952 if you need it.
// The SDK already knows the USDT0 contract and decimals for both, so a price of
// "$0.01" resolves on its own and we never hardcode a token address.
const NETWORK = process.env.X402_NETWORK || "eip155:196";

// The address buyers pay. There is no sensible default for this one.
const PAY_TO = process.env.X402_PAY_TO || "";

// What the listing charges, for any one operation.
const PRICE = process.env.X402_PRICE || "$0.01";

// Wait for on-chain confirmation before we hand back memory, rather than taking
// the facilitator's "pending" and serving immediately. Costs a little latency
// per call and removes the window where we give away a decrypted note for a
// payment that never lands.
const SYNC_SETTLE = process.env.X402_SYNC_SETTLE !== "false";

const okxConfig = {
  apiKey: process.env.OKX_API_KEY || "",
  secretKey: process.env.OKX_SECRET_KEY || "",
  passphrase: process.env.OKX_PASSPHRASE || "",
  syncSettle: SYNC_SETTLE,
};

const missing = [
  ["OKX_API_KEY", okxConfig.apiKey],
  ["OKX_SECRET_KEY", okxConfig.secretKey],
  ["OKX_PASSPHRASE", okxConfig.passphrase],
  ["X402_PAY_TO", PAY_TO],
].filter(([, value]) => !value).map(([name]) => name);

if (missing.length > 0) {
  // Fail at boot rather than at the first buyer. A half-configured paywall that
  // 500s mid-payment is worse than a service that never claimed to be up.
  console.error(`pay: refusing to start, missing ${missing.join(", ")}`);
  process.exit(1);
}

// The SDK does not check this. A malformed payTo is copied verbatim into the
// payment challenge and served as a valid-looking 402, so the failure surfaces
// at the buyer's signing step, or as payments that quietly never arrive. Check
// it here, at boot, where it is still cheap to notice.
if (!/^0x[0-9a-fA-F]{40}$/.test(PAY_TO)) {
  console.error(`pay: refusing to start, X402_PAY_TO is not an EVM address: ${PAY_TO}`);
  process.exit(1);
}

// What each operation does and what it needs beyond the identity. `extra` is the
// one parameter that operation cannot work without.
//
// forget requires a folder here even though the app defaults it to "", because
// forget is permanent and the caller is an agent that learned this schema from
// the 402 moments ago. Defaulting it would mean a fumbled parameter silently
// retires the buyer's default folder. The free paths keep the app's default;
// only this lane insists.
const OPERATIONS = {
  remember: { target: "/remember", extra: "content" },
  recall: { target: "/recall", extra: "query" },
  forget: { target: "/forget", extra: "folder" },
};

// Required everywhere: which operation, and whose memory. Anything conditional on
// the operation is described rather than marked required, because the buyer's
// schema reader treats `required` as a flat list and cannot express "content is
// required only when operation is remember". The 400 below closes that gap by
// naming whatever is actually missing.
const REQUIRED = ["operation", "user_key", "passphrase"];

const PROPERTIES = {
  operation: {
    type: "string",
    enum: Object.keys(OPERATIONS),
    description:
      "What to do: remember stores a note and needs `content`; recall searches the memory and needs `query`; forget retires a folder and needs `folder`.",
  },
  user_key: {
    type: "string",
    description: "The identity the memory is filed under, usually an email address.",
  },
  passphrase: {
    type: "string",
    description:
      "The passphrase this identity's memory is encrypted under. It is the only key that opens these notes; we cannot derive it, reset it, or recover it.",
  },
  content: { type: "string", description: "The note to store. Required when operation is remember." },
  query: { type: "string", description: "What to search for. Required when operation is recall." },
  folder: {
    type: "string",
    description: "The folder to file under, search within, or retire. Required when operation is forget.",
  },
  limit: { type: "integer", description: "For recall: maximum notes to return, 1 to 50. Defaults to 8." },
};

const INPUT_SCHEMA = { type: "object", properties: PROPERTIES, required: REQUIRED };

const DESCRIPTION =
  "Private, portable memory for an agent: store a note, recall context, or retire a folder, encrypted under a passphrase only the caller holds.";

const facilitator = new OKXFacilitatorClient(okxConfig);
const resourceServer = registerExactEvmScheme(new x402ResourceServer(facilitator), {
  networks: [NETWORK],
});

const app = express();

// Trust Caddy's forwarded headers; it is the only thing in front of us.
app.set("trust proxy", 1);

// The app caps its own bodies. This cap only has to stop us buffering something
// absurd before the paywall has even run.
app.use(express.json({ limit: "1mb" }));

app.use(
  paymentMiddleware(
    {
      "POST /x402/memory": {
        accepts: {
          scheme: "exact",
          price: PRICE,
          network: NETWORK,
          payTo: PAY_TO,
          maxTimeoutSeconds: 120,
        },
        description: DESCRIPTION,
        mimeType: "application/json",
        // In x402 v2 the payment challenge travels in the PAYMENT-REQUIRED
        // header, which leaves the body free for us. A buying agent that finds no
        // machine-readable parameter declaration will replay the call with
        // nothing attached and get a 400. So we spend the body on saying exactly
        // what the paid call needs, in the shape the buyer looks for: a
        // `required` list and an `inputSchema`.
        unpaidResponseBody: () => ({
          contentType: "application/json",
          body: {
            error: `Payment required. This call also needs ${REQUIRED.join(", ")} in the JSON body.`,
            description: DESCRIPTION,
            required: REQUIRED,
            inputSchema: INPUT_SCHEMA,
          },
        }),
      },
    },
    resourceServer,
  ),
);

app.post("/x402/memory", async (req, res) => {
  const body = req.body ?? {};

  // The buyer has already paid by the time we get here, so a missing parameter is
  // our last chance to tell them what was wrong in a way their agent can act on.
  // Naming the parameter is what lets it retry correctly instead of surfacing an
  // opaque failure to its user.
  const absent = REQUIRED.filter((name) => !body[name]);
  if (absent.length > 0) {
    res.status(400).json({
      error: `missing required body param "${absent[0]}"`,
      required: REQUIRED,
      inputSchema: INPUT_SCHEMA,
    });
    return;
  }

  const operation = OPERATIONS[body.operation];
  if (!operation) {
    res.status(400).json({
      error: `unknown operation "${body.operation}"`,
      required: REQUIRED,
      inputSchema: INPUT_SCHEMA,
    });
    return;
  }

  // The parameter this particular operation cannot work without. Same reasoning
  // as above: name it, so the buyer's agent can ask for the one thing it missed.
  if (operation.extra && !body[operation.extra]) {
    res.status(400).json({
      error: `missing required body param "${operation.extra}" for operation "${body.operation}"`,
      required: [...REQUIRED, operation.extra],
      inputSchema: INPUT_SCHEMA,
    });
    return;
  }

  // The app takes the operation as the route, not as a field. Everything else it
  // ignores what it does not know, so forward the body as-is minus our own knob.
  const { operation: _operation, ...forwarded } = body;

  try {
    const upstream = await fetch(`${APP_URL}${operation.target}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(forwarded),
      signal: AbortSignal.timeout(60_000),
    });
    const text = await upstream.text();
    res
      .status(upstream.status)
      .type(upstream.headers.get("content-type") || "application/json")
      .send(text);
  } catch (error) {
    // The buyer paid and we could not deliver. Say so plainly; do not dress a
    // backend outage up as a bad request.
    console.error(`pay: upstream ${operation.target} failed:`, error.message);
    res.status(502).json({ error: "the memory service is unavailable, payment was taken" });
  }
});

app.get("/x402/health", (_req, res) => {
  res.json({ status: "ok", service: "agent-qa-pay", network: NETWORK });
});

app.listen(PORT, () => {
  console.log(`pay: listening on ${PORT}, settling on ${NETWORK}, forwarding to ${APP_URL}`);
});
