"""CSV / run-list import.

This is the adapter that works today with zero credentials and zero terms-of-
service risk: every auction lets you export or email yourself a run list, and
this reads it. Column names are matched loosely so you can point it at a
Manheim export, an ACV export, or a spreadsheet you keep by hand.
"""

from __future__ import annotations

import csv
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from ..models import Listing
from .base import ListingSource

# Loose header matching -- auctions all name these differently.
ALIASES = {
    "vin": ("vin", "vin number", "vehicle identification number"),
    "year": ("year", "yr", "model year"),
    "make": ("make", "manufacturer"),
    "model": ("model", "model name"),
    "trim": ("trim", "series", "style"),
    "odometer": ("odometer", "miles", "mileage", "odo"),
    "current_bid": ("current bid", "bid", "high bid", "price", "current price"),
    "buy_now": ("buy now", "buy it now", "bin", "buy now price"),
    "grade": ("grade", "condition grade", "autograde", "cr score"),
    "announcements": ("announcements", "disclosures", "lights", "notes"),
    "location": ("location", "city", "auction location", "yard"),
    "sale_date": ("sale date", "run date", "auction date", "date"),
    "listing_id": ("listing id", "lot", "lot number", "stock", "id", "run number"),
    "condition_report_text": ("condition report", "cr", "cr text", "description"),
    "photo_urls": ("photos", "photo urls", "images", "image urls"),
    "seller_type": ("seller type", "seller", "consignor type"),
    "url": ("url", "link", "listing url"),
}


def _normalize(header: str) -> str:
    return header.strip().lower().replace("_", " ")


def _build_map(fieldnames: list[str]) -> dict[str, str]:
    """Map our field names to the CSV's actual column names."""
    found: dict[str, str] = {}
    normalized = {_normalize(f): f for f in fieldnames}
    for field, options in ALIASES.items():
        for opt in options:
            if opt in normalized:
                found[field] = normalized[opt]
                break
    return found


def _to_int(value: str | None) -> int | None:
    if not value:
        return None
    cleaned = "".join(c for c in value if c.isdigit())
    return int(cleaned) if cleaned else None


def _to_float(value: str | None) -> float | None:
    if not value:
        return None
    cleaned = "".join(c for c in value if c.isdigit() or c == ".")
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


def _to_date(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue
    return None


class CsvSource(ListingSource):
    name = "csv"

    def __init__(self, path: str | Path, source_label: str = "csv"):
        self.path = Path(path)
        self.source_label = source_label

    def fetch(self) -> Iterator[Listing]:
        with self.path.open(newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            if not reader.fieldnames:
                return
            cols = _build_map(list(reader.fieldnames))
            if "vin" not in cols:
                raise ValueError(
                    f"{self.path}: no VIN column found. "
                    f"Columns present: {', '.join(reader.fieldnames)}"
                )
            for i, row in enumerate(reader):
                vin = (row.get(cols["vin"]) or "").strip().upper()
                if not vin:
                    continue

                def get(field: str) -> str | None:
                    col = cols.get(field)
                    return row.get(col) if col else None

                announcements = [
                    a.strip() for a in (get("announcements") or "").split(";") if a.strip()
                ]
                photos = [
                    p.strip() for p in (get("photo_urls") or "").split(";") if p.strip()
                ]

                yield Listing(
                    source=self.source_label,
                    listing_id=(get("listing_id") or f"row-{i + 2}").strip(),
                    vin=vin,
                    year=_to_int(get("year")),
                    make=(get("make") or "").strip(),
                    model=(get("model") or "").strip(),
                    trim=(get("trim") or "").strip(),
                    odometer=_to_int(get("odometer")),
                    current_bid=_to_float(get("current_bid")),
                    buy_now=_to_float(get("buy_now")),
                    sale_date=_to_date(get("sale_date")),
                    location=(get("location") or "").strip(),
                    grade=_to_float(get("grade")),
                    announcements=announcements,
                    condition_report_text=(get("condition_report_text") or "").strip(),
                    photo_urls=photos,
                    seller_type=(get("seller_type") or "").strip(),
                    url=(get("url") or "").strip(),
                    raw=dict(row),
                )
