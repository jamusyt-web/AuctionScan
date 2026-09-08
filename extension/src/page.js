// DOM reading helpers, kept free of direct document access so they can be
// tested without a browser.

/**
 * Walk up from a link to the element that represents its result row.
 *
 * Written this way on purpose: guessing CSS class names for three
 * marketplaces without ever seeing their markup is how a parser silently
 * returns nothing. Every result row must contain a link to the car, so we
 * anchor on the link -- which we can identify by URL shape -- and climb until
 * we have an element holding a row's worth of text.
 *
 * `el` only needs `innerText` and `parentElement`.
 */
export function pickRowElement(el, { minChars = 40, maxChars = 3000, maxHops = 6 } = {}) {
  let best = null;
  for (let i = 0; i < maxHops && el; i++) {
    const text = (el.innerText || "").trim();
    if (text.length > maxChars) break;      // climbed into the whole list
    if (text.length >= minChars) best = el; // keep the largest still row-sized
    el = el.parentElement;
  }
  return best;
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
