import assert from "node:assert/strict";
import { test } from "node:test";

import {
  pickRowElement, looksRendered, waitFor, isSafeDismissLabel, looksLikeDetailUrl,
  looksLikeVehicleRow, mainListLinks, listGroupSizes,
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

// --- the ACV failure: climbing past the row into the page header -----------

test("stops at the row, not the list, using link count", () => {
  // The shape that broke: a sparse page where every ancestor is under the
  // character cap, so text size alone never stops the climb.
  const header = { innerText: "NOTIFICATIONS MENU\nJamus Killian\nPLATINUM AUTO SALES", parentElement: null };
  const page = { innerText: "NOTIFICATIONS MENU\nJamus Killian\nPLATINUM AUTO SALES\n" +
    "2021 Tesla Model Y 143,991 mi $11,500", parentElement: null };
  const list = { innerText: "2021 Tesla Model Y 143,991 mi $11,500", parentElement: page };
  const row = { innerText: "2021 Tesla Model Y Performance 143,991 mi $11,500", parentElement: list };
  const link = { innerText: "2021 Tesla Model Y", parentElement: row };

  const links = new Map([[link, 1], [row, 1], [list, 37], [page, 37], [header, 0]]);
  const picked = pickRowElement(link, { linkCount: (el) => links.get(el) ?? 0 });

  assert.equal(picked, row);
  assert.notEqual(picked, list);
  assert.notEqual(picked, page);
});

test("without a link count it still refuses oversized containers", () => {
  const list = { innerText: "z".repeat(9000), parentElement: null };
  const row = { innerText: "2021 Ford F-150 62,400 mi $18,500", parentElement: list };
  assert.equal(pickRowElement({ innerText: "F-150", parentElement: row }), row);
});

// --- rejecting page furniture ---------------------------------------------

test("site navigation is not a vehicle row", () => {
  assert.equal(looksLikeVehicleRow(
    "NOTIFICATIONS MENU\nnotifications_none\nhelp_outline\nJ\nJamus Killian\n" +
    "PLATINUM AUTO SALES OF CNY INC\nmenu"), false);
  assert.equal(looksLikeVehicleRow("emoji_events\n454 pts."), false);
  assert.equal(looksLikeVehicleRow(""), false);
});

test("a real row is accepted on miles or on price", () => {
  assert.equal(looksLikeVehicleRow("2021 Tesla Model Y Performance  143,991 mi"), true);
  assert.equal(looksLikeVehicleRow("2020 Honda Accord Sport  $9,900"), true);
  assert.equal(looksLikeVehicleRow("2021 Toyota RAV4 XLE AWD"), false);  // no miles, no price
});

test("a year alone from unrelated text is not enough", () => {
  assert.equal(looksLikeVehicleRow("Copyright 2021 ACV Auctions"), false);
});

// --- separating the results grid from recommendation rails -----------------

test("keeps the results grid and drops a recommendations carousel", () => {
  const grid = "GRID", rail = "RAIL";
  const links = [
    ...Array.from({ length: 37 }, (_, i) => ({ id: `car${i}`, box: grid })),
    ...Array.from({ length: 6 }, (_, i) => ({ id: `rec${i}`, box: rail })),
  ];
  const kept = mainListLinks(links, (l) => l.box);
  assert.equal(kept.length, 37);
  assert.ok(kept.every((l) => l.box === grid));
});

test("keeps everything when the split is ambiguous", () => {
  // Two similarly sized groups means the boundary wasn't found. Throwing
  // half the inventory away on a guess is worse than scanning both.
  const links = [
    ...Array.from({ length: 10 }, () => ({ box: "A" })),
    ...Array.from({ length: 8 }, () => ({ box: "B" })),
  ];
  assert.equal(mainListLinks(links, (l) => l.box).length, 18);
});

test("a single group is left alone", () => {
  const links = Array.from({ length: 12 }, () => ({ box: "ONLY" }));
  assert.equal(mainListLinks(links, (l) => l.box).length, 12);
});

test("handles zero and one link without grouping", () => {
  assert.deepEqual(mainListLinks([], () => null), []);
  const one = [{ box: "A" }];
  assert.deepEqual(mainListLinks(one, (l) => l.box), one);
});

test("reports group sizes largest first", () => {
  const links = [{ box: "A" }, { box: "A" }, { box: "A" }, { box: "B" }];
  assert.deepEqual(listGroupSizes(links, (l) => l.box), [3, 1]);
});

test("ACV's real vehicle URL is recognized by the site pattern", async () => {
  const { SITES } = await import("../src/sites.js");
  assert.equal(
    SITES.acv.detailUrl.test("https://app.acvauctions.com/auction/16312139?was_recommendation=false"),
    true);
  // The marketplace list itself must not look like a vehicle page.
  assert.equal(
    SITES.acv.detailUrl.test("https://app.acvauctions.com/marketplace?p=make_offer"),
    false);
});
