const ARC_NETWORK = "eip155:5042002";
const ARC_USDC = "0x3600000000000000000000000000000000000000";

export function createAgonManifest(price = "$0.01") {
  return {
    protocol: "agon-service/2",
    identity: {
      chainId: 5042002,
      agentId: "886500",
      serviceKey: "0xca32fe4bfab325462f5aef2583ec379edd47e9c568ab306e7ef4252e335aa446",
    },
    service: {
      name: "Agent QA Runtime Memory",
      description: "Private, portable memory for an agent: store a note, recall context, or retire a folder, encrypted under a passphrase only the caller holds.",
      category: "analysis",
      tags: ["agent-memory", "runtime-memory", "context-memory", "encrypted-memory", "mcp", "walrus", "agent-infrastructure", "recall"],
      logoUrl: "https://agentsqa.xyz/agon/agentqa.png",
      version: "3",
      capabilities: ["agent-memory", "remember", "recall", "forget"],
    },
    invocation: {
      endpoint: "https://agentsqa.xyz/x402/agon-memory",
      method: "POST",
      requestSchema: {
        type: "object",
        properties: {
          operation: { type: "string", enum: ["remember", "recall", "forget"] },
          user_key: { type: "string", minLength: 1 },
          passphrase: { type: "string", minLength: 1 },
          content: { type: "string" },
          query: { type: "string" },
          folder: { type: "string" },
          limit: { type: "integer", minimum: 1, maximum: 50 },
        },
        required: ["operation", "user_key", "passphrase"],
      },
      responseSchema: { type: "object" },
      timeoutMs: 60000,
      maxResponseBytes: 65536,
      idempotency: "none",
      sideEffects: "review_required",
      privacy: {
        retention: "declared",
        sendsToThirdParties: true,
        description: "Encrypted memory records are persisted to external decentralized storage. The service does not retain the caller's passphrase.",
      },
    },
    pricing: {
      rail: "x402",
      amountUSDC: price.replace(/^\$/, ""),
      network: ARC_NETWORK,
      asset: ARC_USDC,
    },
    certification: {
      adapter: "agon-http",
      adapterVersion: "1",
      endpoint: "https://agentsqa.xyz/agon/v1/challenge",
    },
  };
}
