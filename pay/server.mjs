// The paid lane. Caddy sends /x402/* here; everything else still goes straight
// to the app. This service takes payment for a call and then forwards it to the
// app unchanged, so the free console and the paid marketplace lane share one
// implementation of the memory itself.
//
// Why this exists at all: an agent that finds us on the OKX marketplace gets a
// URL and nothing else. It cannot read our docs and it has no config file to put
// a passphrase in. The one thing it will do is read the 402 we return and ask its
// own user for whatever we say we need. So each route below answers an unpaid
// request with a manifest naming the parameters it requires, and the buying agent
// collects them, pays, and replays the call with them in the body.
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

// X Layer testnet by default. Mainnet is eip155:196. The SDK already knows the
// USDT0 contract and decimals for both, so a price of "$0.01" resolves on its
// own and we never hardcode a token address.
const NETWORK = process.env.X402_NETWORK || "eip155:1952";

// The address buyers pay. There is no sensible default for this one.
const PAY_TO = process.env.X402_PAY_TO || "";

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

// One identity pair, described once. Every paid route needs these two and
// nothing works without them, so they are required everywhere.
const IDENTITY = {
  user_key: {
    type: "string",
    description: "The identity the memory is filed under, usually an email address.",
  },
  passphrase: {
    type: "string",
    description:
      "The passphrase this identity's memory is encrypted under. It is the only key that opens these notes; we cannot derive it, reset it, or recover it.",
  },
};

// Each paid service: the route a buyer calls, the app route it forwards to, what
// it costs, and the parameters a buyer has to supply. `required` is what makes
// the buying agent ask its user instead of guessing or giving up.
const SERVICES = [
  {
    route: "POST /x402/recall",
    target: "/recall",
    price: process.env.X402_PRICE_RECALL || "$0.01",
    description: "Recall this identity's stored memory matching a query.",
    required: ["user_key", "passphrase", "query"],
    properties: {
      ...IDENTITY,
      query: { type: "string", description: "What to search the memory for." },
      folder: { type: "string", description: "Optional folder to search within." },
      limit: { type: "integer", description: "Maximum notes to return, 1 to 50. Defaults to 8." },
    },
  },
  {
    route: "POST /x402/remember",
    target: "/remember",
    price: process.env.X402_PRICE_REMEMBER || "$0.005",
    description: "Store a note in this identity's memory, encrypted under their passphrase.",
    required: ["user_key", "passphrase", "content"],
    properties: {
      ...IDENTITY,
      content: { type: "string", description: "The note to store." },
      folder: { type: "string", description: "Optional folder to file the note under." },
    },
  },
  {
    route: "POST /x402/files",
    target: "/file/list",
    price: process.env.X402_PRICE_FILES || "$0.005",
    description: "List the files stored under this identity.",
    required: ["user_key", "passphrase"],
    properties: {
      ...IDENTITY,
      folder: { type: "string", description: "Optional folder to list." },
    },
  },
  {
    route: "POST /x402/file",
    target: "/file/download",
    price: process.env.X402_PRICE_FILE || "$0.02",
    description: "Fetch one stored file by its blob id.",
    required: ["user_key", "passphrase", "blob_id"],
    properties: {
      ...IDENTITY,
      blob_id: { type: "string", description: "The blob id, as returned by the file list." },
    },
  },
];

/**
 * Build the body we return with a 402.
 *
 * In x402 v2 the payment challenge travels in the PAYMENT-REQUIRED header, which
 * leaves the body free for us. A buying agent that finds no machine-readable
 * parameter declaration will replay the call with nothing attached and get a 400.
 * So we spend the body on saying exactly what the paid call needs, in the shape
 * the OKX buyer skill looks for: a `required` list and an `inputSchema`.
 *
 * @param {object} service - One entry from SERVICES.
 * @returns {object} The 402 body.
 */
function manifest(service) {
  return {
    error: `Payment required. This call also needs ${service.required.join(", ")} in the JSON body.`,
    description: service.description,
    required: service.required,
    inputSchema: {
      type: "object",
      properties: service.properties,
      required: service.required,
    },
  };
}

const facilitator = new OKXFacilitatorClient(okxConfig);
const resourceServer = registerExactEvmScheme(new x402ResourceServer(facilitator), {
  networks: [NETWORK],
});

const routes = Object.fromEntries(
  SERVICES.map((service) => [
    service.route,
    {
      accepts: {
        scheme: "exact",
        price: service.price,
        network: NETWORK,
        payTo: PAY_TO,
        maxTimeoutSeconds: 120,
      },
      description: service.description,
      mimeType: "application/json",
      unpaidResponseBody: () => ({
        contentType: "application/json",
        body: manifest(service),
      }),
    },
  ]),
);

const app = express();

// Trust Caddy's forwarded headers; it is the only thing in front of us.
app.set("trust proxy", 1);

// The app caps its own bodies. This cap only has to stop us buffering something
// absurd before the paywall has even run.
app.use(express.json({ limit: "1mb" }));

// Unpaid before this line, paid after it.
app.use(paymentMiddleware(routes, resourceServer));

for (const service of SERVICES) {
  app.post(service.route.split(" ")[1], async (req, res) => {
    const body = req.body ?? {};

    // The buyer has already paid by the time we get here, so a missing parameter
    // is our last chance to tell them what was wrong in a way their agent can act
    // on. Naming the parameter is what lets it retry correctly instead of
    // surfacing an opaque failure to its user.
    const absent = service.required.filter((name) => !body[name]);
    if (absent.length > 0) {
      res.status(400).json({
        error: `missing required body param "${absent[0]}"`,
        required: service.required,
        inputSchema: {
          type: "object",
          properties: service.properties,
          required: service.required,
        },
      });
      return;
    }

    try {
      const upstream = await fetch(`${APP_URL}${service.target}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
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
      console.error(`pay: upstream ${service.target} failed:`, error.message);
      res.status(502).json({ error: "the memory service is unavailable, payment was taken" });
    }
  });
}

app.get("/x402/health", (_req, res) => {
  res.json({ status: "ok", service: "agent-qa-pay", network: NETWORK });
});

app.listen(PORT, () => {
  console.log(`pay: listening on ${PORT}, settling on ${NETWORK}, forwarding to ${APP_URL}`);
});
