"""Drivability and powertrain risk.

The gap this fills: every valuation you can buy -- MMR, AccuTrade target
auction, ADESA CLEAR -- prices a vehicle that RUNS. None of them price an INOP
car, because none of them know why it doesn't run.

That matters most on an out-of-warranty EV, where the spread between "dead 12V
battery" and "dead traction pack" is five figures on a car worth five figures.
A cosmetic recon estimate is worthless there. What you need is a reserve: the
worst case you could be buying, priced against the best case, so you can see
whether you are looking at a deal or a lottery ticket.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .models import Listing, VinDecode

if TYPE_CHECKING:
    from .config import Capabilities

# Signals that the car does not drive under its own power.
INOP_PATTERNS = (
    "inop", "not drivable", "non-drivable", "nondrivable", "won't start",
    "wont start", "no start", "does not run", "doesn't run", "non-runner",
    "no crank", "will not start", "not running", "dead battery", "no power",
)
# Signals that you have no arbitration recourse if it's worse than advertised.
AS_IS_PATTERNS = ("as-is", "as is", "no arbitration", "no arb", "red light")

# Battery + drive unit warranty: 8 years OR these miles, whichever comes FIRST.
# The mileage limit is what expires first on anything that reaches a wholesale
# lane, so that is what we check.
EV_WARRANTY_MILES = {
    ("tesla", "model s"): 150_000,
    ("tesla", "model x"): 150_000,
    ("tesla", "model 3"): 120_000,   # Long Range / Performance; RWD is 100k
    ("tesla", "model y"): 120_000,   # Long Range / Performance; RWD is 100k
}
EV_WARRANTY_YEARS = 8
EV_WARRANTY_MILES_DEFAULT = 100_000   # federal minimum for EV traction packs

# Out-of-warranty traction pack replacement, installed. (low = refurb/salvage
# pack from an independent, high = OEM through the dealer network.)
EV_PACK_COST = {
    ("tesla", "model y"): (10_000, 17_000),
    ("tesla", "model 3"): (9_000, 16_000),
    ("tesla", "model s"): (13_000, 22_000),
    ("tesla", "model x"): (13_000, 22_000),
}
EV_PACK_COST_DEFAULT = (8_000, 20_000)
EV_DRIVE_UNIT_COST = (2_500, 7_000)

# Non-EV worst case: engine or transmission replacement.
ICE_POWERTRAIN_COST = (3_500, 9_000)
ICE_POWERTRAIN_WARRANTY_MILES = 60_000

# The cheap end of "why won't it start": 12V battery, missing charge cable,
# a blown fuse, a car that just sat on the lot too long.
TRIVIAL_FIX = 400.0

EV_FUEL_HINTS = ("electric", "bev", "electric fuel")
EV_MAKE_HINTS = ("tesla", "rivian", "lucid", "polestar")


@dataclass
class PowertrainReserve:
    """What the unknown could cost you, bracketed.

    low is the benign explanation, high is the expensive one. When the car is
    INOP and nobody has diagnosed it, both are live and the spread between them
    is the size of the bet you are being asked to make.
    """

    low: float = 0.0
    high: float = 0.0
    unverified: bool = False
    in_warranty: bool = False
    is_ev: bool = False
    in_house: bool = False           # you can do this repair at a known price
    unpriced: list[str] = field(default_factory=list)   # jobs you can't price
    basis: list[str] = field(default_factory=list)

    @property
    def spread(self) -> float:
        return self.high - self.low


def _haystack(listing: Listing) -> str:
    parts = [
        " ".join(listing.announcements),
        listing.condition_report_text,
        listing.raw.get("Mechanical", "") if isinstance(listing.raw, dict) else "",
    ]
    return " ".join(str(p) for p in parts).lower()


def is_inoperable(listing: Listing) -> bool:
    """True when the listing says the car does not drive."""
    if listing.drivable is False:
        return True
    text = _haystack(listing)
    # "inop" as a word, not inside another word.
    if re.search(r"\binop\b", text):
        return True
    return any(p in text for p in INOP_PATTERNS if p != "inop")


def is_as_is(listing: Listing) -> bool:
    """True when the sale carries no arbitration rights."""
    if listing.as_is:
        return True
    return any(p in _haystack(listing) for p in AS_IS_PATTERNS)


def is_ev(listing: Listing, decode: VinDecode | None = None) -> bool:
    fuel = (decode.fuel_type if decode else "").lower()
    if any(h in fuel for h in EV_FUEL_HINTS):
        return True
    if listing.make.lower() in EV_MAKE_HINTS:
        return True
    blob = f"{listing.trim} {listing.raw.get('Fuel', '')}".lower()
    return "electric" in blob


def _key(listing: Listing) -> tuple[str, str]:
    make = listing.make.lower().strip()
    model = listing.model.lower().strip()
    # "Model Y Performance" -> ("tesla", "model y")
    for (m, mo) in EV_WARRANTY_MILES:
        if make == m and model.startswith(mo):
            return (m, mo)
    return (make, model)


def powertrain_warranty(
    listing: Listing, decode: VinDecode | None = None, current_year: int = 2026
) -> tuple[bool, str]:
    """Is the powertrain still covered? Returns (covered, explanation)."""
    if listing.odometer is None:
        return False, "Odometer unknown; assume out of warranty"

    electric = is_ev(listing, decode)
    if electric:
        limit = EV_WARRANTY_MILES.get(_key(listing), EV_WARRANTY_MILES_DEFAULT)
        term = EV_WARRANTY_YEARS
        label = "battery/drive unit"
    else:
        limit = ICE_POWERTRAIN_WARRANTY_MILES
        term = 5
        label = "powertrain"

    age = current_year - listing.year if listing.year else 99
    if listing.odometer >= limit:
        return False, (
            f"{label.title()} warranty expired: {listing.odometer:,} mi vs "
            f"{limit:,} mi limit (past by {listing.odometer - limit:,})"
        )
    if age >= term:
        return False, f"{label.title()} warranty expired on age ({age} yrs vs {term})"
    return True, (
        f"{label.title()} likely still covered ({listing.odometer:,} mi / "
        f"{limit:,}, {age} yrs / {term})"
    )


def estimate_reserve(
    listing: Listing,
    decode: VinDecode | None = None,
    current_year: int = 2026,
    capabilities: "Capabilities | None" = None,
) -> PowertrainReserve:
    """Bracket the cost of whatever is wrong with an INOP car.

    When `capabilities` prices the repair, the worst case becomes YOUR cost
    rather than the market's -- which is the whole reason a shop that can do
    the work sees a deal where everyone else sees a landmine.
    """
    reserve = PowertrainReserve()
    if not is_inoperable(listing):
        return reserve

    reserve.unverified = True
    reserve.is_ev = is_ev(listing, decode)
    covered, why = powertrain_warranty(listing, decode, current_year)
    reserve.in_warranty = covered
    reserve.basis.append(why)
    reserve.basis.append("Listed INOP / not drivable with no diagnosis")

    if covered:
        # A covered failure costs you time and transport, not a new pack.
        reserve.low = TRIVIAL_FIX
        reserve.high = 1_500.0
        reserve.basis.append("Failure may be warranty-covered; exposure limited")
        return reserve

    reserve.low = TRIVIAL_FIX

    # Each candidate failure: (capability name, market worst case, label).
    if reserve.is_ev:
        pack = EV_PACK_COST.get(_key(listing), EV_PACK_COST_DEFAULT)
        candidates = [
            ("ev_battery_pack", pack[1], "traction pack"),
            ("ev_drive_unit", EV_DRIVE_UNIT_COST[1], "drive unit"),
        ]
    else:
        candidates = [
            ("engine", ICE_POWERTRAIN_COST[1], "engine"),
            ("transmission", ICE_POWERTRAIN_COST[1], "transmission"),
        ]

    # The binding number is the worst SINGLE job, not the sum -- these are
    # alternative explanations for one no-start, not a list of known failures.
    worst = 0.0
    for name, market, label in candidates:
        own = capabilities.cost_for(name) if capabilities else None
        if own is not None:
            reserve.in_house = True
            reserve.basis.append(f"{label.title()} in-house at ${own:,.0f}")
            worst = max(worst, own)
        else:
            reserve.unpriced.append(label)
            reserve.basis.append(
                f"{label.title()} NOT priced -- using market ${market:,.0f}. "
                f"Set capabilities.{name} to your real cost."
            )
            worst = max(worst, market)
    reserve.high = worst

    if is_as_is(listing):
        reserve.basis.append("Sold AS-IS: no arbitration if it's the expensive one")

    return reserve


def flagged_problems(valuation_problems: list[str]) -> list[str]:
    """Pull the powertrain-relevant items out of a 'common problems' list.

    AccuTrade returns known failure modes per VIN. When one of them is a
    traction pack or a drive motor and the car in front of you won't move,
    that is not a coincidence worth ignoring.
    """
    serious = ("battery replacement", "motor", "drive unit", "transmission",
               "engine", "inverter", "charger")
    return [p for p in valuation_problems if any(s in p.lower() for s in serious)]
