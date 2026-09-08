import assert from "node:assert/strict";
import { test } from "node:test";

import {
  pickRowElement, looksRendered, waitFor, isSafeDismissLabel, looksLikeDetailUrl,
} from "../src/page.js";

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

// --- modal dismissal safety ------------------------------------------------

test("dismisses a plain OK / Accept dialog", () => {
  for (const label of ["OK", "Okay", "Accept", "I Agree", "Continue",
                       "Got it", "Close", "Dismiss", "I understand"]) {
    assert.equal(isSafeDismissLabel(label), true, label);
  }
});

test("NEVER clicks anything that could commit money", () => {
  for (const label of ["Place Bid", "Buy Now", "OK, place my bid", "Confirm",
                       "Submit Offer", "Accept Bid", "Purchase", "Pay Now",
                       "Accept and Buy", "Continue to checkout", "Sell it"]) {
    assert.equal(isSafeDismissLabel(label), false, label);
  }
});

test("does not match a dismiss word buried in a long label", () => {
  assert.equal(isSafeDismissLabel("Click OK to acknowledge the arbitration policy"), false);
  assert.equal(isSafeDismissLabel(""), false);
  assert.equal(isSafeDismissLabel(null), false);
});

// --- link recognition ------------------------------------------------------

test("recognizes a vehicle detail URL by shape", () => {
  assert.equal(
    looksLikeDetailUrl("https://marketplace.adesa.com/details/be5be7b6ed4c4f949b3a155bd58d40590"),
    true);
  assert.equal(looksLikeDetailUrl("https://www.acvauctions.com/vehicle/1234567"), true);
  assert.equal(looksLikeDetailUrl("https://x.com/listings/abc12345-de67-89"), true);
});

test("does not treat navigation links as vehicles", () => {
  for (const href of ["https://marketplace.adesa.com/browse?sale-channel=TIMED",
                      "https://www.acvauctions.com/help",
                      "https://x.com/vehicles/",
                      "https://x.com/about/team"]) {
    assert.equal(looksLikeDetailUrl(href), false, href);
  }
});
