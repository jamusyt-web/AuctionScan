// Content script. Reads whatever page you're on, scores it, reports back.
//
// It never fetches anything. Everything it reads was already delivered to your
// browser; this only parses text that is on screen.
//
// Two things these marketplaces do that a naive content script gets wrong:
// they render after document_idle (so reading immediately gets an empty
// shell), and they navigate without reloading (so the script never runs
// again). Both are handled below.

(async () => {
  const base = (f) => chrome.runtime.getURL(`src/${f}`);
  const [{ extractListing, labeledMoney }, scoring, { siteFor, isDetailPage }, { loadConfig },
         { pickRowElement, looksRendered, waitFor, isSafeDismissLabel, looksLikeDetailUrl }] =
    await Promise.all([
      import(base("extract.js")), import(base("scoring.js")),
      import(base("sites.js")), import(base("config.js")), import(base("page.js")),
    ]);

  const site = siteFor(location.href);
  const inFrame = window !== window.top;
  const cfg = await loadConfig();

  // A sub-frame is either the AccuTrade VIN-dow sidebar (which is where MMR
  // actually lives) or site chrome. Either way it isn't a listing page --
  // read it for valuation numbers and stop.
  if (inFrame) { await reportPanel(); return; }
  if (!site) return;

  /**
   * All the text on the page, including places innerText can't reach.
   *
   * The VIN-dow panel showed mmr=- on every car because it renders outside
   * the page's own tree: an injected sidebar is typically a shadow root or a
   * same-origin frame, and document.body.innerText sees neither.
   */
  function pageText() {
    let out = document.body?.innerText || "";
    try {
      for (const el of document.querySelectorAll("*")) {
        if (el.shadowRoot) out += "\n" + (el.shadowRoot.textContent || "");
      }
    } catch { /* a hostile page must not break the read */ }
    try {
      for (const f of document.querySelectorAll("iframe")) {
        // Cross-origin access throws; that frame is covered by all_frames
        // and reports itself via reportPanel().
        const t = f.contentDocument?.body?.innerText;
        if (t) out += "\n" + t;
      }
    } catch { /* expected for cross-origin frames */ }
    return out;
  }

  /** Clear a "click OK to continue" interstitial, carefully. */
  function dismissInterstitial() {
    const clickable = [...document.querySelectorAll(
      "button, [role='button'], input[type='button'], input[type='submit'], a.btn")];
    for (const el of clickable) {
      const label = el.innerText || el.value || el.getAttribute("aria-label") || "";
      if (!isSafeDismissLabel(label)) continue;
      const r = el.getBoundingClientRect?.();
      if (!r || r.width === 0 || r.height === 0) continue;
      try { el.click(); return label.trim(); } catch { /* keep looking */ }
    }
    return null;
  }

  /** Every link on the page that points at a vehicle detail page. */
  function detailLinks() {
    const all = [...document.querySelectorAll("a[href]")];
    const byPattern = all.filter((a) => {
      try { return site.detailUrl.test(a.href); } catch { return false; }
    });
    if (byPattern.length) return byPattern;
    // ACV returned 0 links because its URL shape isn't one I've seen. Rather
    // than fail closed, fall back to recognizing a detail URL generically.
    return all.filter((a) => looksLikeDetailUrl(a.href, location.origin));
  }

  let lastUrl = location.href;
  await handle();

  // SPA navigation: the URL changes with no page load, so re-run on change.
  const observer = new MutationObserver(() => {
    if (location.href !== lastUrl) {
      lastUrl = location.href;
      handle().catch(() => {});
    }
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });
  addEventListener("popstate", () => {
    if (location.href !== lastUrl) { lastUrl = location.href; handle().catch(() => {}); }
  });

  async function handle() {
    if (isDetailPage(location.href, site)) await handleDetail();
    else await handleResults();
  }

  // --- one car --------------------------------------------------------------

  async function handleDetail() {
    // Wait for the app to render. A VIN on the page is the strongest signal
    // that the vehicle section actually loaded.
    let dismissed = null;
    await waitFor(() => {
      dismissed ||= dismissInterstitial();
      const t = pageText();
      return /\b[A-HJ-NPR-Z0-9]{17}\b/.test(t) && looksRendered(t);
    }, { timeoutMs: 25000 });

    const text = pageText();
    const listing = extractListing(text, site);
    listing.url = location.href;

    // The sidebar frame reports separately; fold its numbers in.
    if (!listing.mmr) {
      const panel = await chrome.runtime.sendMessage({ type: "getPanel" }).catch(() => null);
      if (panel?.data) {
        for (const k of ["mmr", "targetAuction", "instantOffer", "retail", "medianRetail"]) {
          listing[k] ??= panel.data[k] ?? null;
        }
      }
    }

    const diagnostics = {
      url: location.href, kind: "detail", textLength: text.length,
      vin: listing.vin, year: listing.year, odometer: listing.odometer,
      grade: listing.grade, mmr: listing.mmr, dismissed,
      currentBid: listing.currentBid, buyNow: listing.buyNow, reserve: listing.reserve,
    };

    const deal = (listing.vin || listing.year) ? scoreOne(listing) : null;
    if (deal) {
      chrome.runtime.sendMessage({ type: "detail", deal: strip(deal), url: location.href, diagnostics });
      showBadge(deal);
    } else {
      // Say so rather than sitting there silently -- a blank page and a page
      // we failed to parse look identical from the outside otherwise.
      chrome.runtime.sendMessage({ type: "detailFailed", url: location.href, diagnostics });
      showFailure(diagnostics);
    }
  }

  // --- a page of results ----------------------------------------------------

  async function handleResults() {
    let dismissed = null;
    await waitFor(() => {
      dismissed ||= dismissInterstitial();
      return detailLinks().length > 0;
    }, { timeoutMs: 25000 });

    // Anchor each row on its own link, so a candidate always carries the URL
    // that opens it. Guessing row CSS classes for three sites sight-unseen is
    // how this returns nothing.
    const seen = new Set();
    const rows = [];
    for (const a of detailLinks()) {
      const href = a.href.split("#")[0];
      if (seen.has(href)) continue;
      seen.add(href);
      const row = pickRowElement(a);
      rows.push({ href, text: (row?.innerText || a.innerText || "").trim() });
    }

    const candidates = [];
    for (const { href, text } of rows) {
      if (!text) continue;
      const listing = extractListing(text, site, { listRow: true });
      listing.url = href;
      if (!listing.year && !listing.vin) continue;
      if (scoring.worthOpening(listing, cfg)) candidates.push({ href, listing });
    }

    chrome.runtime.sendMessage({
      type: "results",
      url: location.href,
      candidates,
      diagnostics: {
        url: location.href, kind: "results", textLength: pageText().length,
        links: rows.length, parsedRows: rows.filter((r) => r.text).length,
        candidates: candidates.length, dismissed,
        sampleRow: rows.find((r) => r.text)?.text.slice(0, 220) || "",
        // When no links are recognized, these are what the page actually had.
        // Paste them and the URL pattern can be fixed exactly.
        sampleHrefs: rows.length ? [] : [...new Set(
          [...document.querySelectorAll("a[href]")].map((a) => a.getAttribute("href"))
        )].filter(Boolean).slice(0, 10),
      },
    });
  }

  /** Sub-frame: report any valuation numbers so the main frame can use them. */
  async function reportPanel() {
    const read = () => {
      const t = document.body?.innerText || "";
      return {
        mmr: labeledMoney(t, ["Manheim", "MMR"]),
        targetAuction: labeledMoney(t, ["Target Auction"]),
        instantOffer: labeledMoney(t, ["Instant Offer"]),
        retail: labeledMoney(t, ["Retail:", "Clear Retail"]),
        medianRetail: labeledMoney(t, ["Median Retail"]),
      };
    };
    let data = read();
    if (!data.mmr && !data.targetAuction) {
      await waitFor(() => { data = read(); return Boolean(data.mmr || data.targetAuction); },
        { timeoutMs: 20000 });
    }
    if (data.mmr || data.targetAuction) {
      chrome.runtime.sendMessage({ type: "panel", data }).catch(() => {});
    }
  }

  // --- scoring --------------------------------------------------------------

  function scoreOne(listing) {
    // Prefer the panel's own numbers; fall back to the rough curve so a car
    // still gets a provisional read when no valuation is on screen.
    const wholesale = listing.mmr ?? listing.targetAuction ?? null;
    const valuation = wholesale
      ? { source: "accutrade", wholesale, mmr: listing.mmr, retail: listing.retail,
          adjustedForMileage: true, adjustedForCondition: false }
      : { source: "heuristic", wholesale: scoring.roughValue(listing), retail: null,
          adjustedForMileage: true, adjustedForCondition: false };
    if (!valuation.wholesale) return null;
    return scoring.scoreListing(listing, valuation, cfg);
  }

  // --- on-page verdict ------------------------------------------------------

  function panel(bg) {
    document.getElementById("auctionscan-badge")?.remove();
    const el = document.createElement("div");
    el.id = "auctionscan-badge";
    el.style.cssText = `position:fixed;bottom:18px;left:18px;z-index:2147483647;
      max-width:360px;padding:12px 14px;border-radius:10px;font:13px/1.45
      -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#fff;
      background:${bg};box-shadow:0 4px 16px rgba(0,0,0,.35);cursor:pointer;`;
    el.onclick = () => el.remove();
    document.body.appendChild(el);
    return el;
  }

  function showBadge(deal) {
    const acq = deal.acquisition || {};
    const good = deal.score >= (cfg.alertScore ?? 25);
    const price = acq.price != null ? `$${Math.round(acq.price).toLocaleString()}` : "reserve?";
    const el = panel(good ? "#12603a" : "#3a3a42");
    el.innerHTML = `
      <div style="font-weight:700;font-size:15px;margin-bottom:4px">
        ${good ? "WORTH A LOOK" : "PASS"} &middot; score ${Math.round(deal.score)}</div>
      <div style="opacity:.9">Max bid <b>$${Math.round(deal.maxBid).toLocaleString()}</b>
        &middot; price ${price} &middot; ${deal.exitChannel || ""}</div>
      <div style="margin-top:6px">${esc(acq.action || "")}</div>
      ${deal.riskFlags?.length
        ? `<div style="margin-top:6px;color:#ffc9c9">! ${esc(deal.riskFlags.join("; "))}</div>` : ""}
      <div style="margin-top:8px;opacity:.6;font-size:11px">click to dismiss</div>`;
  }

  function showFailure(d) {
    const el = panel("#6b2020");
    const missing = ["vin", "year", "odometer"].filter((k) => !d[k]);
    el.innerHTML = `
      <div style="font-weight:700;font-size:15px;margin-bottom:4px">
        Couldn't read this page</div>
      <div style="opacity:.9">Page text: ${d.textLength} chars.
        Missing: ${missing.join(", ") || "nothing obvious"}.</div>
      <div style="margin-top:6px;font-size:12px;opacity:.85">
        ${d.textLength < 400
          ? "The page looks empty -- it may still have been loading."
          : "Text loaded but the fields didn't parse. Send this screenshot."}</div>
      <div style="margin-top:8px;opacity:.6;font-size:11px">click to dismiss</div>`;
  }

  const esc = (s) => String(s).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  // Deals carry the full listing; keep the message small.
  function strip(deal) {
    return {
      score: deal.score, maxBid: deal.maxBid, maxBidOptimistic: deal.maxBidOptimistic,
      headroom: deal.headroom, exitChannel: deal.exitChannel, executable: deal.executable,
      speculative: deal.speculative, confidence: deal.confidence,
      riskFlags: deal.riskFlags, reasoning: deal.reasoning,
      acquisition: deal.acquisition, listing: deal.listing,
    };
  }
})();
