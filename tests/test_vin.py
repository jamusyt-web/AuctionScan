"""VIN decode tests. The network call is stubbed so these run offline."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from auctionscan.enrich import vin as vin_module
from auctionscan.enrich.vin import decode_vin, validate_vin

VALID_VIN = "1FTFW1E84MFA12345"

# Trimmed from a real vPIC DecodeVinValues response.
VPIC_PAYLOAD = {
    "Results": [{
        "ErrorText": "0 - VIN decoded clean. Check Digit (9th position) is correct",
        "Make": "FORD",
        "Model": "F-150",
        "ModelYear": "2021",
        "Series": "XLT",
        "Trim": "",
        "BodyClass": "Pickup",
        "DriveType": "4WD/4-Wheel Drive/4x4",
        "EngineCylinders": "6",
        "DisplacementL": "3.5",
        "EngineHP": "375",
        "FuelTypePrimary": "Gasoline",
        "PlantCountry": "UNITED STATES (USA)",
    }]
}


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_decode_maps_vpic_fields(monkeypatch):
    monkeypatch.setattr(
        vin_module.requests, "get", lambda *a, **k: FakeResponse(VPIC_PAYLOAD)
    )
    decoded = decode_vin(VALID_VIN)
    assert decoded.valid
    assert decoded.year == 2021
    assert decoded.make == "Ford"          # title-cased from vPIC's FORD
    assert decoded.model == "F-150"
    assert decoded.trim == "XLT"           # falls back to Series when Trim is blank
    assert decoded.body_class == "Pickup"
    assert "3.5" in decoded.engine
    assert decoded.errors == []


def test_clean_decode_produces_no_errors(monkeypatch):
    """vPIC's 'decoded clean' message must not be treated as an error."""
    monkeypatch.setattr(
        vin_module.requests, "get", lambda *a, **k: FakeResponse(VPIC_PAYLOAD)
    )
    assert decode_vin(VALID_VIN).errors == []


def test_real_error_text_is_surfaced(monkeypatch):
    payload = {"Results": [dict(VPIC_PAYLOAD["Results"][0],
                                ErrorText="6 - Incomplete VIN", Make="")]}
    monkeypatch.setattr(
        vin_module.requests, "get", lambda *a, **k: FakeResponse(payload)
    )
    decoded = decode_vin(VALID_VIN)
    assert decoded.errors == ["6 - Incomplete VIN"]
    assert not decoded.valid


def test_bad_check_digit_never_hits_the_network(monkeypatch):
    def explode(*a, **k):
        raise AssertionError("should not call vPIC for an invalid VIN")

    monkeypatch.setattr(vin_module.requests, "get", explode)
    decoded = decode_vin("1FTFW1E85MFA12345")
    assert decoded.errors == ["Invalid VIN check digit"]


def test_network_failure_is_returned_not_raised(monkeypatch):
    def boom(*a, **k):
        raise vin_module.requests.RequestException("connection reset")

    monkeypatch.setattr(vin_module.requests, "get", boom)
    decoded = decode_vin(VALID_VIN)
    assert decoded.errors and "connection reset" in decoded.errors[0]


@pytest.mark.parametrize("vin,expected", [
    ("1FTFW1E84MFA12345", True),
    ("1FTFW1E85MFA12345", False),   # check digit off by one
    ("1FTFW1E84MFA1234O", False),   # O is not a legal VIN character
    ("1FTFW1E84MFA1234", False),    # 16 chars
    ("", False),
])
def test_check_digit_cases(vin, expected):
    assert validate_vin(vin) is expected
