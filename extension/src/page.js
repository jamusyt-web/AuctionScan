// DOM reading helpers, kept free of direct document access so they can be
// tested without a browser.

/**
 * Walk up from a link to the element that represents its result row.
 *
 * Anchored on the link rather than a CSS class, because guessing class names
 * for marketplaces whose markup you've never seen is how a parser silently
 * returns nothing.
 *
 * The stopping rule is link count, not text length. A result row contains
 * exactly ONE vehicle link; the list containing it has dozens. Character
 * count is useless as a boundary -- a sparse listing page can be under 3,000
 * characters in total, so every ancestor "looks" row-sized and the climb runs
 * all the way to the header. (That is exactly what happened on ACV: rows came
 * back as "NOTIFICATIONS MENU / Jamus Killian / PLATINUM AUTO SALES".)
 *
 * `el` needs `innerText` and `parentElement`. `linkCount` counts vehicle
 * links inside an element; without it this falls back to text size alone.
 */
export function pickRowElement(
  // minChars is deliberately low: a real row can be as terse as
  // "2021 Ford F-150 62,400 mi $18,500". Link count is what actually stops
  // the climb, so this only has to exclude a bare link label.
  el, { minChars = 24, maxChars = 3000, maxHops = 8, linkCount = null } = {},
) {
  let best = null;
  for (let i = 0; i < maxHops && el; i++) {
    // Stop before the element that holds more than this one car.
    if (linkCount && linkCount(el) > 1) break;
    const text = (el.innerText || "").trim();
    if (text.length > maxChars) break;
    if (text.length >= minChars) best = el;
    el = el.parentElement;
  }
  return best;
}

/**
 * Does this text actually describe a vehicle?
 *
 * Triage ran on page furniture and "passed" 16 cars that were really the
 * site header. A row has to carry a plausible model year plus either
 * mileage or a price before it is worth believing.
 */
export function looksLikeVehicleRow(text, currentYear = 2026) {
  const t = String(text || "");
  const year = t.match(/\b(19[89]\d|20[0-4]\d)\b/);
  if (!year || Number(year[1]) > currentYear + 1) return false;
  const hasMiles = /[0-9][0-9,]{2,}\s*(?:mi\b|miles\b)/i.test(t);
  const hasMoney = /\$\s*[0-9][0-9,]{2,}/.test(t);
  return hasMiles || hasMoney;
}

/** Has the app actually rendered, or are we looking at an empty shell? */
export function looksRendered(text, { minChars = 400 } = {}) {
  if (!text) return false;
  const t = text.trim();
  if (t.length < minChars) return false;
  // A shell that only says "Loading" is not rendered.
  return !/^\s*(loading|please wait)\.{0,3}\s*$/i.test(t);
}

/**
 * Poll until `check()` is satisfied or we run out of time.
 *
 * Single-page apps render after document_idle, so reading the DOM the moment
 * the content script runs gets an empty page. This is why a listing showed no
 * badge and a browse page yielded no rows.
 */
export async function waitFor(check, { timeoutMs = 20000, intervalMs = 400 } = {}) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    try { if (check()) return true; } catch { /* keep polling */ }
    if (Date.now() >= deadline) return false;
    await new Promise((r) => setTimeout(r, intervalMs));
  }
}

// --- dismissing the interstitial ------------------------------------------

// Buttons that are safe to click to clear a "click OK to continue" dialog.
// Matched against the WHOLE trimmed label, never a substring, so "OK" matches
// but "OK, place my bid" does not.
const SAFE_DISMISS = [
  /^ok$/i, /^okay$/i, /^accept$/i, /^accept all$/i, /^i agree$/i, /^agree$/i,
  /^continue$/i, /^got it$/i, /^close$/i, /^dismiss$/i, /^understood$/i,
  /^acknowledge$/i, /^i understand$/i, /^proceed$/i, /^enter$/i, /^next$/i,
];

// Anything that could commit money or change state. Checked first and wins
// outright -- on an auction site an accidental click is not a cosmetic bug.
const NEVER_CLICK =
  /bid|buy|offer|purchase|checkout|pay|invoice|submit|confirm|delete|remove|sell|list|arbitrat|transport|finance/i;

export function isSafeDismissLabel(label) {
  const text = String(label || "").trim();
  if (!text || text.length > 24) return false;
  if (NEVER_CLICK.test(text)) return false;
  return SAFE_DISMISS.some((re) => re.test(text));
}

// --- recognizing a vehicle link -------------------------------------------

/**
 * Does this URL look like a vehicle detail page?
 *
 * Fallback for when a site's specific pattern misses. Requires a path segment
 * that reads like an id -- a long hex/alphanumeric slug or a run of digits --
 * under a vehicle-ish path, which is what every marketplace's VDP URL is.
 */
export function looksLikeDetailUrl(href, origin = "") {
  let path;
  try {
    const u = new URL(href, origin || undefined);
    path = u.pathname;
  } catch { return false; }
  if (!/\/(details?|vehicles?|listings?|lots?|items?|auctions?|inventory|cars?)\//i.test(path)) {
    return false;
  }
  return path.split("/").some((seg) => /^[a-f0-9]{12,}$/i.test(seg) || /^\d{5,}$/.test(seg)
    || /^[a-z0-9]{8,}-[a-z0-9-]{4,}$/i.test(seg));
}

// --- telling the results grid from the carousels ---------------------------

/**
 * Keep only the links belonging to the page's main results list.
 *
 * A marketplace page shows more than one set of cars: the search results you
 * filtered for, plus "recommended for you" and "similar vehicles" rails. They
 * are indistinguishable by URL -- ACV's own `was_recommendation` parameter
 * exists precisely because it needs to track which rail a click came from.
 *
 * The results grid is the biggest group, so group the links by their list
 * container and keep the largest. Selector-free, and it survives a redesign.
 *
 * `containerOf(link)` returns the nearest ancestor holding more than one
 * vehicle link.
 */
export function mainListLinks(links, containerOf) {
  if (!links || links.length < 2) return links || [];

  const groups = new Map();
  for (const link of links) {
    const key = containerOf(link) ?? null;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(link);
  }
  if (groups.size <= 1) return links;

  let best = null;
  for (const arr of groups.values()) {
    if (!best || arr.length > best.length) best = arr;
  }
  // Only trust the split when one group is clearly the list. A near-tie
  // means the grouping didn't find a real boundary; keep everything rather
  // than silently discarding half the page's inventory.
  const rest = [...groups.values()].filter((a) => a !== best);
  const runnerUp = rest.reduce((m, a) => Math.max(m, a.length), 0);
  return best.length >= Math.max(2, runnerUp * 2) ? best : links;
}

/** Sizes of each link group, for diagnostics. */
export function listGroupSizes(links, containerOf) {
  const groups = new Map();
  for (const link of links || []) {
    const key = containerOf(link) ?? null;
    groups.set(key, (groups.get(key) || 0) + 1);
  }
  return [...groups.values()].sort((a, b) => b - a);
}
