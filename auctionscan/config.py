"""Configuration: the buy box and the cost model.

Everything a dealer would argue about lives here, not in the scoring code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class CostModel:
    """What it costs to buy a car and get it sellable.

    Defaults are deliberately conservative. Replace them with your real
    numbers -- the whole system is only as good as these.

    Sell fee and hold time live on ExitChannel, not here: they differ between
    running a car back through the lane and retailing it yourself.
    """

    buy_fee: float = 400.0               # auction buyer fee, avg across lanes
    transport_per_mile: float = 1.15
    transport_minimum: float = 150.0
    default_transport_miles: int = 250
    floorplan_daily: float = 9.0         # interest + curtailment per day
    # Recon we assume even on a "clean" car: detail, safety check, a tire or two.
    baseline_recon: float = 450.0
    # Applied to AI recon estimates -- photo-based estimates run light.
    recon_safety_multiplier: float = 1.25

    def transport_cost(self, miles: int | None = None) -> float:
        miles = self.default_transport_miles if miles is None else miles
        return max(self.transport_minimum, miles * self.transport_per_mile)


@dataclass
class Capabilities:
    """Repairs you can do in-house, and what they actually cost you.

    This is the edge, and it belongs in the model. The lane prices an INOP
    out-of-warranty Tesla as a $17,000 unknown because that is what a pack
    costs the average buyer. If you can put one in for $3,500, that $13,500
    gap is your margin, not your risk -- but only the tool that knows your
    cost can see it.

    Leave a repair as None if you can't price it. The tool will use market
    cost, keep it flagged as a real unknown, and demand
    `unpriced_powertrain_min_gross` before calling it a buy -- which is the
    "only if there's good money on the table" rule, made explicit.
    """

    ev_battery_pack: float | None = None
    ev_drive_unit: float | None = None
    engine: float | None = None
    transmission: float | None = None

    # Extra gross required when the powertrain job is one you can't price
    # up front. Higher than a normal target because you're bidding blind.
    unpriced_powertrain_min_gross: float = 4_000.0

    def cost_for(self, repair: str) -> float | None:
        return getattr(self, repair, None)


@dataclass
class ExitChannel:
    """Where the car goes when you're done with it.

    A wholesale flip and a fix-and-retail are different businesses with
    different exit prices, hold times, recon standards and gross targets.
    Scoring the same car against both is the point -- a car that's dead on
    one can be a solid buy on the other.
    """

    name: str = "wholesale"
    enabled: bool = True
    days_held: int = 21
    sell_fee: float = 300.0          # auction fee; zero if you retail it
    recon_multiplier: float = 1.0    # retail-ready costs more than lane-ready
    target_gross: float = 1_200.0
    target_gross_pct: float = 0.06

    def required_gross(self, value: float) -> float:
        return max(self.target_gross, value * self.target_gross_pct)


def default_exits() -> list[ExitChannel]:
    return [
        ExitChannel(name="wholesale", days_held=21, sell_fee=300.0,
                    recon_multiplier=1.0, target_gross=1_200.0,
                    target_gross_pct=0.06),
        ExitChannel(name="retail", enabled=False, days_held=45, sell_fee=0.0,
                    recon_multiplier=1.6, target_gross=2_500.0,
                    target_gross_pct=0.10),
    ]


@dataclass
class BuyBox:
    """What you're willing to own."""

    min_year: int | None = None
    max_year: int | None = None
    max_odometer: int = 150_000
    min_wholesale_value: float = 3_000.0
    max_wholesale_value: float = 45_000.0

    target_gross: float = 1_200.0        # minimum profit to bother
    target_gross_pct: float = 0.06       # or this % of value, whichever is higher

    allowed_makes: list[str] = field(default_factory=list)   # empty = all
    excluded_makes: list[str] = field(default_factory=list)

    # Risk tolerance
    allow_branded_title: bool = False
    allow_structural_damage: bool = False
    allow_airbag_deployed: bool = False
    max_accidents: int = 1
    min_grade: float = 3.0               # AutoGrade floor
    max_recon: float = 2_500.0

    # Drivability. An INOP car with no diagnosis is a bet on which repair it
    # needs; allow_inoperable opts into taking those bets at all.
    allow_inoperable: bool = False
    allow_as_is_inop: bool = False       # INOP *and* no arbitration rights
    max_powertrain_reserve: float = 4_000.0
    # How far the best and worst case can diverge before it stops being a buy.
    max_risk_spread: float = 3_000.0

    def required_gross(self, wholesale_value: float) -> float:
        return max(self.target_gross, wholesale_value * self.target_gross_pct)


@dataclass
class Config:
    buy_box: BuyBox = field(default_factory=BuyBox)
    costs: CostModel = field(default_factory=CostModel)
    capabilities: Capabilities = field(default_factory=Capabilities)
    exits: list[ExitChannel] = field(default_factory=default_exits)
    home_zip: str = ""
    model: str = "claude-opus-5"
    # Turn AI condition analysis off to run the pipeline for free.
    use_ai_condition: bool = True
    max_photos_per_vehicle: int = 8

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        data: dict[str, Any] = yaml.safe_load(Path(path).read_text()) or {}
        exits = data.get("exits")
        return cls(
            buy_box=BuyBox(**data.get("buy_box", {})),
            costs=CostModel(**data.get("costs", {})),
            capabilities=Capabilities(**data.get("capabilities", {})),
            exits=[ExitChannel(**e) for e in exits] if exits else default_exits(),
            home_zip=data.get("home_zip", ""),
            model=data.get("model", "claude-opus-5"),
            use_ai_condition=data.get("use_ai_condition", True),
            max_photos_per_vehicle=data.get("max_photos_per_vehicle", 8),
        )
