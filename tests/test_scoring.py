"""Tests for the money math. These are the numbers that matter."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from auctionscan.config import BuyBox, Config, CostModel, ExitChannel
from auctionscan.enrich.vin import validate_vin
from auctionscan.models import (
    ConditionAssessment, HistoryReport, Listing, TitleBrand, Valuation,
)
from auctionscan.scoring import adjusted_wholesale, rank, score_listing


def make_listing(**kw) -> Listing:
    base = dict(
        source="test", listing_id="L1", vin="1FTFW1E84MFA12345",
        year=2021, make="Ford", model="F-150", odometer=60_000,
        current_bid=20_000, grade=4.0,
    )
    base.update(kw)
    return Listing(**base)


def make_config() -> Config:
    return Config(
        buy_box=BuyBox(target_gross=1_000, target_gross_pct=0.0, min_grade=3.0),
        costs=CostModel(
            buy_fee=400, transport_minimum=200, default_transport_miles=100,
            transport_per_mile=1.0, floorplan_daily=10, baseline_recon=500,
        ),
        exits=[ExitChannel(name="wholesale", days_held=20, sell_fee=300,
                           recon_multiplier=1.0, target_gross=1_000,
                           target_gross_pct=0.0)],
    )


def test_max_bid_subtracts_every_cost():
    cfg = make_config()
    listing = make_listing(grade=None, odometer=None)
    val = Valuation(source="mmr", wholesale=25_000,
                    adjusted_for_mileage=True, adjusted_for_condition=True)
    deal = score_listing(listing, val, cfg=cfg)
    # 25000 - recon 500 - transport 200 - fees 700 - holding 200 - gross 1000
    assert deal.total_costs == 500 + 200 + 700 + 200
    assert deal.max_bid == 25_000 - 1_600 - 1_000


def test_headroom_and_gross_track_the_bid():
    cfg = make_config()
    listing = make_listing(current_bid=21_000, grade=None, odometer=None)
    val = Valuation(source="mmr", wholesale=25_000,
                    adjusted_for_mileage=True, adjusted_for_condition=True)
    deal = score_listing(listing, val, cfg=cfg)
    assert deal.headroom == deal.max_bid - 21_000
    assert deal.projected_gross == 25_000 - 21_000 - deal.total_costs


def test_structural_damage_disqualifies():
    cfg = make_config()
    val = Valuation(source="mmr", wholesale=25_000)
    history = HistoryReport(source="carfax", structural_damage=True)
    deal = score_listing(make_listing(), val, history=history, cfg=cfg)
    assert deal.score == 0.0
    assert any("Structural" in f for f in deal.risk_flags)


def test_branded_title_takes_a_haircut_and_disqualifies():
    cfg = make_config()
    listing = make_listing(grade=None, odometer=None)
    val = Valuation(source="mmr", wholesale=20_000,
                    adjusted_for_mileage=True, adjusted_for_condition=True)
    history = HistoryReport(source="carfax", title_brands=[TitleBrand.SALVAGE])
    value, notes = adjusted_wholesale(listing, val, history)
    assert value == 20_000 * 0.5           # 50% salvage haircut
    assert any("salvage" in n for n in notes)

    deal = score_listing(listing, val, history=history, cfg=cfg)
    assert deal.score == 0.0               # buy box rejects branded titles


def test_accidents_reduce_value_progressively():
    listing = make_listing(grade=None, odometer=None)
    val = Valuation(source="mmr", wholesale=20_000,
                    adjusted_for_mileage=True, adjusted_for_condition=True)
    one, _ = adjusted_wholesale(
        listing, val,
        HistoryReport(source="carfax", accident_count=1,
                      worst_accident_severity="moderate"),
    )
    two, _ = adjusted_wholesale(
        listing, val,
        HistoryReport(source="carfax", accident_count=2,
                      worst_accident_severity="moderate"),
    )
    assert one == 20_000 - 20_000 * 0.08
    assert two < one


def test_low_miles_raise_value_high_miles_lower_it():
    val = Valuation(source="mmr", wholesale=20_000)
    low, _ = adjusted_wholesale(make_listing(odometer=30_000, grade=None), val)
    high, _ = adjusted_wholesale(make_listing(odometer=110_000, grade=None), val)
    assert low > 20_000 > high


def test_recon_safety_multiplier_is_applied():
    cfg = make_config()
    cfg.costs.recon_safety_multiplier = 1.5
    cfg.buy_box.max_recon = 10_000
    listing = make_listing(grade=None, odometer=None)
    val = Valuation(source="mmr", wholesale=25_000,
                    adjusted_for_mileage=True, adjusted_for_condition=True)
    condition = ConditionAssessment(estimated_recon_cost=2_000, confidence=0.8)
    deal = score_listing(listing, val, condition=condition, cfg=cfg)
    assert deal.total_costs == 3_000 + 200 + 700 + 200      # 2000 * 1.5


def test_heuristic_valuation_lowers_confidence():
    cfg = make_config()
    listing = make_listing()
    real = score_listing(listing, Valuation(source="mmr", wholesale=25_000), cfg=cfg)
    guess = score_listing(
        listing, Valuation(source="heuristic", wholesale=25_000), cfg=cfg
    )
    assert guess.confidence < real.confidence
    assert guess.score < real.score


def test_no_valuation_is_not_scoreable():
    deal = score_listing(make_listing(), None, cfg=make_config())
    assert deal.score == 0.0
    assert "No valuation available" in deal.risk_flags


def test_rank_drops_disqualified_and_sorts_by_score():
    cfg = make_config()
    good = score_listing(
        make_listing(current_bid=15_000),
        Valuation(source="mmr", wholesale=25_000), cfg=cfg,
    )
    tight = score_listing(
        make_listing(current_bid=22_000),
        Valuation(source="mmr", wholesale=25_000), cfg=cfg,
    )
    bad = score_listing(
        make_listing(),
        Valuation(source="mmr", wholesale=25_000),
        history=HistoryReport(source="carfax", structural_damage=True), cfg=cfg,
    )
    ranked = rank([tight, bad, good])
    assert bad not in ranked
    assert ranked[0] is good


def test_vin_check_digit_validation():
    assert validate_vin("1FTFW1E84MFA12345")
    assert not validate_vin("1FTFW1E85MFA12345")   # wrong check digit
    assert not validate_vin("TOOSHORT")
    assert not validate_vin("1FTFW1E84MFA1234I")   # I is not a legal VIN char


def test_announcement_disqualifies_without_a_history_report():
    """Frame damage announced in the lane counts even with no Carfax."""
    cfg = make_config()
    cfg.buy_box.allow_structural_damage = False
    val = Valuation(source="mmr", wholesale=25_000)
    listing = make_listing(announcements=["Frame damage"])
    deal = score_listing(listing, val, cfg=cfg)
    assert deal.score == 0.0
    assert any("Frame damage" in f for f in deal.risk_flags)


def test_airbag_announcement_respects_the_buy_box():
    cfg = make_config()
    val = Valuation(source="mmr", wholesale=25_000)
    listing = make_listing(current_bid=15_000, announcements=["Airbag deployed prior"])

    cfg.buy_box.allow_airbag_deployed = False
    assert score_listing(listing, val, cfg=cfg).score == 0.0

    cfg.buy_box.allow_airbag_deployed = True
    permitted = score_listing(listing, val, cfg=cfg)
    assert permitted.score > 0.0
    # Still surfaced as a risk, just not a disqualifier.
    assert any("Airbag" in f for f in permitted.risk_flags)


def test_benign_announcement_is_not_a_disqualifier():
    cfg = make_config()
    val = Valuation(source="mmr", wholesale=25_000)
    listing = make_listing(current_bid=15_000, announcements=["Check engine light on"])
    assert score_listing(listing, val, cfg=cfg).score > 0.0
