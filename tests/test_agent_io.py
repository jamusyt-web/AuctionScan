"""The hourly agent's scoring path: JSON facts in, a verdict out."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from auctionscan.agent_io import score_car  # noqa: E402
from auctionscan.cli import main  # noqa: E402
from auctionscan.config import Config  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CFG = Config.load(ROOT / "config.jamus.yaml")


def car(**kw):
    base = {
        "url": "https://app.acvauctions.com/auction/1234567",
        "source": "acv", "vin": "5YJ3E1EB5MF000001",
        "year": 2021, "make": "Tesla", "model": "Model 3", "trim": "Long Range",
        "odometer": 52000, "location": "Rochester, NY",
        "sale_format": "buy_now", "buy_now": 17000,
        "grade": 3.6, "drivable": True, "photo_count": 34,
        "condition_report_text": "Light scratches rear bumper. Tires 5/32.",
        "transport_quote": 210,
        "valuation": {"mmr": 24000, "target_auction": 23500, "instant_offer": 21500},
        "history": {"source": "autocheck", "accident_count": 0, "title_brands": ["clean"]},
        "condition": {
            "estimated_recon_cost": 800, "confidence": 0.75,
            "damage_items": [{"panel": "rear bumper", "kind": "scratch",
                              "severity": "light", "estimated_cost": 200}],
            "tire_replacement_needed": False,
            "cosmetic_summary": "Clean car, light rear bumper scuffs.",
        },
    }
    base.update(kw)
    return base


def test_clean_buy_now_under_max_is_a_buy():
    r = score_car(car(), CFG)
    assert r["verdict"] == "buy", r["action"]
    assert r["action"].startswith("BUY NOW at $17,000")
    assert r["headroom"] > 0
    # The agent's own recon read is used, padded by the safety multiplier.
    assert r["costs"]["recon"] == 1000
    # A transport quote from the page beats the per-mile guess.
    assert r["costs"]["transport"] == 210
    assert r["confidence"] >= 0.6


def test_condition_moves_the_max_bid_dollar_for_dollar():
    light = score_car(car(), CFG)
    rough = score_car(car(condition={"estimated_recon_cost": 3000, "confidence": 0.7}), CFG)
    # 2,200 more recon x 1.25 safety = 2,750 off the max.
    assert light["max_bid"] - rough["max_bid"] == 2750


def test_recon_heavy_car_at_the_same_price_is_a_pass():
    r = score_car(car(buy_now=20500,
                      condition={"estimated_recon_cost": 3200, "confidence": 0.7}), CFG)
    assert r["verdict"] == "pass"
    assert r["action"].startswith("Pass")


def test_structural_damage_in_history_disqualifies_at_any_price():
    r = score_car(car(buy_now=9000,
                      history={"source": "carfax", "accident_count": 1,
                               "worst_accident_severity": "severe",
                               "structural_damage": True}), CFG)
    assert r["verdict"] == "pass"
    assert "Structural damage" in r["action"]


def test_hard_announcement_disqualifies():
    r = score_car(car(announcements=["Frame damage"]), CFG)
    assert r["verdict"] == "pass"


def test_transport_miles_used_when_no_quote():
    r = score_car(car(transport_quote=None, transport_miles=500), CFG)
    assert r["costs"]["transport"] == 575    # 500 mi x $1.15


def test_make_offer_reserve_just_over_max_says_offer_your_max():
    probe = score_car(car(), CFG)
    over = probe["max_bid"] + 600
    r = score_car(car(sale_format="make_offer", buy_now=None, make_offer=True,
                      reserve=over), CFG)
    assert r["verdict"] == "look"
    assert r["action"].startswith(f"OFFER ${probe['max_bid']:,}")


def test_published_reserve_under_max_is_a_buy_with_offer_action():
    r = score_car(car(sale_format="make_offer", buy_now=None, make_offer=True,
                      reserve=16000), CFG)
    assert r["verdict"] == "buy"
    assert r["action"].startswith("OFFER $16,000")


def test_timed_auction_is_passed_even_when_cheap():
    r = score_car(car(sale_format="timed", buy_now=None, current_bid=8000), CFG)
    assert r["verdict"] == "pass"


def test_no_book_value_is_a_pass_not_a_guess():
    r = score_car(car(valuation=None), CFG)
    assert r["verdict"] == "pass"
    assert "AccuTrade" in r["action"]


def test_inop_tesla_uses_the_in_house_pack_cost():
    r = score_car(car(drivable=False, announcements=["INOP"], buy_now=9000,
                      odometer=131000, year=2020), CFG)
    assert any("in-house" in line.lower() or "your cost" in line.lower()
               for line in r["reasoning"]), r["reasoning"]
    assert r["verdict"] in {"buy", "look", "pass"}


def test_phone_message_is_short_and_has_the_link():
    r = score_car(car(), CFG)
    lines = r["message"].splitlines()
    assert 3 <= len(lines) <= 5
    assert "2021 Tesla Model 3 Long Range" in lines[0]
    assert lines[-1] == car()["url"]
    assert "MMR $24,000" in r["message"]


def test_cli_scores_a_file(tmp_path, capsys):
    src = tmp_path / "cars.json"
    src.write_text(json.dumps({"cars": [car(), car(vin="BAD", valuation=None)]}))
    out = tmp_path / "out.json"
    assert main(["score", str(src), "--config", str(ROOT / "config.jamus.yaml"),
                 "--out", str(out)]) == 0
    results = json.loads(out.read_text())
    assert [r["verdict"] for r in results] == ["buy", "pass"]
    assert "Scored 2 car(s)" in capsys.readouterr().err


def test_doc_id_is_the_vin_or_the_listing_number():
    assert score_car(car(), CFG)["doc_id"] == "5YJ3E1EB5MF000001"
    r = score_car(car(vin="", url="https://www.openlane.com/details/2021-ford/48213377"), CFG)
    assert r["doc_id"] == "acv-48213377"
    r = score_car(car(vin="", url="", id="x y z"), CFG)
    assert r["doc_id"].startswith("acv-") and " " not in r["doc_id"]
