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
    """

    buy_fee: float = 400.0               # auction buyer fee, avg across lanes
    sell_fee: float = 300.0              # if you re-run it through the lane
    transport_per_mile: float = 1.15
    transport_minimum: float = 150.0
    default_transport_miles: int = 250
    floorplan_daily: float = 9.0         # interest + curtailment per day
    expected_days_held: int = 21
    # Recon we assume even on a "clean" car: detail, safety check, a tire or two.
    baseline_recon: float = 450.0
    # Applied to AI recon estimates -- photo-based estimates run light.
    recon_safety_multiplier: float = 1.25

    def transport_cost(self, miles: int | None = None) -> float:
        miles = self.default_transport_miles if miles is None else miles
        return max(self.transport_minimum, miles * self.transport_per_mile)

    def holding_cost(self) -> float:
        return self.floorplan_daily * self.expected_days_held


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
    home_zip: str = ""
    model: str = "claude-opus-5"
    # Turn AI condition analysis off to run the pipeline for free.
    use_ai_condition: bool = True
    max_photos_per_vehicle: int = 8

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        data: dict[str, Any] = yaml.safe_load(Path(path).read_text()) or {}
        return cls(
            buy_box=BuyBox(**data.get("buy_box", {})),
            costs=CostModel(**data.get("costs", {})),
            home_zip=data.get("home_zip", ""),
            model=data.get("model", "claude-opus-5"),
            use_ai_condition=data.get("use_ai_condition", True),
            max_photos_per_vehicle=data.get("max_photos_per_vehicle", 8),
        )
