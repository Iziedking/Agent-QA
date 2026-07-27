// Tests for the local buffer, focused on the drain support.
//
// This is the one module that DELETES a user's stored items: removeItems runs
// after the relayer confirms a buffered write, and a bug here loses memory that
// exists nowhere else during an outage. Run with `npm test` in this directory.
//
// AGENT_MEMORY_DATA_DIR is set before importing, because localstore resolves it
// at module load.
import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const dir = mkdtempSync(join(tmpdir(), "agentqa-localstore-"));
process.env.AGENT_MEMORY_DATA_DIR = dir;

const { appendItem, readItems, listNamespaces, removeItems, pendingCount } = await import(
  "./localstore.mjs"
);

test.after(() => rmSync(dir, { recursive: true, force: true }));

test("reads items back in append order", () => {
  // A drain replays oldest first, so order has to survive the round trip.
  appendItem("avow-order", "enc1:one");
  appendItem("avow-order", "enc1:two");
  appendItem("avow-order", "enc1:three");
  assert.deepEqual(readItems("avow-order"), ["enc1:one", "enc1:two", "enc1:three"]);
});

test("lists namespaces and counts what is buffered across them", () => {
  appendItem("avow-count-a", "enc1:a");
  appendItem("avow-count-b", "gen:1");
  const listed = listNamespaces();
  assert.ok(listed.includes("avow-count-a"));
  assert.ok(listed.includes("avow-count-b"));
  assert.equal(pendingCount() >= 2, true);
});

test("removes only the confirmed items", () => {
  appendItem("avow-partial", "enc1:one");
  appendItem("avow-partial", "enc1:two");
  assert.equal(removeItems("avow-partial", ["enc1:one"]), 1);
  assert.deepEqual(readItems("avow-partial"), ["enc1:two"]);
});

test("keeps an item appended while a drain was in flight", () => {
  // The race that would lose data: the drain read two items, a third arrived
  // mid-flight, and only the confirmed two may be dropped.
  appendItem("avow-race", "enc1:one");
  appendItem("avow-race", "enc1:two");
  const inFlight = readItems("avow-race");
  appendItem("avow-race", "enc1:arrived-mid-drain");
  removeItems("avow-race", inFlight);
  assert.deepEqual(readItems("avow-race"), ["enc1:arrived-mid-drain"]);
});

test("drops the file once a namespace is fully drained", () => {
  appendItem("avow-empty", "enc1:only");
  removeItems("avow-empty", ["enc1:only"]);
  assert.equal(existsSync(join(dir, "avow-empty.jsonl")), false);
  assert.equal(listNamespaces().includes("avow-empty"), false);
  assert.deepEqual(readItems("avow-empty"), []);
});

test("an empty removal touches nothing", () => {
  appendItem("avow-noop", "gen:1");
  assert.equal(removeItems("avow-noop", []), 0);
  assert.deepEqual(readItems("avow-noop"), ["gen:1"]);
});

test("survives ciphertext containing quotes and newlines", () => {
  const nasty = 'enc1:has"quotes"\nand\nnewlines';
  appendItem("avow-nasty", nasty);
  appendItem("avow-nasty", "enc1:plain");
  assert.deepEqual(readItems("avow-nasty"), [nasty, "enc1:plain"]);
  removeItems("avow-nasty", [nasty]);
  assert.deepEqual(readItems("avow-nasty"), ["enc1:plain"]);
  assert.equal(existsSync(join(dir, "avow-nasty.jsonl.tmp")), false);
});
