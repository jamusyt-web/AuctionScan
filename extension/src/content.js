// Content script. Reads whatever page you're on, scores it, and reports back.
//
// It never fetches anything. Everything it reads was already delivered to your
// browser for you; the extension just parses text that is on screen.

(async () => {
  const base = (f) => chrome.runtime.getURL(`src/${f}`);
  const [{ extractListing, splitRows }, scoring, { siteFor, isDetailPage }, { loadConfig }] =
    await Promise.all([
      import(base("extract.js")), import(base("scoring.js")),
      import(base("sites.js")), import(base("config.js")),
    ]);

  const site = siteFor(location.href);
  if (!site) return;
  const cfg = await loadConfig();

  const pageText = () => document.body?.innerText || "";

  function rowTexts() {
    if (!site.rowSelector) return [];
    let nodes = [];
    try {
      nodes = [...document.querySelectorAll(site.rowSelector)];
    } catch { /* a bad selector must not take the page down */ }
    // Reject absurdly large "rows" -- a selector that matched a container
    // would otherwise swallow the whole list into one entry.
    return nodes
      .map((n) => n.innerText || "")
      .filter((t) => t.length > 40 && t.length < 4000);
  }

  function scoreOne(listing) {
    // Prefer the panel's own numbers; fall back to the rough curve so a car
    // still gets a provisional read when no valuation is on screen.
    const wholesale = listing.mmr ?? listing.targetAuction ?? null;
    const valuation = wholesale
      ? { source: "accutrade", wholesale, mmr: listing.mmr,
          retail: listing.retail, adjustedForMileage: true, adjustedForCondition: false }
      : { source: "heuristic", wholesale: scoring.roughValue(listing),
          retail: null, adjustedForMileage: true, adjustedForCondition: false };
    if (!valuation.wholesale) return null;
    return scoring.scoreListing(listing, valuation, cfg);
  }

  if (isDetailPage(location.href, site)) {
    const listing = extractListing(pageText(), site);
    listing.url = location.href;
    const deal = listing.vin || listing.year ? scoreOne(listing) : null;
    if (deal) {
      chrome.runtime.sendMessage({ type: "detail", deal: strip(deal), url: location.href });
      showBadge(deal);
    } else {
      chrome.runtime.sendMessage({ type: "detailFailed", url: location.href });
    }
    return;
  }

  // Results page: triage every row without opening anything.
  const rows = splitRows(pageText(), rowTexts());
  const candidates = [];
  for (const text of rows) {
    const listing = extractListing(text, site, { listRow: true });
    if (!listing.year && !listing.vin) continue;
    if (scoring.worthOpening(listing, cfg)) candidates.push(listing);
  }
  const links = [...document.querySelectorAll("a[href]")]
    .map((a) => a.href)
    .filter((h) => site.detailUrl.test(h));

  chrome.runtime.sendMessage({
    type: "results",
    scanned: rows.length,
    candidates,
    links: [...new Set(links)],
    url: location.href,
  });

  // --- on-page verdict ------------------------------------------------------

  function showBadge(deal) {
    document.getElementById("auctionscan-badge")?.remove();
    const acq = deal.acquisition || {};
    const good = deal.score >= (cfg.alertScore ?? 25);
    const el = document.createElement("div");
    el.id = "auctionscan-badge";
    el.style.cssText = `position:fixed;bottom:18px;left:18px;z-index:2147483647;
      max-width:340px;padding:12px 14px;border-radius:10px;font:13px/1.45
      -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#fff;
      background:${good ? "#12603a" : "#3a3a42"};box-shadow:0 4px 16px rgba(0,0,0,.35);`;
    const price = acq.price != null ? `$${Math.round(acq.price).toLocaleString()}` : "reserve?";
    el.innerHTML = `
      <div style="font-weight:700;font-size:15px;margin-bottom:4px">
        ${good ? "WORTH A LOOK" : "PASS"} &middot; score ${Math.round(deal.score)}
      </div>
      <div style="opacity:.9">Max bid <b>$${Math.round(deal.maxBid).toLocaleString()}</b>
        &middot; price ${price} &middot; ${deal.exitChannel || ""}</div>
      <div style="margin-top:6px;opacity:.95">${escape(acq.action || "")}</div>
      ${deal.riskFlags?.length
        ? `<div style="margin-top:6px;color:#ffc9c9">! ${escape(deal.riskFlags.join("; "))}</div>` : ""}
      <div style="margin-top:8px;opacity:.6;font-size:11px">click to dismiss</div>`;
    el.onclick = () => el.remove();
    document.body.appendChild(el);
  }

  function escape(s) {
    return String(s).replace(/[&<>"]/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }

  // Deals carry a listing object with page text; keep the message small.
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
