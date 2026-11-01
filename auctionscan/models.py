"""Core data types.

The whole pipeline is a series of enrichments over a Listing: we start with what
the auction publishes, then bolt on a VIN decode, a valuation, a history report
and an AI read of the condition report. Each enrichment is optional -- a listing
with no Carfax is still scoreable, just at lower confidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .acquisition import Acquisition
    from .risk import PowertrainReserve


class TitleBrand(str, Enum):
    CLEAN = "clean"
    SALVAGE = "salvage"
    REBUILT = "rebuilt"
    FLOOD = "flood"
    LEMON = "lemon"
    HAIL = "hail"
    TMU = "true_mileage_unknown"
    UNKNOWN = "unknown"


@dataclass
class Listing:
    """A vehicle offered at auction, as published by the marketplace."""

    source: str                      # "manheim", "acv", "openlane", "csv"
    listing_id: str
    vin: str
    year: int | None = None
    make: str = ""
    model: str = ""
    trim: str = ""
    odometer: int | None = None

    current_bid: float | None = None
    buy_now: float | None = None
    reserve: float | None = None
    # On a timed sale the reserve is usually undisclosed. None means "we don't
    # know", which is NOT the same as met -- see acquisition.py.
    reserve_met: bool | None = None
    make_offer: bool = False
    sale_format: str = ""            # buy_now, make_offer, timed, live, absolute
    sale_date: datetime | None = None
    location: str = ""               # city/state the car physically sits in

    # Condition as the auction states it. grade is the NAAA AutoGrade 0.0-5.0
    # scale; announcements are the seller's disclosures ("frame damage",
    # "engine light on") and are the single most important free signal here.
    grade: float | None = None
    announcements: list[str] = field(default_factory=list)
    condition_report_text: str = ""
    photo_urls: list[str] = field(default_factory=list)
    seller_type: str = ""            # "off-lease", "rental", "dealer", "repo"

    # Drivability. None means the listing didn't say, which is not the same as
    # "yes" -- an undisclosed INOP is the expensive kind.
    drivable: bool | None = None
    as_is: bool = False              # no arbitration rights

    url: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def description(self) -> str:
        parts = [str(self.year or ""), self.make, self.model, self.trim]
        return " ".join(p for p in parts if p).strip()


@dataclass
class VinDecode:
    """Factory build data. Free from NHTSA vPIC -- no key, no contract."""

    vin: str
    year: int | None = None
    make: str = ""
    model: str = ""
    trim: str = ""
    body_class: str = ""
    drive_type: str = ""
    engine: str = ""
    fuel_type: str = ""
    plant_country: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.errors and bool(self.make)


@dataclass
class Valuation:
    """What the car is worth, from a book/market source.

    wholesale is the number that matters: it is what another dealer will pay.
    Retail is carried along only to sanity-check the spread.
    """

    source: str                      # "mmr", "accutrade", "heuristic"
    wholesale: float | None = None
    wholesale_low: float | None = None
    wholesale_high: float | None = None
    retail: float | None = None

    # AccuTrade returns several distinct numbers and they are not
    # interchangeable. mmr is the wholesale benchmark another dealer checks;
    # target_auction is what AccuTrade expects it to bring in the lane;
    # instant_offer is a guaranteed cash bid, so it acts as a floor -- but
    # only on a running car.
    mmr: float | None = None
    target_auction: float | None = None
    instant_offer: float | None = None
    retail_median: float | None = None
    odometer_adjustment: float | None = None
    common_problems: list[str] = field(default_factory=list)
    adjusted_for_mileage: bool = False
    adjusted_for_condition: bool = False
    as_of: datetime | None = None
    notes: str = ""

    @property
    def has_value(self) -> bool:
        return self.wholesale is not None and self.wholesale > 0


@dataclass
class HistoryReport:
    """Vehicle history -- Carfax, AutoCheck, or raw NMVTIS."""

    source: str                      # "carfax", "autocheck", "nmvtis", "none"
    accident_count: int = 0
    worst_accident_severity: str = ""    # "minor", "moderate", "severe"
    structural_damage: bool = False
    airbag_deployed: bool = False
    title_brands: list[TitleBrand] = field(default_factory=list)
    owner_count: int | None = None
    last_reported_odometer: int | None = None
    odometer_rollback: bool = False
    open_recalls: int = 0
    use_types: list[str] = field(default_factory=list)   # "fleet", "rental", "lease"
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_clean(self) -> bool:
        return (
            self.accident_count == 0
            and not self.structural_damage
            and not self.odometer_rollback
            and all(b == TitleBrand.CLEAN for b in self.title_brands)
        )


@dataclass
class DamageItem:
    panel: str
    kind: str                        # "dent", "scratch", "rust", "crack", ...
    severity: str                    # "light", "moderate", "heavy"
    estimated_cost: float = 0.0


@dataclass
class ConditionAssessment:
    """AI's structured read of the condition report + photos."""

    source: str = "claude"
    estimated_recon_cost: float = 0.0
    damage_items: list[DamageItem] = field(default_factory=list)
    tire_replacement_needed: bool = False
    mechanical_concerns: list[str] = field(default_factory=list)
    cosmetic_summary: str = ""
    red_flags: list[str] = field(default_factory=list)
    confidence: float = 0.0          # 0-1, how sure the model is
    notes: str = ""


@dataclass
class Deal:
    """A scored listing -- the output of the pipeline."""

    listing: Listing
    vin_decode: VinDecode | None = None
    valuation: Valuation | None = None
    history: HistoryReport | None = None
    condition: ConditionAssessment | None = None

    powertrain_reserve: "PowertrainReserve | None" = None

    max_bid: float = 0.0             # walk-away number (worst case priced in)
    max_bid_optimistic: float = 0.0  # if the cheap explanation is the right one
    speculative: bool = False        # the bracket is too wide to call it a buy
    exit_channel: str = "wholesale"  # which way out makes this car work
    acquisition: "Acquisition | None" = None
    projected_gross: float = 0.0     # at current bid
    total_costs: float = 0.0         # recon + transport + fees
    score: float = 0.0               # 0-100, higher is better
    risk_flags: list[str] = field(default_factory=list)
    confidence: float = 0.0
    reasoning: list[str] = field(default_factory=list)
    # The subset of risk_flags that zeroed the score: a buy-box miss, a hard
    # announcement. An empty list with score 0 means "priced over your max".
    disqualifiers: list[str] = field(default_factory=list)
    adjusted_value: float = 0.0      # wholesale after history/grade haircuts
    exit_value: float = 0.0          # what the winning exit sells it for
    required_gross: float = 0.0      # the gross that exit has to clear
    transport_cost: float = 0.0
    recon_cost: float = 0.0          # recon base, before the exit's multiplier

    @property
    def headroom(self) -> float:
        """Dollars between what you'd actually pay and your walk-away number.

        Measured against the acquisition price, not the listed bid -- on a
        reserve auction those are different numbers and only one is real.
        """
        if self.acquisition and self.acquisition.price is not None:
            return self.max_bid - self.acquisition.price
        return self.max_bid - (self.listing.current_bid or 0.0)

    @property
    def executable(self) -> bool:
        """Can you transact at a known price right now?"""
        return bool(self.acquisition and self.acquisition.executable)

    @property
    def risk_spread(self) -> float:
        """How much the answer moves between the best and worst explanation."""
        return self.max_bid_optimistic - self.max_bid
