"""Valuation providers.

Wholesale value is the load-bearing input to the whole system: get it wrong and
every downstream number is wrong. Three providers:

  MmrValuation        Manheim Market Report -- the actual wholesale benchmark.
                      Requires MMR Valuations API access via the Cox Automotive
                      API Storefront. This is the one you want.
  AccuTradeValuation  AccuTrade (Cars Commerce) Perseus API. VIN + mileage +
                      condition in, valuation out. Requires a partner API key.
  HeuristicValuation  No API. A crude depreciation curve so the pipeline runs
                      end to end before you have credentials. NOT for real bids
                      -- it marks itself low-confidence and the scorer halves
                      the score accordingly.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

import requests

from ..models import Listing, Valuation


class ValuationProvider(ABC):
    name = "base"

    @abstractmethod
    def value(self, listing: Listing) -> Valuation:
        raise NotImplementedError


class MmrValuation(ValuationProvider):
    """Manheim Market Report.

    MMR is transaction-derived: it is what cars like this actually sold for in
    the lane in the recent past, which is exactly the question you're asking.
    Set MANHEIM_CLIENT_ID / MANHEIM_CLIENT_SECRET.
    """

    name = "mmr"
    BASE = "https://api.manheim.com"

    def __init__(self) -> None:
        from ..sources.api_adapters import ApiNotConfigured, OAuth2Client

        cid = os.getenv("MANHEIM_CLIENT_ID")
        secret = os.getenv("MANHEIM_CLIENT_SECRET")
        if not (cid and secret):
            raise ApiNotConfigured(
                "MMR needs MANHEIM_CLIENT_ID / MANHEIM_CLIENT_SECRET."
            )
        self.client = OAuth2Client(f"{self.BASE}/oauth2/token", cid, secret)

    def value(self, listing: Listing) -> Valuation:
        params: dict[str, Any] = {}
        if listing.odometer:
            params["odometer"] = listing.odometer
        try:
            data = self.client.get(f"{self.BASE}/valuations/vin/{listing.vin}", **params)
        except requests.RequestException as exc:
            return Valuation(source=self.name, notes=f"MMR request failed: {exc}")

        # CONFIRM against a live payload; the portal is behind a login.
        adjusted = data.get("adjustedPricing") or data.get("pricing") or {}
        wholesale = adjusted.get("wholesale", {}) or {}
        return Valuation(
            source=self.name,
            wholesale=wholesale.get("average"),
            wholesale_low=wholesale.get("below"),
            wholesale_high=wholesale.get("above"),
            adjusted_for_mileage=bool(params.get("odometer")),
            adjusted_for_condition=False,
            as_of=datetime.now(timezone.utc),
        )


class AccuTradeValuation(ValuationProvider):
    """AccuTrade (Cars Commerce) -- the one integration that covers everything.

    If you already run AccuTrade with MMR and Carfax bundled in, this is the
    only valuation integration you need: one call returns the Manheim MMR, the
    target auction number, the instant offer, Cars.com median retail, the
    odometer adjustment and the known failure modes for the VIN. That replaces
    three separate contracts.

    The VIN-dow browser extension shows exactly this data on the auction page.
    The Perseus API is the sanctioned way to get the same payload
    programmatically -- ask your Cars Commerce rep for a key rather than
    automating the extension.

    Set ACCUTRADE_API_KEY. Docs: https://docs.accu-trade.com
    """

    name = "accutrade"
    BASE = "https://api.accu-trade.com"

    def __init__(self) -> None:
        key = os.getenv("ACCUTRADE_API_KEY")
        if not key:
            from ..sources.api_adapters import ApiNotConfigured

            raise ApiNotConfigured(
                "Set ACCUTRADE_API_KEY. See https://docs.accu-trade.com"
            )
        self.key = key

    def value(self, listing: Listing) -> Valuation:
        payload = {
            "vin": listing.vin,
            "mileage": listing.odometer,
            # AccuTrade takes a condition input; map AutoGrade onto it if we
            # have one, otherwise let it default.
            "condition": self._condition_from_grade(listing.grade),
        }
        try:
            resp = requests.post(
                f"{self.BASE}/v1/valuation",
                headers={"Authorization": f"Bearer {self.key}"},
                json={k: v for k, v in payload.items() if v is not None},
                timeout=45,
            )
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as exc:
            return Valuation(source=self.name, notes=f"AccuTrade request failed: {exc}")

        # CONFIRM key names against the live spec; the panel labels they map to
        # are the ones shown in the VIN-dow sidebar.
        mmr = data.get("manheim") or data.get("mmr")                 # "Manheim"
        target = data.get("targetAuction")                           # "Target Auction"
        instant = data.get("instantOffer")                           # "Instant Offer"
        retail_median = data.get("medianRetail")                     # "Cars.com Median Retail"

        return Valuation(
            source=self.name,
            # MMR is what the dealer you are flipping to will look up, so it is
            # the benchmark. Fall back to the target auction number.
            wholesale=mmr or target,
            wholesale_low=data.get("lowerEstimate") or instant,
            wholesale_high=data.get("upperEstimate") or target,
            retail=retail_median or data.get("retail"),
            mmr=mmr,
            target_auction=target,
            instant_offer=instant,
            retail_median=retail_median,
            odometer_adjustment=data.get("odometerAdjustment"),
            common_problems=data.get("commonProblems", []) or [],
            adjusted_for_mileage=listing.odometer is not None,
            adjusted_for_condition=listing.grade is not None,
            as_of=datetime.now(timezone.utc),
        )

    @staticmethod
    def _condition_from_grade(grade: float | None) -> str | None:
        if grade is None:
            return None
        if grade >= 4.5:
            return "excellent"
        if grade >= 3.5:
            return "good"
        if grade >= 2.5:
            return "fair"
        return "poor"


# Rough new-vehicle transaction prices by segment, used only by the heuristic.
SEGMENT_BASE = {
    "luxury": 64_000.0,
    "truck": 58_000.0,
    "suv": 44_000.0,
    "midsize": 33_000.0,
    "compact": 27_000.0,
}
LUXURY_MAKES = {
    "bmw", "mercedes-benz", "mercedes", "audi", "lexus", "porsche", "jaguar",
    "land rover", "cadillac", "genesis", "infiniti", "acura", "volvo", "tesla",
}
TRUCK_HINTS = ("f-150", "f150", "silverado", "sierra", "ram", "tundra", "tacoma",
               "colorado", "ranger", "frontier", "titan", "canyon", "ridgeline")
SUV_HINTS = ("explorer", "tahoe", "suburban", "pilot", "highlander", "rav4",
             "cr-v", "crv", "equinox", "escape", "rogue", "grand cherokee",
             "4runner", "telluride", "palisade", "atlas", "traverse")


class HeuristicValuation(ValuationProvider):
    """Offline fallback. Depreciation curve, not a market value.

    Present so you can run and tune the whole pipeline before paying for data.
    Every value it produces is flagged low-confidence; do not bid off it.
    """

    name = "heuristic"

    def __init__(self, current_year: int = 2026):
        self.current_year = current_year

    def value(self, listing: Listing) -> Valuation:
        if not listing.year:
            return Valuation(source=self.name, notes="No model year; cannot estimate")

        base = self._segment_base(listing)
        age = max(0, self.current_year - listing.year)
        # Depreciation is front-loaded: roughly 20% off the lot, then ~11%/yr
        # declining balance. A flat rate badly under-values 4-6 year old cars,
        # which is most of what runs through a wholesale lane.
        residual = 1.0 if age == 0 else max(0.12, 0.80 * 0.89 ** (age - 1))
        retail = base * residual

        # Mileage adjustment against a 12k/yr expectation.
        if listing.odometer:
            expected = max(1, age) * 12_000
            delta = (expected - listing.odometer) * 0.045
            retail += max(-retail * 0.25, min(retail * 0.25, delta))

        # Wholesale runs under retail asking by roughly the dealer's margin.
        wholesale = retail * 0.85

        return Valuation(
            source=self.name,
            wholesale=round(max(500.0, wholesale), -1),
            wholesale_low=round(max(500.0, wholesale * 0.9), -1),
            wholesale_high=round(max(500.0, wholesale * 1.1), -1),
            retail=round(max(700.0, retail), -1),
            adjusted_for_mileage=True,
            adjusted_for_condition=False,
            as_of=datetime.now(timezone.utc),
            notes="ESTIMATE ONLY - no market data source. Do not bid off this.",
        )

    @staticmethod
    def _segment_base(listing: Listing) -> float:
        make = listing.make.lower()
        model = listing.model.lower()
        if make in LUXURY_MAKES:
            return SEGMENT_BASE["luxury"]
        if any(h in model for h in TRUCK_HINTS):
            return SEGMENT_BASE["truck"]
        if any(h in model for h in SUV_HINTS):
            return SEGMENT_BASE["suv"]
        if listing.year and listing.year >= 2018:
            return SEGMENT_BASE["midsize"]
        return SEGMENT_BASE["compact"]


class CsvValuation(ValuationProvider):
    """Values keyed in by hand from the AccuTrade VIN-dow panel.

    The pragmatic option, and the one to start with. If you already have
    AccuTrade with MMR and Carfax bundled, you can read those numbers off the
    auction page today and drop them in a spreadsheet column -- no API key, no
    contract, no terms-of-service question, and the scoring is identical to
    what it will be once Perseus is wired up. Swap in AccuTradeValuation later
    and nothing downstream changes.

    Reads MMR / Target Auction / Instant Offer / Median Retail / Common
    Problems from the same CSV row as the listing.
    """

    name = "csv"

    def value(self, listing: Listing) -> Valuation:
        row = listing.raw if isinstance(listing.raw, dict) else {}
        lookup = {str(k).strip().lower(): v for k, v in row.items()}

        def num(*names: str) -> float | None:
            for name in names:
                raw = lookup.get(name)
                if raw:
                    cleaned = "".join(c for c in str(raw) if c.isdigit() or c == ".")
                    if cleaned:
                        try:
                            return float(cleaned)
                        except ValueError:
                            continue
            return None

        mmr = num("mmr", "manheim", "manheim mmr")
        target = num("target auction", "target", "target wholesale")
        instant = num("instant offer", "ico", "instant cash offer")
        # Two different retail numbers. "Retail" is the mileage-adjusted one
        # the marketplace shows; "Median Retail" is a book median for the
        # model with no odometer adjustment at all. Only the first is a
        # defensible exit price -- using a median on a 144k-mile car
        # overstates the exit by thousands.
        retail_adj = num("retail", "adjusted retail", "clear retail")
        retail_median = num("median retail", "retail median",
                            "cars.com median retail")
        problems_raw = str(
            lookup.get("common problems") or lookup.get("known problems") or ""
        )
        problems = [p.strip() for p in problems_raw.replace("|", ";").split(";") if p.strip()]

        if not (mmr or target):
            return Valuation(
                source=self.name,
                notes="No MMR or target auction value in the row",
            )

        return Valuation(
            source="accutrade",   # same provenance, keyed in by hand
            wholesale=mmr or target,
            wholesale_low=instant,
            wholesale_high=target,
            retail=retail_adj,
            mmr=mmr,
            target_auction=target,
            instant_offer=instant,
            retail_median=retail_median,
            common_problems=problems,
            notes=(
                "" if retail_adj else
                "No mileage-adjusted retail value; retail exit not scored."
            ),
            # AccuTrade applies its own odometer adjustment before it shows you
            # these numbers, so don't apply ours on top.
            adjusted_for_mileage=True,
            adjusted_for_condition=False,
            as_of=datetime.now(timezone.utc),
        )
