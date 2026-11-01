import assert from "node:assert/strict";
import { test } from "node:test";

import { DEFAULT_CONFIG } from "../src/config.js";
import {
  HEARTBEAT_MS, buildPicksPayload, formatAllowed, isPick, pickRecord, shouldExport,
} from "../src/picks.js";
import { scoreListing } from "../src/scoring.js";

const NOW = Date.parse("2026-11-01T18:00:00Z");
const cfg = structuredClone(DEFAULT_CONFIG);

// A find the way background.js stores it: the stripped deal plus url/seenAt.
function find(listing, { book = true, seenAt = NOW - 60_000 } = {}) {
  const l = {
    vin: "5YJ3E1EB5MF000001", year: 2021, odometer: 52000, grade: 3.6,
    currentBid: null, buyNow: null, reserve: null, reserveMet: null, makeOffer: false,
    announcements: [], drivable: null, asIs: false, source: "acv",
    mmr: book ? 24000 : null, targetAuction: book ? 23500 : null, retail: null,
    ...listing,
  };
  l.saleFormat ??= l.buyNow ? "buy_now" : (l.makeOffer ? "make_offer" : "timed");
  const valuation = book
    ? { source: "accutrade", wholesale: l.mmr, mmr: l.mmr, retail: l.retail,
        adjustedForMileage: true, adjustedForCondition: false }
    : { source: "heuristic", wholesale: 24000, retail: null,
        adjustedForMileage: true, adjustedForCondition: false };
  const deal = scoreListing(l, valuation, cfg);
  return {
    score: deal.score, maxBid: deal.maxBid, headroom: deal.headroom,
    exitChannel: deal.exitChannel, executable: deal.executable,
    riskFlags: deal.riskFlags, acquisition: deal.acquisition, listing: l,
    url: `https://app.acvauctions.com/auction/${l.vin.slice(-6)}`, seenAt, alerted: false,
  };
}

test("defaults hand off Buy Now and Make Offer only", () => {
  assert.deepEqual(DEFAULT_CONFIG.formats, ["buy_now", "make_offer"]);
  assert.equal(DEFAULT_CONFIG.exportPicks, true);
});

test("a Buy Now under the quick max is a pick", () => {
  const f = find({ buyNow: 15000 });
  assert.ok(f.headroom > 0, `headroom ${f.headroom}`);
  assert.equal(isPick(f, cfg, NOW), true);
});

test("a Buy Now just over the quick max is still a pick within the slack", () => {
  const probe = find({ buyNow: 15000 });
  const f = find({ buyNow: Math.round(probe.maxBid + 600) });
  assert.ok(f.headroom < 0 && f.headroom >= -cfg.pickSlack, `headroom ${f.headroom}`);
  assert.equal(isPick(f, cfg, NOW), true);
});

test("a Buy Now well over the quick max is not a pick", () => {
  const probe = find({ buyNow: 15000 });
  const f = find({ buyNow: Math.round(probe.maxBid + 3000) });
  assert.equal(isPick(f, cfg, NOW), false);
});

test("a timed auction with a hidden reserve is never handed off", () => {
  const f = find({ currentBid: 9000 });
  assert.equal(f.acquisition.format, "timed");
  assert.equal(isPick(f, cfg, NOW), false);
});

test("a published make-offer reserve counts as a price", () => {
  const f = find({ makeOffer: true, reserve: 15500 });
  assert.equal(f.acquisition.format, "make_offer");
  assert.equal(f.executable, true);
  assert.equal(isPick(f, cfg, NOW), true);
});

test("a buy-box miss is not a pick even at a good price", () => {
  const f = find({ buyNow: 12000, odometer: 171000 });
  assert.ok(f.riskFlags.some((x) => x.startsWith("Odometer")));
  assert.equal(isPick(f, cfg, NOW), false);
});

test("an unread VIN-dow panel still goes to the agent instead of vanishing", () => {
  const f = find({ buyNow: 15000 }, { book: false });
  assert.equal(isPick(f, cfg, NOW), true);
  assert.equal(pickRecord(f).book, null);
});

test("without book numbers, value flags are ignored but real flags still count", () => {
  const f = find({ buyNow: 15000 }, { book: false });
  f.riskFlags = ["Value $70,000 above ceiling"];
  assert.equal(isPick(f, cfg, NOW), true);
  f.riskFlags = ["Year 2012 below 2015"];
  assert.equal(isPick(f, cfg, NOW), false);
});

test("stale finds age out", () => {
  const f = find({ buyNow: 15000 }, { seenAt: NOW - 49 * 3600_000 });
  assert.equal(isPick(f, cfg, NOW), false);
});

test("an empty format list allows everything", () => {
  const f = find({ currentBid: 9000 });
  assert.equal(formatAllowed(f, { formats: [] }), true);
});

test("the payload lists picks only and its signature ignores timestamps", () => {
  const finds = [find({ buyNow: 15000 }), find({ currentBid: 9000, vin: "5YJ3E1EB5MF000002" })];
  const a = buildPicksPayload(finds, cfg, { now: NOW });
  const b = buildPicksPayload(finds, cfg, { now: NOW + 3600_000 });
  assert.equal(a.payload.count, 1);
  assert.equal(a.payload.picks[0].format, "buy_now");
  assert.equal(a.payload.picks[0].price, 15000);
  assert.equal(a.payload.picks[0].book.mmr, 24000);
  assert.equal(a.signature, b.signature);
  assert.notEqual(a.payload.generatedAt, b.payload.generatedAt);
});

test("export only when picks change or the heartbeat is due", () => {
  assert.equal(shouldExport("x", null, NOW), true);
  assert.equal(shouldExport("x", { signature: "x", at: NOW - 60_000 }, NOW), false);
  assert.equal(shouldExport("y", { signature: "x", at: NOW - 60_000 }, NOW), true);
  assert.equal(shouldExport("x", { signature: "x", at: NOW - HEARTBEAT_MS }, NOW), true);
});
