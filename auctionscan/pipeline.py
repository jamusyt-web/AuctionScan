"""Wire the pieces together: listings in, ranked deals out.

Order matters for cost. Cheap filters run first so you never pay for a
valuation or an AI call on a car that was never in your buy box.
"""

from __future__ import annotations

from collections.abc import Iterable

from .ai.condition import ConditionAnalyzer
from .config import Config
from .enrich.history import HistoryProvider, NullHistory
from .enrich.valuation import ValuationProvider
from .enrich.vin import decode_vin, validate_vin
from .models import Deal, Listing
from .scoring import rank, score_listing


def prefilter(listings: Iterable[Listing], cfg: Config) -> tuple[list[Listing], list[str]]:
    """Drop obvious non-starters before spending money on enrichment."""
    kept: list[Listing] = []
    dropped: list[str] = []
    bb = cfg.buy_box

    for listing in listings:
        if not validate_vin(listing.vin):
            dropped.append(f"{listing.listing_id}: bad VIN {listing.vin}")
            continue
        if listing.odometer and listing.odometer > bb.max_odometer:
            dropped.append(f"{listing.vin}: {listing.odometer:,} miles")
            continue
        if bb.min_year and listing.year and listing.year < bb.min_year:
            dropped.append(f"{listing.vin}: {listing.year}")
            continue
        if bb.max_year and listing.year and listing.year > bb.max_year:
            dropped.append(f"{listing.vin}: {listing.year}")
            continue
        make = listing.make.lower()
        if make and bb.excluded_makes and make in [m.lower() for m in bb.excluded_makes]:
            dropped.append(f"{listing.vin}: excluded make {listing.make}")
            continue
        if make and bb.allowed_makes and make not in [m.lower() for m in bb.allowed_makes]:
            dropped.append(f"{listing.vin}: {listing.make} not in buy box")
            continue
        kept.append(listing)

    return kept, dropped


def run(
    listings: Iterable[Listing],
    valuation: ValuationProvider,
    cfg: Config | None = None,
    history: HistoryProvider | None = None,
    analyzer: ConditionAnalyzer | None = None,
    decode: bool = False,
    use_batch: bool = True,
    transport_miles: int | None = None,
) -> tuple[list[Deal], list[str]]:
    """Run the full pipeline. Returns (ranked deals, dropped-listing notes)."""
    cfg = cfg or Config()
    history = history or NullHistory()

    candidates, dropped = prefilter(listings, cfg)
    if not candidates:
        return [], dropped

    # AI condition pass. Batch when there's enough volume to be worth the poll.
    assessments = {}
    if analyzer is not None:
        if use_batch and len(candidates) >= 5:
            assessments = analyzer.analyze_batch(candidates)
        else:
            for listing in candidates:
                result = analyzer.analyze(listing)
                if result:
                    assessments[listing.vin] = result

    deals: list[Deal] = []
    for listing in candidates:
        decoded = None
        if decode:
            decoded = decode_vin(listing.vin)
            # Trust the VIN over the run list for identity fields.
            if decoded.valid:
                listing.year = listing.year or decoded.year
                listing.make = listing.make or decoded.make
                listing.model = listing.model or decoded.model

        deal = score_listing(
            listing=listing,
            valuation=valuation.value(listing),
            history=history.lookup(listing.vin),
            condition=assessments.get(listing.vin),
            cfg=cfg,
            transport_miles=transport_miles,
        )
        deal.vin_decode = decoded
        deals.append(deal)

    return rank(deals), dropped
