import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";

test("the legacy OKX middleware defers facilitator sync so AGON can boot independently", async () => {
  const source = await readFile(new URL("../server.mjs", import.meta.url), "utf8");
  assert.match(
    source,
    /paymentMiddleware\([\s\S]*?resourceServer,\s*undefined,\s*undefined,\s*false,\s*\)/,
  );
});
