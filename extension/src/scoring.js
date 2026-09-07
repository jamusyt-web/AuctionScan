// Port of the Python scoring engine (auctionscan/{scoring,risk,acquisition}.py).
// Kept deliberately literal so the two stay comparable -- extension/test
// asserts parity against numbers produced by the Python side.

export const ACCIDENT_HAIRCUT = { "": 0, minor: 0.03, moderate: 0.08, severe: 0.15 };
export const STRUCTURAL_HAIRCUT = 0.18;
export const AIRBAG_HAIRCUT = 0.10;
export const BRAND_HAIRCUT = {
  clean: 0, unknown: 0, hail: 0.12, lemon: 0.20,
  true_mileage_unknown: 0.25, rebuilt: 0.35, flood: 0.45, salvage: 0.50,
};
export const GRADE_ADJUSTMENT = { 5: 0.04, 4: 0.01, 3: -0.04, 2: -0.12, 1: -0.22, 0: -0.30 };

const MILEAGE_BASELINE_PER_YEAR = 12000;
const MILEAGE_DOLLARS_PER_MILE = 0.06;
const MILEAGE_ADJUSTMENT_CAP_PCT = 0.20;

// --- drivability / powertrain risk ----------------------------------------

const INOP_PATTERNS = [
  "not drivable", "non-drivable", "nondrivable", "won't start", "wont start",
  "no start", "does not run", "doesn't run", "non-runner", "no crank",
  "will not start", "not running", "dead battery", "no power",
];
const AS_IS_PATTERNS = ["as-is", "as is", "no arbitration", "no arb", "red light"];

const EV_WARRANTY_MILES = {
  "tesla|model s": 150000, "tesla|model x": 150000,
  "tesla|model 3": 120000, "tesla|model y": 120000,
};
const EV_WARRANTY_MILES_DEFAULT = 100000;
const EV_WARRANTY_YEARS = 8;
const EV_PACK_COST = {
  "tesla|model y": [10000, 17000], "tesla|model 3": [9000, 16000],
  "tesla|model s": [13000, 22000], "tesla|model x": [13000, 22000],
};
const EV_PACK_COST_DEFAULT = [8000, 20000];
const EV_DRIVE_UNIT_COST = [2500, 7000];
const ICE_POWERTRAIN_COST = [3500, 9000];
const ICE_POWERTRAIN_WARRANTY_MILES = 60000;
const TRIVIAL_FIX = 400;

const EV_MAKE_HINTS = ["tesla", "rivian", "lucid", "polestar"];

function haystack(listing) {
  return [
    (listing.announcements || []).join(" "),
    listing.conditionReport || "",
    listing.mechanical || "",
  ].join(" ").toLowerCase();
}

export function isInoperable(listing) {
  if (listing.drivable === false) return true;
  const text = haystack(listing);
  if (/\binop\b/.test(text)) return true;
  return INOP_PATTERNS.some((p) => text.includes(p));
}

export function isAsIs(listing) {
  if (listing.asIs) return true;
  const text = haystack(listing);
  return AS_IS_PATTERNS.some((p) => text.includes(p));
}

export function isEv(listing) {
  const make = (listing.make || "").toLowerCase();
  if (EV_MAKE_HINTS.includes(make)) return true;
  const blob = `${listing.trim || ""} ${listing.fuelType || ""}`.toLowerCase();
  return blob.includes("electric");
}

function modelKey(listing) {
  const make = (listing.make || "").toLowerCase().trim();
  const model = (listing.model || "").toLowerCase().trim();
  for (const key of Object.keys(EV_WARRANTY_MILES)) {
    const [m, mo] = key.split("|");
    if (make === m && model.startsWith(mo)) return key;
  }
  return `${make}|${model}`;
}

export function powertrainWarranty(listing, currentYear = 2026) {
  if (listing.odometer == null) return [false, "Odometer unknown; assume out of warranty"];
  const electric = isEv(listing);
  const limit = electric
    ? (EV_WARRANTY_MILES[modelKey(listing)] ?? EV_WARRANTY_MILES_DEFAULT)
    : ICE_POWERTRAIN_WARRANTY_MILES;
  const term = electric ? EV_WARRANTY_YEARS : 5;
  const label = electric ? "Battery/Drive Unit" : "Powertrain";
  const age = listing.year ? currentYear - listing.year : 99;

  if (listing.odometer >= limit) {
    return [false, `${label} warranty expired: ${fmt(listing.odometer)} mi vs ` +
      `${fmt(limit)} mi limit (past by ${fmt(listing.odometer - limit)})`];
  }
  if (age >= term) return [false, `${label} warranty expired on age (${age} yrs vs ${term})`];
  return [true, `${label} likely still covered`];
}

export function estimateReserve(listing, capabilities = {}, currentYear = 2026) {
  const reserve = {
    low: 0, high: 0, unverified: false, inWarranty: false,
    isEv: false, inHouse: false, unpriced: [], basis: [],
  };
  if (!isInoperable(listing)) return reserve;

  reserve.unverified = true;
  reserve.isEv = isEv(listing);
  const [covered, why] = powertrainWarranty(listing, currentYear);
  reserve.inWarranty = covered;
  reserve.basis.push(why, "Listed INOP / not drivable with no diagnosis");

  if (covered) {
    reserve.low = TRIVIAL_FIX;
    reserve.high = 1500;
    reserve.basis.push("Failure may be warranty-covered; exposure limited");
    return reserve;
  }

  reserve.low = TRIVIAL_FIX;
  const candidates = reserve.isEv
    ? [["evBatteryPack", (EV_PACK_COST[modelKey(listing)] ?? EV_PACK_COST_DEFAULT)[1], "traction pack"],
       ["evDriveUnit", EV_DRIVE_UNIT_COST[1], "drive unit"]]
    : [["engine", ICE_POWERTRAIN_COST[1], "engine"],
       ["transmission", ICE_POWERTRAIN_COST[1], "transmission"]];

  // The binding number is the worst SINGLE job, not the sum -- these are
  // alternative explanations for one no-start.
  let worst = 0;
  for (const [name, market, label] of candidates) {
    const own = capabilities[name];
    if (own != null) {
      reserve.inHouse = true;
      reserve.basis.push(`${title(label)} in-house at $${fmt(own)}`);
      worst = Math.max(worst, own);
    } else {
      reserve.unpriced.push(label);
      reserve.basis.push(`${title(label)} NOT priced -- using market $${fmt(market)}`);
      worst = Math.max(worst, market);
    }
  }
  reserve.high = worst;
  if (isAsIs(listing)) reserve.basis.push("Sold AS-IS: no arbitration if it's the expensive one");
  return reserve;
}

// --- valuation adjustment --------------------------------------------------

export function adjustedWholesale(listing, valuation, history = null, currentYear = 2026) {
  const base = valuation.wholesale || 0;
  let value = base;
  const notes = [];

  if (!valuation.adjustedForMileage && listing.odometer && listing.year) {
    const age = Math.max(1, currentYear - listing.year);
    const expected = age * MILEAGE_BASELINE_PER_YEAR;
    let adj = (expected - listing.odometer) * MILEAGE_DOLLARS_PER_MILE;
    const cap = base * MILEAGE_ADJUSTMENT_CAP_PCT;
    adj = Math.max(-cap, Math.min(cap, adj));
    if (Math.abs(adj) > 50) {
      value += adj;
      notes.push(`Mileage: ${fmt(listing.odometer)} vs ${fmt(expected)} expected (${signed(adj)})`);
    }
  }

  if (!valuation.adjustedForCondition && listing.grade != null) {
    const pct = GRADE_ADJUSTMENT[Math.round(listing.grade)] ?? -0.30;
    if (pct) {
      value += base * pct;
      notes.push(`Grade ${listing.grade.toFixed(1)}: ${signed(base * pct)}`);
    }
  }

  if (history) {
    if (history.accidentCount) {
      let pct = ACCIDENT_HAIRCUT[history.worstAccidentSeverity || ""] ?? 0.08;
      pct *= 1 + 0.4 * (history.accidentCount - 1);
      value -= base * pct;
      notes.push(`${history.accidentCount} accident(s): -$${fmt(base * pct)}`);
    }
    if (history.structuralDamage) { value -= base * STRUCTURAL_HAIRCUT; notes.push("Structural damage"); }
    if (history.airbagDeployed) { value -= base * AIRBAG_HAIRCUT; notes.push("Airbag deployed"); }
    for (const brand of history.titleBrands || []) {
      const pct = BRAND_HAIRCUT[brand] ?? 0;
      if (pct) { value -= base * pct; notes.push(`Title brand ${brand}`); }
    }
  }
  return [Math.max(0, value), notes];
}

// --- acquisition -----------------------------------------------------------

export const CERTAINTY = {
  buy_now: 1.0, make_offer: 0.80, absolute: 0.75,
  live: 0.55, timed: 0.50, unknown: 0.40,
};
const RESERVE_UNMET_CERTAINTY = 0.20;
const DISCLOSED_RESERVE_CERTAINTY = 1.0;

export function detectFormat(listing) {
  const blob = (listing.announcements || []).join(" ").toLowerCase();
  if (blob.includes("absolute") || blob.includes("no reserve")) return "absolute";
  if (listing.saleFormat) {
    const f = listing.saleFormat.trim().toLowerCase().replace(/ /g, "_");
    if (CERTAINTY[f] != null) return f;
  }
  if (listing.buyNow) return "buy_now";
  if (listing.makeOffer) return "make_offer";
  return "timed";
}

export function planAcquisition(listing, maxBid, wholesaleValue = null) {
  const format = detectFormat(listing);
  const acq = {
    format, price: null, certainty: CERTAINTY[format] ?? 0.4,
    executable: false, recommendedOffer: null, action: "", notes: [],
  };

  const options = [];
  if (listing.buyNow) options.push([listing.buyNow, "buy_now"]);
  if (listing.reserve != null) options.push([listing.reserve, "reserve"]);

  if (options.length) {
    options.sort((a, b) => a[0] - b[0]);
    const [price, kind] = options[0];
    acq.price = price;
    acq.certainty = DISCLOSED_RESERVE_CERTAINTY;
    acq.executable = true;   // transactable; affordability is a scoring question
    const offering = format === "make_offer" || kind === "reserve";

    if (price > maxBid) {
      acq.action = `Pass -- ${kind === "buy_now" ? "buy now" : "reserve"} ` +
        `$${fmt(price)} is $${fmt(price - maxBid)} over your max`;
      return acq;
    }
    const under = maxBid - price;
    if (offering) {
      acq.recommendedOffer = price;
      acq.action = `OFFER $${fmt(price)} -- their published reserve, takes it now ` +
        `($${fmt(under)} under your max). Try lower first.`;
      acq.notes.push("Reserve is disclosed; anything they accept below it is upside.");
    } else {
      acq.action = `BUY NOW at $${fmt(price)} ($${fmt(under)} under your max)`;
    }
    return acq;
  }

  const bid = listing.currentBid || 0;

  if (format === "make_offer") {
    acq.recommendedOffer = maxBid;
    acq.price = maxBid;
    acq.executable = true;
    acq.action = `OFFER $${fmt(maxBid)} (no reserve shown)`;
    if (wholesaleValue > 0) {
      const ratio = maxBid / wholesaleValue;
      acq.notes.push(`Offer is ${(ratio * 100).toFixed(0)}% of wholesale value`);
      if (ratio < 0.80) { acq.action += " (likely rejected -- well under book)"; acq.certainty *= 0.6; }
      else if (ratio < 0.90) { acq.action += " (may be rejected)"; acq.certainty *= 0.85; }
    }
    return acq;
  }

  if (format === "absolute") {
    acq.price = bid; acq.executable = true;
    acq.action = `Bid up to $${fmt(maxBid)} (no reserve)`;
    return acq;
  }

  if (listing.reserveMet === true) {
    acq.price = bid; acq.executable = true;
    acq.action = `Bid up to $${fmt(maxBid)} (reserve met at $${fmt(bid)})`;
    return acq;
  }

  // Undisclosed, unmet reserve: the listed bid is not a price.
  acq.price = null;
  acq.executable = false;
  acq.certainty = RESERVE_UNMET_CERTAINTY;
  acq.notes.push(`Listed bid $${fmt(bid)} is NOT an acquisition price -- reserve undisclosed and unmet.`);
  acq.action = `Bid up to $${fmt(maxBid)}, but expect a reserve above the bid`;
  return acq;
}

// --- exits and the headline score -----------------------------------------

export function evaluateExit(channel, exitValue, reconBase, transport,
                             reserveLow, reserveHigh, cfg, unpricedPowertrain) {
  const recon = reconBase * (channel.reconMultiplier ?? 1);
  const holding = (cfg.costs.floorplanDaily ?? 0) * (channel.daysHeld ?? 21);
  const fees = (cfg.costs.buyFee ?? 0) + (channel.sellFee ?? 0);
  const fixed = recon + transport + fees + holding;

  let required = Math.max(channel.targetGross ?? 0, exitValue * (channel.targetGrossPct ?? 0));
  if (unpricedPowertrain) {
    required = Math.max(required, cfg.capabilities?.unpricedPowertrainMinGross ?? 4000);
  }
  return {
    exit: channel, exitValue, requiredGross: required,
    maxBid: exitValue - fixed - reserveHigh - required,
    maxBidOptimistic: exitValue - fixed - reserveLow - required,
    totalCosts: fixed + reserveHigh,
  };
}

export function checkBuyBox(listing, value, reserve, recon, cfg) {
  const bb = cfg.buyBox, flags = [];
  if (bb.minYear && listing.year && listing.year < bb.minYear) flags.push(`Year ${listing.year} below ${bb.minYear}`);
  if (bb.maxYear && listing.year && listing.year > bb.maxYear) flags.push(`Year ${listing.year} above ${bb.maxYear}`);
  if (listing.odometer && listing.odometer > bb.maxOdometer) flags.push(`Odometer ${fmt(listing.odometer)} over ${fmt(bb.maxOdometer)}`);
  if (value && value < bb.minWholesaleValue) flags.push(`Value $${fmt(value)} below floor`);
  if (value && value > bb.maxWholesaleValue) flags.push(`Value $${fmt(value)} above ceiling`);
  if (listing.grade != null && listing.grade < bb.minGrade) flags.push(`Grade ${listing.grade} below ${bb.minGrade}`);
  if (recon > bb.maxRecon) flags.push(`Recon $${fmt(recon)} over cap`);
  const make = (listing.make || "").toLowerCase();
  if ((bb.excludedMakes || []).map((m) => m.toLowerCase()).includes(make)) flags.push(`${listing.make} excluded`);
  if ((bb.allowedMakes || []).length && !bb.allowedMakes.map((m) => m.toLowerCase()).includes(make)) {
    flags.push(`${listing.make} not in buy box`);
  }
  if (reserve.unverified && !bb.allowInoperable) flags.push("Listed INOP / not drivable");
  if (reserve.high > bb.maxPowertrainReserve) flags.push(`Powertrain exposure $${fmt(reserve.high)} over cap`);
  if (isAsIs(listing) && reserve.unverified && !bb.allowAsIsInop) {
    flags.push("AS-IS with an undiagnosed no-start: no arbitration");
  }
  return flags;
}

export function scoreListing(listing, valuation, cfg, history = null, condition = null) {
  const deal = { listing, valuation, riskFlags: [], reasoning: [], score: 0, speculative: false };

  if (!valuation || !valuation.wholesale) {
    deal.riskFlags.push("No valuation available");
    return deal;
  }

  const [value, notes] = adjustedWholesale(listing, valuation, history);
  deal.reasoning.push(...notes);

  const recon = Math.max(
    cfg.costs.baselineRecon ?? 450,
    (condition?.estimatedReconCost ?? 0) * (cfg.costs.reconSafetyMultiplier ?? 1.25),
  );
  const transport = Math.max(
    cfg.costs.transportMinimum ?? 150,
    (cfg.costs.defaultTransportMiles ?? 250) * (cfg.costs.transportPerMile ?? 1.15),
  );

  const reserve = estimateReserve(listing, cfg.capabilities || {});
  deal.powertrainReserve = reserve;
  deal.reasoning.push(...reserve.basis);
  const unpriced = reserve.unverified && reserve.unpriced.length > 0;

  const base = valuation.wholesale || 0;
  const haircut = base ? value / base : 1;
  const results = [];
  for (const channel of cfg.exits || []) {
    if (channel.enabled === false) continue;
    let exitValue;
    if (channel.name === "retail") {
      if (!valuation.retail) continue;
      exitValue = valuation.retail * haircut;
    } else {
      exitValue = value;
    }
    results.push(evaluateExit(channel, exitValue, recon, transport,
      reserve.low, reserve.high, cfg, unpriced));
  }
  if (!results.length) {
    deal.riskFlags.push("No usable exit channel");
    return deal;
  }

  const best = results.reduce((a, b) => (b.maxBid > a.maxBid ? b : a));
  deal.exitChannel = best.exit.name;
  deal.maxBid = best.maxBid;
  deal.maxBidOptimistic = best.maxBidOptimistic;
  deal.totalCosts = best.totalCosts;
  for (const r of results) {
    deal.reasoning.push(`[${r.exit.name}] exit $${fmt(r.exitValue)} - costs ` +
      `$${fmt(r.totalCosts)} - gross $${fmt(r.requiredGross)} = max bid $${fmt(r.maxBid)}`);
  }

  deal.riskFlags = checkBuyBox(listing, value, reserve, recon, cfg);
  const disqualifying = [...deal.riskFlags];

  deal.acquisition = planAcquisition(listing, deal.maxBid, value);
  deal.reasoning.push(...deal.acquisition.notes, deal.acquisition.action);

  deal.headroom = deal.acquisition.price != null
    ? deal.maxBid - deal.acquisition.price
    : deal.maxBid - (listing.currentBid || 0);
  deal.executable = deal.acquisition.executable;
  deal.projectedGross = best.exitValue - (listing.currentBid || 0) - best.totalCosts;

  if (reserve.unverified && reserve.unpriced.length &&
      reserve.high - reserve.low > (cfg.buyBox.maxRiskSpread ?? 3000)) {
    deal.speculative = true;
    deal.reasoning.push(`UNVERIFIED FAILURE: $${fmt(reserve.low)} to $${fmt(reserve.high)} swing.`);
  }

  deal.confidence = confidence(deal, valuation, history, condition);

  if (disqualifying.length) deal.score = 0;
  else if (deal.executable && deal.headroom < 0) deal.score = 0;
  else {
    const ratio = best.requiredGross ? deal.headroom / best.requiredGross : 0;
    const raw = Math.max(0, Math.min(100, 50 + 25 * ratio));
    deal.score = raw * deal.confidence * deal.acquisition.certainty;
  }
  return deal;
}

function confidence(deal, valuation, history, condition) {
  let s = 1.0;
  if (valuation.source === "heuristic") s -= 0.45;
  if (!history) s -= 0.25;
  if (!condition) s -= 0.15;
  else if (condition.confidence < 0.6) s -= 0.10;
  if (deal.listing.grade == null) s -= 0.10;
  if (!deal.listing.conditionReport && !(deal.listing.photoUrls || []).length) s -= 0.10;
  return Math.max(0, Math.min(1, s));
}

// --- tier-1 triage (no VIN needed) -----------------------------------------

const SEGMENT_BASE = { luxury: 64000, truck: 58000, suv: 44000, midsize: 33000, compact: 27000 };
const LUXURY_MAKES = ["bmw", "mercedes-benz", "mercedes", "audi", "lexus", "porsche",
  "jaguar", "land rover", "cadillac", "genesis", "infiniti", "acura", "volvo", "tesla"];
const TRUCK_HINTS = ["f-150", "f150", "silverado", "sierra", "ram", "tundra", "tacoma",
  "colorado", "ranger", "frontier", "titan", "canyon", "ridgeline"];
const SUV_HINTS = ["explorer", "tahoe", "suburban", "pilot", "highlander", "rav4",
  "cr-v", "crv", "equinox", "escape", "rogue", "grand cherokee", "4runner",
  "telluride", "palisade", "atlas", "traverse"];

/** Rough value from year/make/model/miles alone. Too crude to bid off; good
 *  enough to decide whether a car is worth opening -- which is all tier 1 is
 *  for, and why ACV hiding the VIN on the results list doesn't matter. */
export function roughValue(listing, currentYear = 2026) {
  if (!listing.year) return null;
  const make = (listing.make || "").toLowerCase();
  const model = (listing.model || "").toLowerCase();
  let base = SEGMENT_BASE.compact;
  if (LUXURY_MAKES.includes(make)) base = SEGMENT_BASE.luxury;
  else if (TRUCK_HINTS.some((h) => model.includes(h))) base = SEGMENT_BASE.truck;
  else if (SUV_HINTS.some((h) => model.includes(h))) base = SEGMENT_BASE.suv;
  else if (listing.year >= 2018) base = SEGMENT_BASE.midsize;

  const age = Math.max(0, currentYear - listing.year);
  const residual = age === 0 ? 1 : Math.max(0.12, 0.80 * Math.pow(0.89, age - 1));
  let retail = base * residual;
  if (listing.odometer) {
    const expected = Math.max(1, age) * 12000;
    const delta = (expected - listing.odometer) * 0.045;
    retail += Math.max(-retail * 0.25, Math.min(retail * 0.25, delta));
  }
  return Math.max(500, retail * 0.85);
}

/** Should the sweep bother opening this one? */
export function worthOpening(listing, cfg) {
  const bb = cfg.buyBox;
  if (bb.minYear && listing.year && listing.year < bb.minYear) return false;
  if (bb.maxYear && listing.year && listing.year > bb.maxYear) return false;
  if (listing.odometer && listing.odometer > bb.maxOdometer) return false;
  const make = (listing.make || "").toLowerCase();
  if ((bb.excludedMakes || []).map((m) => m.toLowerCase()).includes(make)) return false;
  if ((bb.allowedMakes || []).length && !bb.allowedMakes.map((m) => m.toLowerCase()).includes(make)) return false;

  const est = roughValue(listing);
  if (est == null) return true;                 // unknown year -- open and see
  if (est < bb.minWholesaleValue || est > bb.maxWholesaleValue) return false;
  const ask = listing.buyNow ?? listing.reserve ?? listing.currentBid;
  if (ask == null) return true;                 // no price shown -- open and see
  return est - ask > (cfg.triageMinGap ?? 1500);
}

const fmt = (n) => Math.round(n).toLocaleString("en-US");
const signed = (n) => (n >= 0 ? "+" : "-") + "$" + fmt(Math.abs(n));
const title = (s) => s.charAt(0).toUpperCase() + s.slice(1);
