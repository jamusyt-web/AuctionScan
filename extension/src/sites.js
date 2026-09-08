// Per-marketplace hints.
//
// Only label vocabulary and URL shapes live here -- no CSS selectors, so a
// redesign changes wording at worst rather than breaking the parser outright.
// `rowSelector` is a hint for splitting results pages; if it misses, the
// content script falls back to heuristic block splitting.

export const SITES = {
  adesa: {
    name: "adesa",
    match: /(adesa|openlane)\.com/i,
    detailUrl: /\/details\/|\/vehicle\//i,
    rowSelector: "[data-testid*='result'], [class*='result-card'], [class*='vehicle-card'], article",
    labels: {
      currentBid: ["Current Bid", "Opening bid", "High Bid", "Current High Bid"],
      buyNow: ["Buy Now", "Buy It Now"],
      reserve: ["Reserve Price", "Reserve"],
      retail: ["Retail:", "Clear Retail"],
      mmr: ["Manheim", "MMR"],
      targetAuction: ["Target Auction"],
      instantOffer: ["Instant Offer"],
      medianRetail: ["Median Retail"],
    },
  },
  manheim: {
    name: "manheim",
    match: /manheim\.com/i,
    detailUrl: /\/vehicle|\/lot\/|\/details/i,
    rowSelector: "[class*='vehicle-row'], [class*='listing'], article, tr[data-vin]",
    labels: {
      currentBid: ["Current Bid", "High Bid", "Start Price", "Opening bid"],
      buyNow: ["Buy Now", "Buy It Now"],
      reserve: ["Reserve Price", "Reserve"],
      retail: ["Retail:"],
      mmr: ["MMR", "Manheim Market Report", "Manheim"],
      targetAuction: ["Target Auction"],
      instantOffer: ["Instant Offer"],
      medianRetail: ["Median Retail"],
    },
  },
  acv: {
    name: "acv",
    match: /acvauctions\.com/i,
    // ACV's vehicle page is /auction/<id> -- singular. The plural pattern
    // that used to be here never matched, so links were only ever found by
    // the generic fallback, which also sweeps up recommendation carousels.
    detailUrl: /\/auction\/\d+|\/vehicle\/|\/listing\//i,
    rowSelector: "[data-testid*='vehicle'], [class*='vehicle-card'], [class*='listing-card'], article",
    // ACV only populates the VIN once a car is opened, so results-page rows
    // are triaged on year/make/model/miles/price alone. See scoring.worthOpening.
    vinOnDetailOnly: true,
    labels: {
      currentBid: ["Current Bid", "High Bid", "Current Price"],
      buyNow: ["Buy Now", "Buy It Now"],
      reserve: ["Reserve", "Seller's Reserve", "Reserve Price"],
      retail: ["Retail:"],
      mmr: ["Manheim", "MMR"],
      targetAuction: ["Target Auction"],
      instantOffer: ["Instant Offer"],
      medianRetail: ["Median Retail"],
    },
  },
};

export function siteFor(url) {
  for (const site of Object.values(SITES)) {
    if (site.match.test(url)) return site;
  }
  return null;
}

export function isDetailPage(url, site) {
  return Boolean(site?.detailUrl?.test(url));
}
