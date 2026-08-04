// Tests for the local store, focused on the paths that can lose data.
//
// This is the one module that DELETES a user's stored items: removeItems and
// promoteToCache both run after Walrus confirms a buffered write, and a bug
// here loses memory that exists nowhere else until the next flush. Run with
// `npm test` in this directory.
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

const {
  appendItem, readItems, listNamespaces, removeItems, pendingCount,
  readCached, listCachedNamespaces, cachedCount, promoteToCache, writeCache,
} = await import("./localstore.mjs");

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

// --- the cache tier ---------------------------------------------------------
//
// The cache is what a recall actually reads once a flush has happened, so a bug
// here is not a delayed write, it is a memory that answers "nothing remembered"
// about notes Walrus is still holding.

test("promoting to cache moves items across, leaving the buffer empty", () => {
  appendItem("avow-promote", "enc1:p1");
  appendItem("avow-promote", "enc1:p2");
  promoteToCache("avow-promote", ["enc1:p1", "enc1:p2"]);
  assert.deepEqual(readItems("avow-promote"), []);
  assert.deepEqual(readCached("avow-promote"), ["enc1:p1", "enc1:p2"]);
  assert.ok(listCachedNamespaces().includes("avow-promote"));
});

test("a partial promotion leaves the unflushed remainder pending", () => {
  // A flush capped by FLUSH_MAX_ITEMS takes a slice, not the lot. What it did
  // not take has to stay pending or it is silently dropped.
  appendItem("avow-cache-partial", "enc1:q1");
  appendItem("avow-cache-partial", "enc1:q2");
  appendItem("avow-cache-partial", "enc1:q3");
  promoteToCache("avow-cache-partial", ["enc1:q1", "enc1:q2"]);
  assert.deepEqual(readItems("avow-cache-partial"), ["enc1:q3"]);
  assert.deepEqual(readCached("avow-cache-partial"), ["enc1:q1", "enc1:q2"]);
});

test("items written during a flush survive the promotion", () => {
  // The same mid-drain race as removeItems: a remember that lands while the
  // quilt write is in flight must not be swept away by the promotion that
  // follows, because Walrus never saw it.
  appendItem("avow-cache-race", "enc1:before");
  const inFlight = readItems("avow-cache-race");
  appendItem("avow-cache-race", "enc1:during");
  promoteToCache("avow-cache-race", inFlight);
  assert.deepEqual(readItems("avow-cache-race"), ["enc1:during"]);
  assert.deepEqual(readCached("avow-cache-race"), ["enc1:before"]);
});

test("promoting appends rather than replacing, so cache accumulates", () => {
  appendItem("avow-accum", "enc1:first");
  promoteToCache("avow-accum", ["enc1:first"]);
  appendItem("avow-accum", "enc1:second");
  promoteToCache("avow-accum", ["enc1:second"]);
  assert.deepEqual(readCached("avow-accum"), ["enc1:first", "enc1:second"]);
});

test("cachedCount totals the cache without counting the buffer", () => {
  writeCache("avow-tally-a", ["enc1:x", "enc1:y"]);
  writeCache("avow-tally-b", ["enc1:z"]);
  appendItem("avow-tally-a", "enc1:pending-not-counted");
  const cached = cachedCount();
  assert.ok(cached >= 3);
  assert.deepEqual(readCached("avow-tally-a"), ["enc1:x", "enc1:y"]);
});

test("writeCache replaces wholesale, as rehydrate needs", () => {
  writeCache("avow-rehydrate", ["enc1:stale"]);
  writeCache("avow-rehydrate", ["enc1:fresh1", "enc1:fresh2"]);
  assert.deepEqual(readCached("avow-rehydrate"), ["enc1:fresh1", "enc1:fresh2"]);
  assert.equal(existsSync(join(dir, "cache", "avow-rehydrate.jsonl.tmp")), false);
});

test("writeCache with nothing clears the namespace's cache file", () => {
  writeCache("avow-clear", ["enc1:gone"]);
  writeCache("avow-clear", []);
  assert.deepEqual(readCached("avow-clear"), []);
  assert.equal(listCachedNamespaces().includes("avow-clear"), false);
});

test("the cache directory is not mistaken for a namespace", () => {
  // listNamespaces reads the same directory the cache lives under. If it ever
  // returned "cache", a flush would try to write the cache dir as a namespace.
  writeCache("avow-notns", ["enc1:c"]);
  assert.equal(listNamespaces().includes("cache"), false);
});

test("cache writes survive the cache directory being deleted underneath", () => {
  // Regression: ensureDir memoized "the directories exist", so once the cache
  // dir went away, every write failed ENOENT. That is precisely the lost-volume
  // case rehydrate exists to repair, so it failed exactly when it was needed.
  writeCache("avow-vanish", ["enc1:before-loss"]);
  rmSync(join(dir, "cache"), { recursive: true, force: true });
  writeCache("avow-vanish", ["enc1:rebuilt"]);
  assert.deepEqual(readCached("avow-vanish"), ["enc1:rebuilt"]);

  rmSync(join(dir, "cache"), { recursive: true, force: true });
  appendItem("avow-vanish2", "enc1:p");
  promoteToCache("avow-vanish2", ["enc1:p"]);
  assert.deepEqual(readCached("avow-vanish2"), ["enc1:p"]);
});
