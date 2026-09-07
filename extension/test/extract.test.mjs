// Extraction tested against text taken from a real ADESA vehicle page (the
// 2021 Model Y). Marketplace markup is not something this repo can see, so
// the parser is built on the text itself and tested the same way.

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  extractListing, findVins, validateVin, findOdometer, findGrade,
  labeledMoney, findAnnouncements, findSaleFormat,
} from "../src/extract.js";

// Transcribed from the ADESA listing + the AccuTrade VIN-dow panel.
const ADESA_PAGE = `
Go back to results  Prev 2 of 9239 Next
G 3.1  AS-IS  INOP  Price Drop
2021 Tesla Model Y Performance
AWD Auto - Electric - 143,991 mi
Exterior: Gray - Interior: Black
Seller: CARVANA Wholesale
VIN: 5YJYGDEF2MF252410
ADESA Washington DC  Sale Lane 11 9/7/26 10:41 a.m.
ADESA CLEAR
$11,500
Opening bid
0 Bidders - 0 Bids
CV Wholesale: $12,275  Retail: $21,179
Projected profit: $8,096
PLATINUM AUTO SALES OF CNY INC  Buy Fee
Personalized max bid: $17,546
Time Remaining 23:49:02
Overview  Mechanical 1  Exterior 3  Interior 1  History 1
Standard Inspection w/ Mechanical Check
Announcements: Refer to CR for Disclosures
Mechanical: Not Drivable - Mechanical, Unverified Items
Exterior: Glass Issue, Cosmetic Issue, Wheels & Tires Issue
Interior: Mild Other Odor
All Damages (9)
Bumper Cover - Rear  Mult Scratches/Heavy  2
Wheel - LF  Curb Rash  GR 12"
Wheel - RR  Curb Rash  GR 12"
Qtr Panel - R  Dent/Paint Dmg  1/8" to 1/2"
`;

const ACCUTRADE_PANEL = `
Appraisal  ID: 43970674
2021 TESLA MODEL Y PERFORMANCE 4 DOOR SUV ELECTRIC
5YJYGDEF2MF252410   143,991 Mi
Appraisal Intent: Acquisition
Instant Offer $14,425   Target Auction $15,925
Cars.com Median Retail $28,470   Manheim $15,750
Common Problems:
Infotainment Screen Inop|Damage|Charger Missing|Battery Replacement|Electric Motor(s) Wear/Failure
Odometer -$10,025  Base: 48,000
`;

// --- VIN -------------------------------------------------------------------

test("finds the VIN and rejects a corrupted one", () => {
  assert.deepEqual(findVins(ADESA_PAGE), ["5YJYGDEF2MF252410"]);
  assert.equal(validateVin("5YJYGDEF2MF252410"), true);
  assert.equal(validateVin("5YJYGDEF9MF252410"), false);   // one digit off
});

test("ignores 17-char strings that are not valid VINs", () => {
  const noise = "ORDERNUMBER123456 ABCDEFGH123456789";
  assert.deepEqual(findVins(noise), []);
});

// --- the numbers that cost money ------------------------------------------

test("reads mileage, not the odometer adjustment", () => {
  assert.equal(findOdometer(ADESA_PAGE), 143991);
  assert.equal(findOdometer(ACCUTRADE_PANEL), 143991);
});

test("reads the condition grade", () => {
  assert.equal(findGrade(ADESA_PAGE), 3.1);
});

test("separates the four AccuTrade valuation numbers", () => {
  assert.equal(labeledMoney(ACCUTRADE_PANEL, ["Manheim"]), 15750);
  assert.equal(labeledMoney(ACCUTRADE_PANEL, ["Target Auction"]), 15925);
  assert.equal(labeledMoney(ACCUTRADE_PANEL, ["Instant Offer"]), 14425);
  assert.equal(labeledMoney(ACCUTRADE_PANEL, ["Median Retail"]), 28470);
});

test("a label window does not reach across the page for a price", () => {
  // "Buy Fee" has no amount next to it; the parser must not grab the
  // personalized max bid that follows it.
  assert.equal(labeledMoney(ADESA_PAGE, ["Buy Fee"], 20), null);
});

test("reads a price that sits BEFORE its label", () => {
  // ADESA renders the amount above its caption, so the text reads
  // "$11,500\nOpening bid" -- a forward-only scan misses it entirely.
  assert.equal(labeledMoney(ADESA_PAGE, ["Opening bid"]), 11500);
});

test("forward wins when a label has amounts on both sides", () => {
  const text = "$999 Buy Now $19,900";
  assert.equal(labeledMoney(text, ["Buy Now"]), 19900);
});

test("backward scan takes the nearest amount, not the first on the page", () => {
  const text = "$44,000 earlier unrelated ... $11,500\nOpening bid";
  assert.equal(labeledMoney(text, ["Opening bid"], 40), 11500);
});

// --- disclosures -----------------------------------------------------------

test("catches AS-IS and INOP", () => {
  const found = findAnnouncements(ADESA_PAGE);
  assert.ok(found.includes("AS-IS"));
  assert.ok(found.includes("INOP"));
  assert.ok(found.includes("NOT DRIVABLE"));
});

test("no-reserve wording is read as absolute", () => {
  assert.equal(findSaleFormat("ABSOLUTE - no reserve, sells to high bid"), "absolute");
});

test("make-offer wording is detected", () => {
  assert.equal(findSaleFormat("Make an Offer on this vehicle"), "make_offer");
});

// --- the whole listing -----------------------------------------------------

test("extracts the full listing from page + panel text", () => {
  const l = extractListing(ADESA_PAGE + ACCUTRADE_PANEL, { name: "adesa" });
  assert.equal(l.vin, "5YJYGDEF2MF252410");
  assert.equal(l.year, 2021);
  assert.equal(l.odometer, 143991);
  assert.equal(l.grade, 3.1);
  assert.equal(l.mmr, 15750);
  assert.equal(l.targetAuction, 15925);
  assert.equal(l.instantOffer, 14425);
  assert.equal(l.medianRetail, 28470);
  assert.equal(l.drivable, false);
  assert.equal(l.asIs, true);
  assert.equal(l.source, "adesa");
});

test("mileage-adjusted retail is kept apart from the median", () => {
  const l = extractListing(ADESA_PAGE + ACCUTRADE_PANEL, { name: "adesa" });
  assert.equal(l.retail, 21179);        // the marketplace's adjusted number
  assert.equal(l.medianRetail, 28470);  // the book median
  assert.notEqual(l.retail, l.medianRetail);
});

test("a missing field is null, never zero", () => {
  const l = extractListing("2020 Honda Accord 55,600 mi", { name: "acv" });
  assert.equal(l.buyNow, null);
  assert.equal(l.reserve, null);
  assert.equal(l.mmr, null);
  assert.notEqual(l.buyNow, 0);
});

test("a results row with no VIN still yields a triage-able listing", () => {
  // The ACV case: VIN only populates when the car is opened.
  const row = "2021 Toyota RAV4 XLE AWD  48,200 mi  Current Bid $16,800";
  const l = extractListing(row, { name: "acv" }, { listRow: true });
  assert.equal(l.vin, null);
  assert.equal(l.year, 2021);
  assert.equal(l.odometer, 48200);
  assert.equal(l.currentBid, 16800);
  assert.equal(l.fromListRow, true);
});

test("a forward scan stops at the next field boundary", () => {
  // The exact ADESA shape: the amount after "Opening bid" belongs to a
  // different row. Taking it would report CV Wholesale as the bid.
  const text = "$11,500\nOpening bid\n0 Bidders - 0 Bids\nCV Wholesale: $12,275";
  assert.equal(labeledMoney(text, ["Opening bid"]), 11500);
  assert.notEqual(labeledMoney(text, ["Opening bid"]), 12275);
});

test("a value on its own line still belongs to its label", () => {
  assert.equal(labeledMoney("Instant Offer\n$14,425", ["Instant Offer"]), 14425);
});
