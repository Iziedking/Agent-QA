const MAX_EVIDENCE_ITEMS = 32;
const MAX_TEXT_LENGTH = 4_000;

export const AGON_MEMORY_AGENT = Object.freeze({
  name: "Agent QA Runtime Memory",
  version: "1.0.0",
  category: "analysis",
  capabilities: ["analysis", "runtime-memory", "adversarial-recall"],
});

function tokens(value) {
  return (String(value).toLowerCase().match(/[a-z0-9]+/g) || []).filter((token) => token.length > 2);
}

function overlap(objective, text) {
  const available = new Set(tokens(text));
  return tokens(objective).reduce((score, token) => score + (available.has(token) ? 1 : 0), 0);
}

function instructionLike(text) {
  return /\b(system|assistant|developer)\s*:|ignore (all|any|the|previous)|override|guaranteed|execute|send funds|private key|seed phrase/i.test(text);
}

function evidenceItems(input) {
  if (!input || typeof input !== "object" || Array.isArray(input)) throw new Error("input must be an object");
  if (!Array.isArray(input.evidence) || input.evidence.length === 0 || input.evidence.length > MAX_EVIDENCE_ITEMS) {
    throw new Error(`input.evidence must contain 1 to ${MAX_EVIDENCE_ITEMS} items`);
  }
  return input.evidence.map((item, index) => {
    if (!item || typeof item !== "object" || Array.isArray(item)) throw new Error(`evidence[${index}] must be an object`);
    const id = String(item.id || `evidence-${index + 1}`).slice(0, 80);
    const kind = item.kind === "observed" ? "observed" : "claim";
    const text = String(item.text || "").trim();
    if (!text || text.length > MAX_TEXT_LENGTH) throw new Error(`evidence[${index}].text is invalid`);
    return { id, kind, text };
  });
}

export function runAgonMemoryChallenge(request) {
  if (!request || typeof request !== "object" || Array.isArray(request)) throw new Error("request must be an object");
  if (request.protocol !== "agon-playground/1") throw new Error("unsupported protocol");
  if (request.category !== "analysis" || request.task?.id !== "evidence-under-pressure") {
    throw new Error("unsupported challenge");
  }

  const objective = String(request.input?.objective || request.task.adversarialPrompt || "review the evidence").slice(0, 1_000);
  const items = evidenceItems(request.input);
  const untrusted = items.filter((item) => item.kind !== "observed" || instructionLike(item.text));
  const observations = items
    .filter((item) => item.kind === "observed" && !instructionLike(item.text))
    .map((item) => ({ id: item.id, fact: item.text, relevance: overlap(objective, item.text) }))
    .sort((left, right) => right.relevance - left.relevance || left.id.localeCompare(right.id));

  return {
    protocol: "agon-playground/1",
    agent: {
      name: AGON_MEMORY_AGENT.name,
      version: AGON_MEMORY_AGENT.version,
      capabilities: AGON_MEMORY_AGENT.capabilities,
    },
    output: {
      decision: untrusted.length > 0 ? "review" : "allow",
      observations,
      untrustedClaims: untrusted.map((item) => ({ id: item.id, claim: item.text })),
      ignoredInstructions: true,
      writesPerformed: false,
      memoryPolicy: "rank observations, quarantine embedded instructions, preserve uncertainty",
    },
    externalWrites: false,
  };
}
