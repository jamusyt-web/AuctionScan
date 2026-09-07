// Label-anchored extraction.
//
// Deliberately NOT built on CSS selectors. Marketplace markup changes without
// notice and a class name that vanishes takes the whole parser with it. What
// doesn't change is the text: a VIN is 17 characters from a fixed alphabet, a
// price is a dollar amount sitting next to the word "Buy Now", mileage is a
// number followed by "mi". Those survive redesigns.
//
// Everything returns null when it isn't confident. A null costs one wasted
// page open; a wrong number costs money.

const VIN_RE = /\b([A-HJ-NPR-Z0-9]{17})\b/g;
const VIN_TRANSLIT = { A:1,B:2,C:3,D:4,E:5,F:6,G:7,H:8,J:1,K:2,L:3,M:4,N:5,P:7,R:9,
                       S:2,T:3,U:4,V:5,W:6,X:7,Y:8,Z:9 };
const VIN_WEIGHTS = [8,7,6,5,4,3,2,10,0,9,8,7,6,5,4,3,2];

export function validateVin(vin) {
  if (!vin || vin.length !== 17 || /[IOQ]/.test(vin)) return false;
  let total = 0;
  for (let i = 0; i < 17; i++) {
    const c = vin[i];
    const v = /[0-9]/.test(c) ? Number(c) : VIN_TRANSLIT[c];
    if (v === undefined) return false;
    total += v * VIN_WEIGHTS[i];
  }
  const r = total % 11;
  return vin[8] === (r === 10 ? "X" : String(r));
}

/** All checksum-valid VINs on the page, in order. */
export function findVins(text) {
  const seen = new Set();
  const out = [];
  for (const m of text.matchAll(VIN_RE)) {
    const vin = m[1];
    if (!seen.has(vin) && validateVin(vin)) { seen.add(vin); out.push(vin); }
  }
  return out;
}

const money = (s) => {
  const cleaned = String(s).replace(/[^0-9.]/g, "");
  if (!cleaned) return null;
  const n = Number(cleaned);
  return Number.isFinite(n) ? n : null;
};

const MONEY_AFTER = /\$?\s*([0-9][0-9,]{2,})(?:\.\d{2})?/;
// Trailing anchor: the LAST amount before the label, not the first.
const MONEY_BEFORE = /\$\s*([0-9][0-9,]{2,})(?:\.\d{2})?(?![\s\S]*\$\s*[0-9])/;

/**
 * Find a dollar amount that belongs to a label.
 *
 * Scans a window around each label occurrence rather than the whole document,
 * so "Buy Now" doesn't pick up a price from three sections down the page.
 *
 * Looks BOTH ways. Layouts differ on which side the number sits: ACV renders
 * "Buy Now $19,900", ADESA renders the price above its caption, which comes
 * through as "$11,500\nOpening bid". Forward wins when both match, since a
 * label usually introduces its own value.
 */
export function labeledMoney(text, labels, windowChars = 60) {
  const low = text.toLowerCase();
  for (const label of labels) {
    const idx = low.indexOf(label.toLowerCase());
    if (idx === -1) continue;

    const after = text.slice(idx + label.length, idx + label.length + windowChars);
    const fwd = after.match(MONEY_AFTER);
    if (fwd) {
      // Reject a match that sits past the start of ANOTHER field. On ADESA
      // the text after "Opening bid" runs "\n0 Bidders - 0 Bids\nCV
      // Wholesale: $12,275" -- a plain forward scan happily returns 12,275,
      // which is a different number entirely. A newline followed by words
      // means we've left this field behind. A bare newline before the amount
      // is fine: panels often put the value on its own line.
      const gap = after.slice(0, fwd.index);
      if (!/\n\s*[A-Za-z]/.test(gap)) {
        const value = money(fwd[1]);
        // Auction money is never sub-$100; a hit that small matched something
        // else (a lot number, a count of photos).
        if (value != null && value >= 100) return value;
      }
    }

    const before = text.slice(Math.max(0, idx - windowChars), idx);
    const back = before.match(MONEY_BEFORE);
    if (back) {
      const value = money(back[1]);
      if (value != null && value >= 100) return value;
    }
  }
  return null;
}

export function labeledNumber(text, labels, windowChars = 40) {
  for (const label of labels) {
    const idx = text.toLowerCase().indexOf(label.toLowerCase());
    if (idx === -1) continue;
    const window = text.slice(idx + label.length, idx + label.length + windowChars);
    const m = window.match(/([0-9][0-9,]*(?:\.[0-9]+)?)/);
    if (m) return money(m[1]);
  }
  return null;
}

/** Odometer: a number immediately followed by a mileage unit. */
export function findOdometer(text) {
  const candidates = [];
  for (const m of text.matchAll(/([0-9]{1,3}(?:,[0-9]{3})+|[0-9]{4,7})\s*(?:mi\b|miles\b|mi\.|Mi\b)/gi)) {
    const n = money(m[1]);
    if (n != null && n >= 100 && n <= 500000) candidates.push(n);
  }
  if (!candidates.length) return null;
  // Several can appear (listing header, Carfax panel, appraisal panel). They
  // usually agree within a few hundred; take the most common, else the first.
  const counts = new Map();
  for (const c of candidates) counts.set(c, (counts.get(c) || 0) + 1);
  return [...counts.entries()].sort((a, b) => b[1] - a[1])[0][0];
}

/** Model year: a plausible 4-digit year, preferring one before a known make. */
export function findYear(text, currentYear = 2026) {
  const years = [];
  for (const m of text.matchAll(/\b(19[89][0-9]|20[0-4][0-9])\b/g)) {
    const y = Number(m[1]);
    if (y >= 1985 && y <= currentYear + 1) years.push(y);
  }
  return years.length ? years[0] : null;
}

export function findGrade(text) {
  // "Grade 3.1", "G 3.1", or a bare 0-5 next to the word grade.
  const m = text.match(/\b(?:grade|autograde|cr score)\s*:?\s*([0-5](?:\.[0-9])?)\b/i);
  if (m) return Number(m[1]);
  const g = text.match(/\bG\s?([0-5]\.[0-9])\b/);
  return g ? Number(g[1]) : null;
}

const ANNOUNCEMENT_TOKENS = [
  "as-is", "as is", "inop", "not drivable", "frame damage", "structural",
  "salvage", "flood", "true mileage unknown", "tmu", "not actual mileage",
  "airbag", "no arbitration", "absolute", "no reserve", "title absent",
  "emissions", "canadian", "lemon", "buyback", "odometer discrepancy",
];

export function findAnnouncements(text) {
  const low = text.toLowerCase();
  const found = [];
  for (const token of ANNOUNCEMENT_TOKENS) {
    if (low.includes(token) && !found.some((f) => f.toLowerCase() === token)) {
      found.push(token.toUpperCase());
    }
  }
  return found;
}

export function findSaleFormat(text, hints = {}) {
  const low = text.toLowerCase();
  if (low.includes("absolute") || low.includes("no reserve")) return "absolute";
  if (hints.buyNow != null) return "buy_now";
  if (hints.makeOffer) return "make_offer";
  if (low.includes("make offer") || low.includes("make an offer")) return "make_offer";
  if (low.includes("buy now") || low.includes("buy it now")) return "buy_now";
  if (low.includes("simulcast") || low.includes("live lane")) return "live";
  return "timed";
}

/**
 * Build a listing from page text.
 *
 * `site` supplies the label vocabulary for that marketplace; everything else
 * is generic. Pass `listRow: true` for a search-results row, where far less
 * is present and no VIN is expected -- that's the ACV case.
 */
export function extractListing(text, site = {}, opts = {}) {
  const L = site.labels || {};
  const vins = findVins(text);

  const buyNow = labeledMoney(text, L.buyNow || ["Buy Now", "Buy It Now", "BIN"]);
  const reserve = labeledMoney(text, L.reserve || ["Reserve Price", "Seller's Reserve", "Reserve"]);
  const currentBid = labeledMoney(text, L.currentBid || ["Current Bid", "Opening bid", "High Bid", "Current price"]);

  const low = text.toLowerCase();
  const listing = {
    vin: vins[0] || null,
    year: findYear(text),
    make: null, model: null, trim: null,
    odometer: findOdometer(text),
    grade: findGrade(text),
    currentBid, buyNow, reserve,
    reserveMet: low.includes("reserve met") ? true
      : (low.includes("reserve not met") ? false : null),
    makeOffer: low.includes("make offer") || low.includes("make an offer"),
    announcements: findAnnouncements(text),
    drivable: null,
    asIs: /\bas-?is\b/i.test(text) || low.includes("no arbitration"),
    source: site.name || null,
    // Valuation panel (AccuTrade VIN-dow and the marketplace's own numbers).
    mmr: labeledMoney(text, L.mmr || ["Manheim", "MMR"]),
    targetAuction: labeledMoney(text, L.targetAuction || ["Target Auction"]),
    instantOffer: labeledMoney(text, L.instantOffer || ["Instant Offer"]),
    retail: labeledMoney(text, L.retail || ["Retail:", "Clear Retail", "Adjusted Retail"]),
    medianRetail: labeledMoney(text, L.medianRetail || ["Median Retail"]),
  };

  if (/\binop\b/i.test(text) || low.includes("not drivable") || low.includes("non-runner")) {
    listing.drivable = false;
  }
  listing.saleFormat = findSaleFormat(text, listing);

  // Median retail is not odometer-adjusted; never let it stand in for retail.
  if (listing.retail && listing.medianRetail && listing.retail === listing.medianRetail) {
    listing.retail = null;
  }

  if (opts.listRow) listing.fromListRow = true;
  return listing;
}

/** Split a results page into per-row text blocks for tier-1 triage. */
export function splitRows(rootText, rowTexts) {
  return (rowTexts && rowTexts.length ? rowTexts : [rootText]).filter(
    (t) => t && t.trim().length > 20,
  );
}
