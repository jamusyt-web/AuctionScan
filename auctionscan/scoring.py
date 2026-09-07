"""The math: turn an enriched listing into a walk-away number and a score.

The core equation is the one every wholesale buyer runs in their head:

    max_bid = wholesale_value
              - reconditioning
              - transport
              - buy fee - sell fee
              - floorplan/holding
              - the gross you demand

Everything else here is adjusting `wholesale_value` down for the things that
make another dealer pay less (accidents, brands, grade, mileage) and refusing
to bid at all when the car is outside the buy box.
"""

from __future__ import annotations

from .config import Config
from .models import (
    ConditionAssessment, Deal, HistoryReport, Listing, TitleBrand, Valuation, VinDecode,
)
from .risk import estimate_reserve, flagged_problems, is_as_is

# How much of book a car loses in the wholesale lane for history damage.
# These are haircuts on wholesale value, drawn from how announced cars
# actually trade relative to clean MMR. Tune them against your own sales.
ACCIDENT_HAIRCUT = {"": 0.0, "minor": 0.03, "moderate": 0.08, "severe": 0.15}
STRUCTURAL_HAIRCUT = 0.18
AIRBAG_HAIRCUT = 0.10
BRAND_HAIRCUT = {
    TitleBrand.CLEAN: 0.0,
    TitleBrand.UNKNOWN: 0.0,
    TitleBrand.HAIL: 0.12,
    TitleBrand.LEMON: 0.20,
    TitleBrand.TMU: 0.25,
    TitleBrand.REBUILT: 0.35,
    TitleBrand.FLOOD: 0.45,
    TitleBrand.SALVAGE: 0.50,
}
# Seller announcements carry the same weight as a history report -- often more,
# because they are made under auction arbitration rules. Each keyword maps to
# the buy-box permission that governs it; if that permission is off, the
# announcement is disqualifying on its own, with or without a Carfax.
HARD_ANNOUNCEMENTS = {
    "frame damage": "allow_structural_damage",
    "structural": "allow_structural_damage",
    "unibody": "allow_structural_damage",
    "frame": "allow_structural_damage",
    "salvage": "allow_branded_title",
    "rebuilt": "allow_branded_title",
    "flood": "allow_branded_title",
    "water damage": "allow_branded_title",
    "true mileage unknown": "allow_branded_title",
    "tmu": "allow_branded_title",
    "not actual mileage": "allow_branded_title",
    "mileage unknown": "allow_branded_title",
    "exceeds mechanical limits": "allow_branded_title",
    "airbag": "allow_airbag_deployed",
}

# AutoGrade is 0-5. A 4.0+ car trades at or above MMR; below 3.0 it gets ugly.
GRADE_ADJUSTMENT = {5: 0.04, 4: 0.01, 3: -0.04, 2: -0.12, 1: -0.22, 0: -0.30}

# Rough industry rule: ~$0.06/mile of value swing around a 12k/yr baseline,
# capped so it can't run away on outliers. Only used when the valuation source
# has not already adjusted for mileage.
MILEAGE_BASELINE_PER_YEAR = 12_000
MILEAGE_DOLLARS_PER_MILE = 0.06
MILEAGE_ADJUSTMENT_CAP_PCT = 0.20


def adjusted_wholesale(
    listing: Listing,
    valuation: Valuation,
    history: HistoryReport | None = None,
    current_year: int = 2026,
) -> tuple[float, list[str]]:
    """Discount book value for everything that makes a car harder to resell."""
    base = valuation.wholesale or 0.0
    notes: list[str] = []
    value = base

    if not valuation.adjusted_for_mileage and listing.odometer and listing.year:
        age = max(1, current_year - listing.year)
        expected = age * MILEAGE_BASELINE_PER_YEAR
        delta_miles = expected - listing.odometer          # positive = low miles
        adj = delta_miles * MILEAGE_DOLLARS_PER_MILE
        cap = base * MILEAGE_ADJUSTMENT_CAP_PCT
        adj = max(-cap, min(cap, adj))
        if abs(adj) > 50:
            value += adj
            notes.append(
                f"Mileage: {listing.odometer:,} vs {expected:,} expected "
                f"({adj:+,.0f})"
            )

    if not valuation.adjusted_for_condition and listing.grade is not None:
        # Round, don't truncate -- a 3.8 car is a 4, not a 3.
        pct = GRADE_ADJUSTMENT.get(round(listing.grade), -0.30)
        if pct:
            value += base * pct
            notes.append(f"Grade {listing.grade:.1f}: {base * pct:+,.0f}")

    if history:
        if history.accident_count:
            pct = ACCIDENT_HAIRCUT.get(history.worst_accident_severity, 0.08)
            # A second accident hurts, but not linearly.
            pct *= 1.0 + 0.4 * (history.accident_count - 1)
            value -= base * pct
            notes.append(
                f"{history.accident_count} accident(s) "
                f"({history.worst_accident_severity or 'unspecified'}): "
                f"-{base * pct:,.0f}"
            )
        if history.structural_damage:
            value -= base * STRUCTURAL_HAIRCUT
            notes.append(f"Structural damage: -{base * STRUCTURAL_HAIRCUT:,.0f}")
        if history.airbag_deployed:
            value -= base * AIRBAG_HAIRCUT
            notes.append(f"Airbag deployed: -{base * AIRBAG_HAIRCUT:,.0f}")
        for brand in history.title_brands:
            pct = BRAND_HAIRCUT.get(brand, 0.0)
            if pct:
                value -= base * pct
                notes.append(f"Title brand {brand.value}: -{base * pct:,.0f}")

    return max(0.0, value), notes


def recon_estimate(condition: ConditionAssessment | None, cfg: Config) -> float:
    """Recon dollars, with a safety margin on AI estimates."""
    if condition is None:
        return cfg.costs.baseline_recon
    est = condition.estimated_recon_cost * cfg.costs.recon_safety_multiplier
    return max(cfg.costs.baseline_recon, est)


def check_buy_box(
    listing: Listing,
    value: float,
    history: HistoryReport | None,
    recon: float,
    cfg: Config,
) -> list[str]:
    """Hard disqualifiers. A non-empty list means don't bid, at any price."""
    bb = cfg.buy_box
    flags: list[str] = []

    if bb.min_year and listing.year and listing.year < bb.min_year:
        flags.append(f"Year {listing.year} below minimum {bb.min_year}")
    if bb.max_year and listing.year and listing.year > bb.max_year:
        flags.append(f"Year {listing.year} above maximum {bb.max_year}")
    if listing.odometer and listing.odometer > bb.max_odometer:
        flags.append(f"Odometer {listing.odometer:,} over {bb.max_odometer:,}")
    if value and value < bb.min_wholesale_value:
        flags.append(f"Value ${value:,.0f} below floor ${bb.min_wholesale_value:,.0f}")
    if value and value > bb.max_wholesale_value:
        flags.append(f"Value ${value:,.0f} above ceiling ${bb.max_wholesale_value:,.0f}")

    make = listing.make.lower()
    if bb.allowed_makes and make not in [m.lower() for m in bb.allowed_makes]:
        flags.append(f"{listing.make} not in allowed makes")
    if make in [m.lower() for m in bb.excluded_makes]:
        flags.append(f"{listing.make} is excluded")

    if listing.grade is not None and listing.grade < bb.min_grade:
        flags.append(f"Grade {listing.grade:.1f} below {bb.min_grade:.1f}")
    if recon > bb.max_recon:
        flags.append(f"Recon ${recon:,.0f} over ${bb.max_recon:,.0f} cap")

    if history:
        branded = [b for b in history.title_brands if b != TitleBrand.CLEAN]
        if branded and not bb.allow_branded_title:
            flags.append(f"Branded title: {', '.join(b.value for b in branded)}")
        if history.structural_damage and not bb.allow_structural_damage:
            flags.append("Structural damage")
        if history.airbag_deployed and not bb.allow_airbag_deployed:
            flags.append("Airbag deployment")
        if history.accident_count > bb.max_accidents:
            flags.append(
                f"{history.accident_count} accidents over limit {bb.max_accidents}"
            )
        if history.odometer_rollback:
            flags.append("Odometer rollback reported")

    return flags


def _announcement_permission(announcement: str) -> str | None:
    """Which buy-box permission governs this announcement, if any."""
    low = announcement.lower()
    # Longest keyword first so "frame damage" beats "frame".
    for keyword in sorted(HARD_ANNOUNCEMENTS, key=len, reverse=True):
        if keyword in low:
            return HARD_ANNOUNCEMENTS[keyword]
    return None


def confidence_score(deal: Deal) -> float:
    """How much to trust this scoring. Missing data is the main penalty."""
    score = 1.0
    if deal.valuation is None or not deal.valuation.has_value:
        return 0.0
    if deal.valuation.source == "heuristic":
        score -= 0.45          # no real book value -- this is a guess
    if deal.history is None or deal.history.source == "none":
        score -= 0.25
    if deal.condition is None:
        score -= 0.15
    elif deal.condition.confidence < 0.6:
        score -= 0.10
    if deal.listing.grade is None:
        score -= 0.10
    if not deal.listing.condition_report_text and not deal.listing.photo_urls:
        score -= 0.10
    return max(0.0, min(1.0, score))


def score_listing(
    listing: Listing,
    valuation: Valuation | None,
    history: HistoryReport | None = None,
    condition: ConditionAssessment | None = None,
    cfg: Config | None = None,
    transport_miles: int | None = None,
    vin_decode: VinDecode | None = None,
) -> Deal:
    """Score one listing. Returns a Deal with max_bid, gross and risk flags."""
    cfg = cfg or Config()
    deal = Deal(
        listing=listing, valuation=valuation, history=history,
        condition=condition, vin_decode=vin_decode,
    )

    if valuation is None or not valuation.has_value:
        deal.risk_flags.append("No valuation available")
        deal.reasoning.append("Cannot score without a wholesale value.")
        return deal

    value, notes = adjusted_wholesale(listing, valuation, history)
    deal.reasoning.extend(notes)

    recon = recon_estimate(condition, cfg)
    transport = cfg.costs.transport_cost(transport_miles)
    fees = cfg.costs.buy_fee + cfg.costs.sell_fee
    holding = cfg.costs.holding_cost()

    # Every book value assumes the car runs. If it doesn't, price the unknown.
    reserve = estimate_reserve(listing, vin_decode)
    deal.powertrain_reserve = reserve
    deal.reasoning.extend(reserve.basis)

    deal.total_costs = recon + transport + fees + holding + reserve.high
    optimistic_costs = recon + transport + fees + holding + reserve.low

    required_gross = cfg.buy_box.required_gross(value)
    deal.max_bid = value - deal.total_costs - required_gross
    deal.max_bid_optimistic = value - optimistic_costs - required_gross

    bid = listing.current_bid or 0.0
    deal.projected_gross = value - bid - deal.total_costs

    deal.reasoning.append(
        f"Adjusted wholesale ${value:,.0f} - recon ${recon:,.0f} "
        f"- transport ${transport:,.0f} - fees ${fees:,.0f} "
        f"- holding ${holding:,.0f}"
        + (f" - powertrain reserve ${reserve.high:,.0f}" if reserve.high else "")
        + f" - target gross ${required_gross:,.0f} = max bid ${deal.max_bid:,.0f}"
    )

    # A reserve you cannot size is a bet, not a buy. Say so out loud.
    if reserve.unverified and reserve.spread > cfg.buy_box.max_risk_spread:
        deal.speculative = True
        deal.reasoning.append(
            f"UNVERIFIED FAILURE: outcome swings ${reserve.low:,.0f} to "
            f"${reserve.high:,.0f}. Max bid is ${deal.max_bid:,.0f} if it's the "
            f"expensive one, ${deal.max_bid_optimistic:,.0f} if it's the cheap "
            f"one. Diagnose before bidding, or bid the low number."
        )

    deal.risk_flags = check_buy_box(listing, value, history, recon, cfg)

    if condition:
        deal.risk_flags.extend(condition.red_flags)

    if reserve.unverified and not cfg.buy_box.allow_inoperable:
        deal.risk_flags.append("Listed INOP / not drivable")
    if reserve.high > cfg.buy_box.max_powertrain_reserve:
        deal.risk_flags.append(
            f"Powertrain exposure ${reserve.high:,.0f} over "
            f"${cfg.buy_box.max_powertrain_reserve:,.0f} cap"
        )
    if is_as_is(listing) and reserve.unverified and not cfg.buy_box.allow_as_is_inop:
        deal.risk_flags.append("AS-IS with an undiagnosed no-start: no arbitration")

    disqualifying = list(deal.risk_flags)

    # Advisory, not disqualifying on its own -- the INOP flag above already
    # gates the car. This tells you *why* the no-start is worth worrying about.
    problems = flagged_problems(valuation.common_problems)
    if problems and reserve.unverified:
        deal.risk_flags.append(
            "Known failure modes for this VIN line up with the no-start: "
            + "; ".join(problems)
        )
    for ann in listing.announcements:
        permission = _announcement_permission(ann)
        if permission is None:
            continue
        flag = f"Announcement: {ann}"
        deal.risk_flags.append(flag)
        if not getattr(cfg.buy_box, permission):
            disqualifying.append(flag)

    deal.confidence = confidence_score(deal)

    # Score: headroom relative to the gross you asked for, scaled by confidence.
    # 0 headroom -> 50. Double your target gross of headroom -> 100.
    if disqualifying:
        deal.score = 0.0
    else:
        ratio = deal.headroom / required_gross if required_gross else 0.0
        raw = 50.0 + 25.0 * ratio
        deal.score = max(0.0, min(100.0, raw)) * deal.confidence

    return deal


def rank(deals: list[Deal], min_score: float = 1.0) -> list[Deal]:
    """Best buys first. Anything disqualified or below threshold drops out."""
    keep = [d for d in deals if d.score >= min_score]
    return sorted(keep, key=lambda d: (d.score, d.headroom), reverse=True)
