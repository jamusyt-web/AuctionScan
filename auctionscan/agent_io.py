"""Score a car the hourly agent has read. JSON in, JSON out.

The agent (Claude, working in your Chrome) reads a listing the way a buyer
does -- the condition report and photos, the Carfax/AutoCheck box, the
AccuTrade VIN-dow numbers, the transport quote -- writes what it found as
JSON, and runs:

    python -m auctionscan score cars.json --config config.jamus.yaml

That puts the facts through the same engine as everything else and returns a
verdict. The math lives here rather than in the agent's head so the same car
gets the same number every time, and the numbers can be tested.

Input is one car object, a list of them, or {"cars": [...]}:

    {
      "url": "...", "source": "acv", "vin": "...",
      "year": 2021, "make": "Tesla", "model": "Model 3", "trim": "Long Range",
      "odometer": 52000, "location": "Rochester, NY",
      "sale_format": "buy_now",                 # or "make_offer"
      "buy_now": 15000, "reserve": null, "make_offer": false, "current_bid": null,
      "grade": 3.6, "announcements": [], "drivable": true, "as_is": false,
      "condition_report_text": "...", "photo_count": 34,
      "transport_quote": 310,                   # dollars shown on the page, or
      "transport_miles": 90,                    # driving miles to Liverpool, NY
      "valuation": {"mmr": 24000, "target_auction": 23500, "instant_offer": 21000,
                    "retail": 29900, "retail_median": 31000,
                    "common_problems": [], "adjusted_for_condition": false},
      "history": {"source": "autocheck", "accident_count": 0,
                  "worst_accident_severity": "", "structural_damage": false,
                  "airbag_deployed": false, "title_brands": ["clean"],
                  "owner_count": 1, "odometer_rollback": false},
      "condition": {"estimated_recon_cost": 1100, "confidence": 0.7,
                    "damage_items": [{"panel": "rear bumper", "kind": "scratch",
                                      "severity": "moderate", "estimated_cost": 250}],
                    "tire_replacement_needed": true, "mechanical_concerns": [],
                    "cosmetic_summary": "...", "red_flags": []}
    }
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .acquisition import SaleFormat
from .config import Config
from .models import (
    ConditionAssessment, DamageItem, Deal, HistoryReport, Listing, TitleBrand, Valuation,
)
from .scoring import score_listing

# The formats Jamus buys. A timed auction's bid isn't a price.
DEFAULT_FORMATS = ("buy_now", "make_offer")

# "Buy" means act now: a price you can take, data you can trust.
BUY_MIN_CONFIDENCE = 0.6
BUY_MIN_CERTAINTY = 0.8
# A make-offer car whose published reserve is just over your max is still
# worth an offer AT your max -- sellers take under reserve all the time.
OFFER_GAP = 1_000.0

_BRANDS = {b.value: b for b in TitleBrand}
_BRAND_ALIASES = {
    "tmu": TitleBrand.TMU, "true mileage unknown": TitleBrand.TMU,
    "not actual mileage": TitleBrand.TMU, "buyback": TitleBrand.LEMON,
    "lemon law": TitleBrand.LEMON, "water": TitleBrand.FLOOD,
}


def _num(v: Any) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    cleaned = "".join(c for c in str(v) if c.isdigit() or c == ".")
    return float(cleaned) if cleaned else None


def _int(v: Any) -> int | None:
    n = _num(v)
    return int(round(n)) if n is not None else None


def _bool(v: Any) -> bool | None:
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in {"1", "true", "yes", "y"}


def _list(v: Any) -> list[str]:
    if not v:
        return []
    if isinstance(v, str):
        return [p.strip() for p in v.replace("|", ";").split(";") if p.strip()]
    return [str(p).strip() for p in v if str(p).strip()]


def _brand(v: str) -> TitleBrand:
    key = str(v).strip().lower().replace("-", " ")
    return _BRANDS.get(key.replace(" ", "_")) or _BRAND_ALIASES.get(key) or TitleBrand.UNKNOWN


def listing_from(d: dict[str, Any]) -> Listing:
    url = d.get("url") or ""
    return Listing(
        source=str(d.get("source") or "agent"),
        listing_id=str(d.get("id") or url or d.get("vin") or ""),
        vin=str(d.get("vin") or "").strip().upper(),
        year=_int(d.get("year")),
        make=str(d.get("make") or ""),
        model=str(d.get("model") or ""),
        trim=str(d.get("trim") or ""),
        odometer=_int(d.get("odometer")),
        current_bid=_num(d.get("current_bid")),
        buy_now=_num(d.get("buy_now")),
        reserve=_num(d.get("reserve")),
        reserve_met=_bool(d.get("reserve_met")),
        make_offer=bool(_bool(d.get("make_offer"))),
        sale_format=str(d.get("sale_format") or ""),
        location=str(d.get("location") or ""),
        grade=_num(d.get("grade")),
        announcements=_list(d.get("announcements")),
        condition_report_text=str(d.get("condition_report_text") or ""),
        # The engine only checks whether photos exist; the agent looks at them.
        photo_urls=["photo"] * (_int(d.get("photo_count")) or 0),
        seller_type=str(d.get("seller_type") or ""),
        drivable=_bool(d.get("drivable")),
        as_is=bool(_bool(d.get("as_is"))),
        url=url,
        raw={"Mechanical": str(d.get("mechanical") or "")},
    )


def valuation_from(v: dict[str, Any] | None, listing: Listing) -> Valuation | None:
    """AccuTrade numbers off the VIN-dow panel. Same rules as CsvValuation."""
    if not v:
        return None
    mmr = _num(v.get("mmr"))
    target = _num(v.get("target_auction"))
    wholesale = _num(v.get("wholesale")) or mmr or target
    if not wholesale:
        return Valuation(source="accutrade", notes="No MMR or target auction value")
    retail = _num(v.get("retail"))   # mileage-adjusted only; median is not an exit
    return Valuation(
        source=str(v.get("source") or "accutrade"),
        wholesale=wholesale,
        wholesale_low=_num(v.get("instant_offer")),
        wholesale_high=target,
        retail=retail,
        mmr=mmr,
        target_auction=target,
        instant_offer=_num(v.get("instant_offer")),
        retail_median=_num(v.get("retail_median")),
        common_problems=_list(v.get("common_problems")),
        # AccuTrade applies its own odometer adjustment before showing these.
        adjusted_for_mileage=bool(_bool(v.get("adjusted_for_mileage")) is not False),
        # Only true when the number shown already reflects grade/condition --
        # otherwise the grade adjustment is applied here, once.
        adjusted_for_condition=bool(_bool(v.get("adjusted_for_condition"))),
        notes="" if retail else "No mileage-adjusted retail value; retail exit not scored.",
    )


def history_from(h: dict[str, Any] | None) -> HistoryReport | None:
    if not h:
        return None
    return HistoryReport(
        source=str(h.get("source") or "autocheck"),
        accident_count=_int(h.get("accident_count")) or 0,
        worst_accident_severity=str(h.get("worst_accident_severity") or "").lower(),
        structural_damage=bool(_bool(h.get("structural_damage"))),
        airbag_deployed=bool(_bool(h.get("airbag_deployed"))),
        title_brands=[_brand(b) for b in _list(h.get("title_brands"))],
        owner_count=_int(h.get("owner_count")),
        odometer_rollback=bool(_bool(h.get("odometer_rollback"))),
        use_types=_list(h.get("use_types")),
    )


def condition_from(c: dict[str, Any] | None) -> ConditionAssessment | None:
    if not c:
        return None
    items = [
        DamageItem(
            panel=str(i.get("panel") or ""), kind=str(i.get("kind") or ""),
            severity=str(i.get("severity") or ""),
            estimated_cost=_num(i.get("estimated_cost")) or 0.0,
        )
        for i in c.get("damage_items") or []
    ]
    total = _num(c.get("estimated_recon_cost"))
    if total is None:
        total = sum(i.estimated_cost for i in items)
    return ConditionAssessment(
        source=str(c.get("source") or "claude"),
        estimated_recon_cost=total,
        damage_items=items,
        tire_replacement_needed=bool(_bool(c.get("tire_replacement_needed"))),
        mechanical_concerns=_list(c.get("mechanical_concerns")),
        cosmetic_summary=str(c.get("cosmetic_summary") or ""),
        red_flags=_list(c.get("red_flags")),
        confidence=_num(c.get("confidence")) or 0.0,
    )


def verdict(deal: Deal, formats: tuple[str, ...] = DEFAULT_FORMATS) -> tuple[str, str]:
    """buy / look / pass, and the one line that says what to do."""
    acq = deal.acquisition
    if deal.valuation is None or not deal.valuation.has_value or acq is None:
        return "pass", "No AccuTrade value read -- can't price it"
    if deal.disqualifiers:
        return "pass", f"Pass -- {deal.disqualifiers[0]}"
    if formats and acq.format.value not in formats:
        return "pass", f"Pass -- {acq.format.value.replace('_', ' ')} isn't a format you buy"

    if acq.executable and acq.price is not None and deal.headroom >= 0:
        trusted = (deal.confidence >= BUY_MIN_CONFIDENCE
                   and acq.certainty >= BUY_MIN_CERTAINTY
                   and not deal.speculative)
        return ("buy" if trusted else "look"), acq.action

    gap = (acq.price or 0) - deal.max_bid
    if (acq.format == SaleFormat.MAKE_OFFER and acq.price is not None
            and deal.max_bid > 0 and 0 < gap <= OFFER_GAP):
        return "look", (f"OFFER ${deal.max_bid:,.0f} (your max) -- their reserve is "
                        f"${acq.price:,.0f}, ${gap:,.0f} over")
    return "pass", acq.action or "Over your max"


_VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")


def doc_id(listing: Listing) -> str:
    """Stable key for the finds dashboard: the VIN, else the listing number.

    Uses only characters the dashboard store accepts in a path segment.
    """
    if _VIN_RE.match(listing.vin or ""):
        return listing.vin
    source = re.sub(r"[^A-Za-z0-9_-]", "", listing.source or "") or "car"
    numbers = re.findall(r"\d{5,}", listing.url or "")
    if numbers:
        return f"{source}-{numbers[-1]}"
    seed = (listing.url or listing.listing_id or "").encode()
    return f"{source}-{hashlib.sha1(seed).hexdigest()[:16]}"


def _money(n: float | None) -> str:
    return "?" if n is None else f"${n:,.0f}"


_HISTORY_NAMES = {"autocheck": "AutoCheck", "carfax": "Carfax", "nmvtis": "NMVTIS"}


def deal_to_dict(deal: Deal, car: dict[str, Any], cfg: Config,
                 formats: tuple[str, ...] = DEFAULT_FORMATS) -> dict[str, Any]:
    listing, acq, val = deal.listing, deal.acquisition, deal.valuation
    kind, action = verdict(deal, formats)
    exit_ch = next((e for e in cfg.exits if e.name == deal.exit_channel), None)
    cond = deal.condition

    out: dict[str, Any] = {
        "doc_id": doc_id(listing),
        "id": listing.listing_id,
        "url": listing.url or None,
        "vin": listing.vin or None,
        "title": listing.description or listing.vin or "Vehicle",
        "source": listing.source,
        "location": listing.location or None,
        "odometer": listing.odometer,
        "grade": listing.grade,
        "verdict": kind,
        "action": action,
        "format": acq.format.value if acq else None,
        "price": acq.price if acq else None,
        "max_bid": round(deal.max_bid),
        "max_bid_optimistic": round(deal.max_bid_optimistic),
        "headroom": round(deal.headroom) if acq and acq.price is not None else None,
        "exit": deal.exit_channel,
        "score": round(deal.score, 1),
        "confidence": round(deal.confidence, 2),
        "certainty": round(acq.certainty, 2) if acq else None,
        "speculative": deal.speculative,
        "costs": {
            "recon": round(deal.recon_cost * (exit_ch.recon_multiplier if exit_ch else 1.0)),
            "transport": round(deal.transport_cost),
            "fees": round(cfg.costs.buy_fee + (exit_ch.sell_fee if exit_ch else 0.0)),
            "holding": round(cfg.costs.floorplan_daily * (exit_ch.days_held if exit_ch else 0)),
            "required_gross": round(deal.required_gross),
            "powertrain_reserve": round(deal.powertrain_reserve.high)
            if deal.powertrain_reserve else 0,
        },
        "book": {
            "source": val.source if val else None,
            "mmr": val.mmr if val else None,
            "target_auction": val.target_auction if val else None,
            "instant_offer": val.instant_offer if val else None,
            "retail": val.retail if val else None,
            "wholesale_used": val.wholesale if val else None,
            "adjusted_value": round(deal.adjusted_value),
            "exit_value": round(deal.exit_value),
        },
        "condition": None if cond is None else {
            "summary": cond.cosmetic_summary,
            "recon_estimate": round(cond.estimated_recon_cost),
            "items": [
                {"panel": i.panel, "kind": i.kind, "severity": i.severity,
                 "cost": round(i.estimated_cost)} for i in cond.damage_items
            ],
            "tires": cond.tire_replacement_needed,
            "mechanical": cond.mechanical_concerns,
            "confidence": cond.confidence,
        },
        "history": None if deal.history is None else {
            "source": deal.history.source,
            "accidents": deal.history.accident_count,
            "worst": deal.history.worst_accident_severity or None,
            "structural": deal.history.structural_damage,
            "title": [b.value for b in deal.history.title_brands],
            "owners": deal.history.owner_count,
        },
        "flags": deal.risk_flags,
        "reasoning": deal.reasoning,
    }
    out["message"] = phone_message(out)
    return out


def phone_message(d: dict[str, Any]) -> str:
    """Three or four short lines that read well as a phone notification."""
    fmt = (d.get("format") or "").replace("_", " ").title()
    src = (d.get("source") or "").upper()
    miles = f"{d['odometer']:,} mi" if d.get("odometer") else ""
    head = " · ".join(p for p in [d["title"], miles, f"{src} {fmt}".strip()] if p)

    cond = d.get("condition") or {}
    hist = d.get("history") or {}
    book = d.get("book") or {}
    bits = [f"recon ~{_money(d['costs']['recon'])}"]
    items = [f"{i['panel']} {i['kind']}".strip() for i in (cond.get("items") or [])[:3]]
    if cond.get("tires"):
        items.append("tires")
    if items:
        bits[0] += f" ({', '.join(items)})"
    if hist:
        acc = hist.get("accidents") or 0
        name = _HISTORY_NAMES.get(str(hist.get("source") or "").lower(), "History")
        bits.append(f"{name}: "
                    + ("clean" if not acc else f"{acc} accident{'s' if acc > 1 else ''}"))
    reserve = (d.get("costs") or {}).get("powertrain_reserve") or 0
    if reserve:
        bits.append(f"INOP reserve {_money(reserve)}")
    if book.get("mmr"):
        bits.append(f"MMR {_money(book['mmr'])}")
    elif book.get("target_auction"):
        bits.append(f"target auction {_money(book['target_auction'])}")

    lines = [head, d["action"], f"Max {_money(d['max_bid'])} ({d['exit']} exit) · "
             + " · ".join(bits)]
    warnings = list(d.get("flags") or [])
    if d.get("speculative"):
        warnings.insert(0, f"Speculative: best case max {_money(d.get('max_bid_optimistic'))}")
    if warnings:
        lines.append("! " + "; ".join(warnings[:2]))
    if d.get("url"):
        lines.append(d["url"])
    return "\n".join(lines)


def score_car(car: dict[str, Any], cfg: Config,
              formats: tuple[str, ...] = DEFAULT_FORMATS) -> dict[str, Any]:
    listing = listing_from(car)
    deal = score_listing(
        listing,
        valuation_from(car.get("valuation"), listing),
        history=history_from(car.get("history")),
        condition=condition_from(car.get("condition")),
        cfg=cfg,
        transport_miles=_int(car.get("transport_miles")),
        transport_cost=_num(car.get("transport_quote")),
    )
    return deal_to_dict(deal, car, cfg, formats)


def load_cars(path: str | Path) -> list[dict[str, Any]]:
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if isinstance(data, dict) and "cars" in data:
        data = data["cars"]
    return data if isinstance(data, list) else [data]
