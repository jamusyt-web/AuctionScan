import assert from "node:assert/strict";
import { test } from "node:test";

import { pickRowElement, looksRendered, waitFor } from "../src/page.js";

// A fake element chain: link -> row -> list -> body.
function chain(...texts) {
  let parent = null;
  for (const t of texts) parent = { innerText: t, parentElement: parent };
  return parent;   // innermost is returned last... build from outside in
}

test("climbs from a link to the row, not the whole list", () => {
  const body = { innerText: "x".repeat(50000), parentElement: null };
  const list = { innerText: "y".repeat(9000), parentElement: body };
  const row = { innerText: "2021 Ford F-150 XLT 62,400 mi Current Bid $18,500", parentElement: list };
  const link = { innerText: "2021 Ford F-150", parentElement: row };

  assert.equal(pickRowElement(link), row);
});

test("stops before swallowing the entire results list", () => {
  const list = { innerText: "z".repeat(9000), parentElement: null };
  const row = { innerText: "a".repeat(200), parentElement: list };
  const link = { innerText: "short", parentElement: row };
  const picked = pickRowElement(link);
  assert.equal(picked, row);
  assert.notEqual(picked, list);
});

test("returns null when nothing in the chain is row-sized", () => {
  const link = { innerText: "tiny", parentElement: { innerText: "also tiny", parentElement: null } };
  assert.equal(pickRowElement(link), null);
});

test("an empty SPA shell is not treated as rendered", () => {
  assert.equal(looksRendered(""), false);
  assert.equal(looksRendered("Loading..."), false);
  assert.equal(looksRendered("x".repeat(120)), false);   // too little to be a page
  assert.equal(looksRendered("x".repeat(900)), true);
});

test("waitFor resolves once the check passes", async () => {
  let ready = false;
  setTimeout(() => { ready = true; }, 120);
  assert.equal(await waitFor(() => ready, { timeoutMs: 2000, intervalMs: 40 }), true);
});

test("waitFor gives up rather than hanging", async () => {
  assert.equal(await waitFor(() => false, { timeoutMs: 150, intervalMs: 40 }), false);
});

test("waitFor survives a check that throws", async () => {
  let n = 0;
  const check = () => { if (++n < 3) throw new Error("not ready"); return true; };
  assert.equal(await waitFor(check, { timeoutMs: 2000, intervalMs: 20 }), true);
});
