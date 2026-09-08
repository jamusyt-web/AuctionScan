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
         { pickRowElement, looksRendered, waitFor, isSafeDismissLabel,
           looksLikeDetailUrl, looksLikeVehicleRow, mainListLinks, listGroupSizes }] =
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

  /** The element holding just this car, and its text. */
  function rowFor(anchor) {
    const row = pickRowElement(anchor, {
      linkCount: countVehicleLinks,
    });
    return { text: (row?.innerText || anchor.innerText || "").trim() };
  }

  /** Nearest ancestor holding more than one vehicle link: the link's list. */
  function listContainerOf(anchor) {
    let el = anchor.parentElement;
    for (let i = 0; i < 10 && el; i++) {
      if (countVehicleLinks(el) > 1) return el;
      el = el.parentElement;
    }
    return null;
  }

  function countVehicleLinks(el) {
    try {
      return [...(el.querySelectorAll?.("a[href]") || [])].filter(isVehicleLink).length;
    } catch { return 0; }
  }

  function isVehicleLink(a) {
    try {
      return site.detailUrl.test(a.href) || looksLikeDetailUrl(a.href, location.origin);
    } catch { return false; }
  }

  let lastUrl = location.href;
  await handle();

  // SPA navigation: the URL changes with no page load, so re-run on change.
  //
  // Compare the PATH only and rate-limit. Comparing full URLs re-fired on
  // every query-string tweak the app made to itself, which is why one page
  // logged six times with different counts each pass.
  const pathOf = (u) => { try { return new URL(u).pathname; } catch { return u; } };
  let lastPath = pathOf(location.href);
  let lastRun = Date.now();
  let running = false;

  async function maybeRehandle() {
    const path = pathOf(location.href);
    if (path === lastPath || running || Date.now() - lastRun < 4000) return;
    lastPath = path;
    lastUrl = location.href;
    running = true;
    try { await handle(); } catch { /* ignore */ } finally {
      running = false;
      lastRun = Date.now();
    }
  }

  const observer = new MutationObserver(maybeRehandle);
  observer.observe(document.documentElement, { childList: true, subtree: true });
  addEventListener("popstate", maybeRehandle);

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
    // Waiting for "at least one link" was too weak: 37 links existed while
    // the cards held 63 characters each, so triage ran on an unrendered page.
    // Hold out for rows that actually read like vehicles.
    let dismissed = null;
    await waitFor(() => {
      dismissed ||= dismissInterstitial();
      const links = mainListLinks(detailLinks(), listContainerOf);
      if (!links.length) return false;
      const sample = links.slice(0, 6).map((a) => rowFor(a).text);
      return sample.filter(looksLikeVehicleRow).length >= Math.min(3, links.length);
    }, { timeoutMs: 25000 });

    // Anchor each row on its own link, so a candidate always carries the URL
    // that opens it. Guessing row CSS classes for three sites sight-unseen is
    // how this returns nothing.
    // A marketplace page carries several sets of cars -- your filtered
    // results plus "recommended" rails. They look identical by URL, which is
    // how a car you never searched for ended up being opened.
    const allLinks = detailLinks();
    const groupSizes = listGroupSizes(allLinks, listContainerOf);
    const gridLinks = mainListLinks(allLinks, listContainerOf);

    const seen = new Set();
    const rows = [];
    for (const a of gridLinks) {
      const href = a.href.split("#")[0];
      if (seen.has(href)) continue;
      seen.add(href);
      rows.push({ href, ...rowFor(a) });
    }

    const candidates = [];
    let rejected = 0;
    for (const { href, text } of rows) {
      // Reject page furniture before it can "pass" triage. Site navigation
      // parsed as a car is worse than no candidate at all -- it spends a
      // page open on nothing.
      if (!looksLikeVehicleRow(text)) { rejected += 1; continue; }
      const listing = extractListing(text, site, { listRow: true });
      listing.url = href;
      if (!listing.year && !listing.vin) { rejected += 1; continue; }
      if (scoring.worthOpening(listing, cfg)) candidates.push({ href, listing });
    }

    chrome.runtime.sendMessage({
      type: "results",
      url: location.href,
      candidates,
      diagnostics: {
        url: location.href, kind: "results", textLength: pageText().length,
        links: rows.length, allLinks: allLinks.length, groupSizes,
        parsedRows: rows.filter((r) => r.text).length,
        candidates: candidates.length, rejected, dismissed,
        sampleRow: rows.find((r) => r.text)?.text.slice(0, 220) || "",
        // When no links are recognized, these are what the page actually had.
        // Paste them and the URL pattern can be fixed exactly.
        sampleHrefs: rows.length ? [] : [...new Set(
          [...document.querySelectorAll("a[href]")].map((a) => a.getAttribute("href"))
        )].filter(Boolean).slice(0, 10),
        // What it decided to open, so a wrong pick is visible immediately.
        picked: candidates.slice(0, 5).map((c) => ({
          href: c.href, text: (c.listing.description || "").slice(0, 70)
            || `${c.listing.year || ""} ${c.listing.make || ""} ${c.listing.model || ""}`.trim(),
        })),
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
