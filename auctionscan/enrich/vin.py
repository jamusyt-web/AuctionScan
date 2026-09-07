"""VIN decode via NHTSA vPIC.

Free, public, no key, no contract, no rate limit worth worrying about. Use it
to confirm the auction's year/make/model actually match the VIN -- run lists
have typos, and a mismatch between the stated trim and the decoded trim is one
of the cheapest ways to catch a mispriced car.
"""

from __future__ import annotations

import requests

from ..models import VinDecode

VPIC_URL = "https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValues/{vin}?format=json"

TRANSLITERATION = {
    **{str(d): d for d in range(10)},
    "A": 1, "B": 2, "C": 3, "D": 4, "E": 5, "F": 6, "G": 7, "H": 8,
    "J": 1, "K": 2, "L": 3, "M": 4, "N": 5, "P": 7, "R": 9,
    "S": 2, "T": 3, "U": 4, "V": 5, "W": 6, "X": 7, "Y": 8, "Z": 9,
}
WEIGHTS = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]


def validate_vin(vin: str) -> bool:
    """Check the ISO 3779 check digit. Catches transcription errors offline."""
    vin = vin.strip().upper()
    if len(vin) != 17 or any(c in "IOQ" for c in vin):
        return False
    try:
        total = sum(TRANSLITERATION[c] * w for c, w in zip(vin, WEIGHTS))
    except KeyError:
        return False
    remainder = total % 11
    expected = "X" if remainder == 10 else str(remainder)
    return vin[8] == expected


def decode_vin(vin: str, timeout: int = 20) -> VinDecode:
    """Decode a VIN. Returns a VinDecode with .errors populated on failure."""
    vin = vin.strip().upper()
    if not validate_vin(vin):
        return VinDecode(vin=vin, errors=["Invalid VIN check digit"])

    try:
        resp = requests.get(VPIC_URL.format(vin=vin), timeout=timeout)
        resp.raise_for_status()
        results = resp.json().get("Results") or []
    except (requests.RequestException, ValueError) as exc:
        return VinDecode(vin=vin, errors=[f"vPIC request failed: {exc}"])

    if not results:
        return VinDecode(vin=vin, errors=["vPIC returned no results"])

    r = results[0]
    year = r.get("ModelYear")
    errors = []
    error_text = (r.get("ErrorText") or "").strip()
    # vPIC uses code "0" for a clean decode; anything else is worth surfacing.
    if error_text and not error_text.startswith("0 - VIN decoded clean"):
        errors.append(error_text)

    return VinDecode(
        vin=vin,
        year=int(year) if year and str(year).isdigit() else None,
        make=(r.get("Make") or "").title(),
        model=r.get("Model") or "",
        trim=r.get("Trim") or r.get("Series") or "",
        body_class=r.get("BodyClass") or "",
        drive_type=r.get("DriveType") or "",
        engine=" ".join(
            filter(None, [r.get("EngineCylinders"), r.get("DisplacementL"), r.get("EngineHP")])
        ),
        fuel_type=r.get("FuelTypePrimary") or "",
        plant_country=r.get("PlantCountry") or "",
        errors=errors,
    )
