import assert from "node:assert/strict";
import { test } from "node:test";

import { readBoundedJson } from "../bounded-json.mjs";

test("reads a bounded JSON provider response", async () => {
  const response = new Response(JSON.stringify({ ok: true }));
  assert.deepEqual(await readBoundedJson(response, 1024), { ok: true });
});

test("rejects provider output larger than the declared limit", async () => {
  const response = new Response(JSON.stringify({ value: "x".repeat(2_000) }));
  await assert.rejects(readBoundedJson(response, 1024), /exceeds the declared limit/);
});

test("rejects non-JSON provider output", async () => {
  await assert.rejects(readBoundedJson(new Response("not json"), 1024), /not valid JSON/);
});
