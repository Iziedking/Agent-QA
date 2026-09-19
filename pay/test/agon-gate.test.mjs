import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";

import { createAgonPaymentGate } from "../agon-gate.mjs";
import { createAgonManifest } from "../agon-manifest.mjs";

function responseRecorder() {
  return {
    statusCode: 200,
    body: null,
    status(code) { this.statusCode = code; return this; },
    json(body) { this.body = body; return this; },
  };
}

test("unsigned AGON requests reach Circle so they receive a 402 challenge", () => {
  let paymentCalls = 0;
  let validationCalls = 0;
  const gate = createAgonPaymentGate({
    requirePayment: (_req, res) => { paymentCalls += 1; res.status(402).json({ error: "payment required" }); },
    validateBody: () => { validationCalls += 1; return { error: "invalid" }; },
  });
  const res = responseRecorder();
  gate({ headers: {}, body: {} }, res, assert.fail);
  assert.equal(paymentCalls, 1);
  assert.equal(validationCalls, 0);
  assert.equal(res.statusCode, 402);
});

test("signed invalid AGON requests are rejected before Circle settlement", () => {
  let paymentCalls = 0;
  const gate = createAgonPaymentGate({
    requirePayment: () => { paymentCalls += 1; },
    validateBody: () => ({ error: "invalid" }),
  });
  const res = responseRecorder();
  gate({ headers: { "payment-signature": "signed" }, body: {} }, res, assert.fail);
  assert.equal(paymentCalls, 0);
  assert.equal(res.statusCode, 400);
  assert.deepEqual(res.body, { error: "invalid" });
});

test("signed valid AGON requests advance through Circle middleware", () => {
  let advanced = false;
  const gate = createAgonPaymentGate({
    requirePayment: (_req, _res, next) => next(),
    validateBody: () => null,
  });
  gate({ headers: { "payment-signature": "signed" }, body: { operation: "recall" } }, responseRecorder(), () => { advanced = true; });
  assert.equal(advanced, true);
});

test("the served manifest matches the immutable checked-in manifest", async () => {
  const checkedIn = JSON.parse(await readFile(new URL("../../agon/manifest.json", import.meta.url), "utf8"));
  assert.deepEqual(createAgonManifest(), checkedIn);
});
