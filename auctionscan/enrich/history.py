"""Vehicle history providers.

Carfax does not sell a self-serve API. Access requires a Service Data Transfer
Facilitation Agreement negotiated with their business development team, and the
keys are not publicly subscribable. Two consequences:

  1. Automating your dealer Carfax login is not a supported path and is what
     puts an account at risk. Don't.
  2. For a first-pass screen, raw NMVTIS is the cost-effective answer -- the
     federal title/brand/theft/total-loss database, resold by authorized
     providers for roughly $1-3 a VIN versus Carfax's dealer report pricing.
     Screen on NMVTIS, then pull the full Carfax only on the short list you're
     actually going to bid on.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Any

import requests

from ..models import HistoryReport, TitleBrand

BRAND_KEYWORDS = {
    "salvage": TitleBrand.SALVAGE,
    "rebuilt": TitleBrand.REBUILT,
    "reconstructed": TitleBrand.REBUILT,
    "flood": TitleBrand.FLOOD,
    "water damage": TitleBrand.FLOOD,
    "lemon": TitleBrand.LEMON,
    "manufacturer buyback": TitleBrand.LEMON,
    "hail": TitleBrand.HAIL,
    "not actual mileage": TitleBrand.TMU,
    "true mileage unknown": TitleBrand.TMU,
    "odometer discrepancy": TitleBrand.TMU,
}


def parse_brands(values: list[str]) -> list[TitleBrand]:
    """Map free-text brand strings onto our enum."""
    found: list[TitleBrand] = []
    for value in values:
        low = value.lower()
        for keyword, brand in BRAND_KEYWORDS.items():
            if keyword in low and brand not in found:
                found.append(brand)
    return found or [TitleBrand.CLEAN]


class HistoryProvider(ABC):
    name = "base"

    @abstractmethod
    def lookup(self, vin: str) -> HistoryReport:
        raise NotImplementedError


class NullHistory(HistoryProvider):
    """No history data. Scored at reduced confidence rather than skipped."""

    name = "none"

    def lookup(self, vin: str) -> HistoryReport:
        return HistoryReport(source="none")


class NmvtisHistory(HistoryProvider):
    """NMVTIS title/brand data via an authorized provider.

    Cheap enough to run on every car in a run list. Set NMVTIS_API_KEY and
    NMVTIS_API_URL for whichever authorized provider you contract with -- the
    underlying federal data is identical across them.
    """

    name = "nmvtis"

    def __init__(self) -> None:
        self.key = os.getenv("NMVTIS_API_KEY")
        self.url = os.getenv("NMVTIS_API_URL")
        if not (self.key and self.url):
            raise RuntimeError("Set NMVTIS_API_KEY and NMVTIS_API_URL.")

    def lookup(self, vin: str) -> HistoryReport:
        try:
            resp = requests.get(
                self.url,
                params={"vin": vin, "key": self.key},
                timeout=45,
            )
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
        except (requests.RequestException, ValueError):
            return HistoryReport(source="none")

        brands = parse_brands(data.get("titleBrands", []) or [])
        return HistoryReport(
            source=self.name,
            title_brands=brands,
            last_reported_odometer=data.get("lastOdometer"),
            odometer_rollback=bool(data.get("odometerRollback")),
            # NMVTIS carries total-loss records but not a full accident history.
            accident_count=1 if data.get("totalLoss") else 0,
            worst_accident_severity="severe" if data.get("totalLoss") else "",
            raw=data,
        )


class CarfaxHistory(HistoryProvider):
    """Carfax via a negotiated data agreement.

    Set CARFAX_API_URL and CARFAX_API_KEY once Carfax provisions them for you.
    Reserve this for the short list -- it is the expensive lookup.
    """

    name = "carfax"

    def __init__(self) -> None:
        self.url = os.getenv("CARFAX_API_URL")
        self.key = os.getenv("CARFAX_API_KEY")
        if not (self.url and self.key):
            raise RuntimeError(
                "Set CARFAX_API_URL and CARFAX_API_KEY. Carfax has no self-serve "
                "API -- contact their business development team for an agreement."
            )

    def lookup(self, vin: str) -> HistoryReport:
        try:
            resp = requests.get(
                f"{self.url.rstrip('/')}/{vin}",
                headers={"Authorization": f"Bearer {self.key}"},
                timeout=60,
            )
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
        except (requests.RequestException, ValueError):
            return HistoryReport(source="none")

        # CONFIRM field names against the spec Carfax issues with your agreement.
        accidents = data.get("accidents", []) or []
        severities = [a.get("severity", "").lower() for a in accidents]
        worst = ""
        for level in ("severe", "moderate", "minor"):
            if level in severities:
                worst = level
                break

        return HistoryReport(
            source=self.name,
            accident_count=len(accidents),
            worst_accident_severity=worst,
            structural_damage=bool(data.get("structuralDamage")),
            airbag_deployed=bool(data.get("airbagDeployed")),
            title_brands=parse_brands(data.get("titleBrands", []) or []),
            owner_count=data.get("ownerCount"),
            last_reported_odometer=data.get("lastReportedOdometer"),
            odometer_rollback=bool(data.get("odometerRollback")),
            open_recalls=data.get("openRecalls", 0) or 0,
            use_types=data.get("useTypes", []) or [],
            raw=data,
        )
