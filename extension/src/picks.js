// Which finds get handed to the hourly Claude agent.
//
// The extension's own score is a quick read: a flat recon number and a
// guessed transport distance, because it can't read damage photos or a
// Carfax box. That makes it a good filter and a poor final answer. So the
// extension doesn't decide "buy" -- it decides "worth Claude's time", and the
// agent opens each pick and reads the condition report properly.
//
// Pure functions only, so they run under `node --test` without a browser.

export const PICKS_FILENAME = "AuctionScan/picks.json";
// Re-save at least this often even when nothing changed, so the agent can
// tell a quiet market from an extension that stopped sweeping.
export const HEARTBEAT_MS = 3 * 60 * 60 * 1000;

/** Sale format of a find, as the scorer decided it. */
export function formatOf(find) {
  return find?.acquisition?.format || find?.listing?.saleFormat || "unknown";
}

/** True when the find's format is one you buy (empty list = all formats). */
export function formatAllowed(find, cfg) {
  const wanted = cfg?.formats || [];
  return !wanted.length || wanted.includes(formatOf(find));
}

/** Did the page give us real book numbers (the VIN-dow panel), or a guess? */
export function hasBookValue(find) {
  const l = find?.listing || {};
  return Boolean(l.mmr || l.targetAuction);
}

// Flags that depend on the value estimate. On a car priced off the rough
// curve those flags are as unreliable as the curve, so they don't count.
const VALUE_FLAG = /^Value \$/;

/**
 * Is this find worth sending to the agent?
 *
 * - a sale format you buy (Buy Now / Make Offer by default),
 * - a price you can actually take (not a hidden-reserve bid),
 * - clean on the buy box,
 * - and under your max -- or within `pickSlack` of it -- on the quick score.
 *
 * A car whose VIN-dow panel wasn't read still goes through when everything
 * else checks out: the agent reads the panel itself, and silently dropping
 * it is how a misread turned into "no deals today" before.
 */
export function isPick(find, cfg, now = Date.now()) {
  if (!find) return false;
  const maxAgeMs = (cfg?.picksMaxAgeHours ?? 48) * 60 * 60 * 1000;
  if (find.seenAt && now - find.seenAt > maxAgeMs) return false;
  if (!formatAllowed(find, cfg)) return false;
  if (!find.executable) return false;

  const book = hasBookValue(find);
  const flags = (find.riskFlags || []).filter((f) => book || !VALUE_FLAG.test(f));
  if (flags.length) return false;

  if (!book) return true;
  const slack = cfg?.pickSlack ?? 1000;
  return (find.headroom ?? -Infinity) >= -slack;
}

const num = (v) => (v == null || v === "" ? null : Number(v));

/** The record the agent reads for one pick. */
export function pickRecord(find) {
  const l = find.listing || {};
  const acq = find.acquisition || {};
  return {
    url: find.url || l.url || null,
    vin: l.vin || null,
    source: l.source || null,
    year: num(l.year), make: l.make || null, model: l.model || null, trim: l.trim || null,
    odometer: num(l.odometer),
    format: formatOf(find),
    price: num(acq.price),
    buyNow: num(l.buyNow), reserve: num(l.reserve), currentBid: num(l.currentBid),
    makeOffer: Boolean(l.makeOffer),
    grade: num(l.grade),
    announcements: l.announcements || [],
    drivable: l.drivable ?? null,
    asIs: Boolean(l.asIs),
    book: hasBookValue(find)
      ? { mmr: num(l.mmr), targetAuction: num(l.targetAuction),
          instantOffer: num(l.instantOffer), retail: num(l.retail),
          medianRetail: num(l.medianRetail) }
      : null,
    quick: {
      maxBid: num(find.maxBid), headroom: num(find.headroom),
      exit: find.exitChannel || null, score: num(find.score),
      action: acq.action || "", alerted: Boolean(find.alerted),
    },
    seenAt: find.seenAt ? new Date(find.seenAt).toISOString() : null,
  };
}

/**
 * Everything the agent needs in one file.
 *
 * `signature` covers the picks only, not timestamps, so the background can
 * skip re-saving an unchanged list.
 */
export function buildPicksPayload(finds, cfg, { now = Date.now(), lastSweep = null } = {}) {
  const picks = (finds || []).filter((f) => isPick(f, cfg, now)).map(pickRecord);
  const signature = JSON.stringify(picks.map((p) => [p.url, p.vin, p.price, p.quick.maxBid]));
  return {
    payload: {
      kind: "auctionscan-picks",
      version: 1,
      generatedAt: new Date(now).toISOString(),
      lastSweep,
      formats: cfg?.formats || [],
      count: picks.length,
      picks,
    },
    signature,
  };
}

/** Save again only when the picks changed, or the heartbeat is due. */
export function shouldExport(signature, last, now = Date.now()) {
  if (!last) return true;
  if (last.signature !== signature) return true;
  return now - (last.at || 0) >= HEARTBEAT_MS;
}
