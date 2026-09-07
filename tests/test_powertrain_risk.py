"""Drivability and powertrain risk.

The headline case is a real listing: a 2021 Tesla Model Y Performance at
143,991 miles, graded 3.1, announced AS-IS and INOP, where ADESA's own panel
showed a projected profit of $8,096 and a personalized max bid of $17,546.
Every book value on the page prices a car that runs. This one doesn't.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from auctionscan.config import (
    BuyBox, Capabilities, Config, CostModel, ExitChannel,
)
from auctionscan.models import Listing, Valuation, VinDecode
from auctionscan.risk import (
    estimate_reserve, is_as_is, is_inoperable, powertrain_warranty,
)
from auctionscan.scoring import score_listing


def tesla() -> Listing:
    return Listing(
        source="adesa",
        listing_id="be5be7b6",
        vin="5YJYGDEF2MF252410",
        year=2021,
        make="Tesla",
        model="Model Y",
        trim="Performance",
        odometer=143_991,
        current_bid=11_500,
        grade=3.1,
        as_is=True,
        drivable=False,
        announcements=["Refer to CR for Disclosures"],
        condition_report_text=(
            "Mechanical: Not Drivable - Mechanical, Unverified Items. "
            "Exterior: Glass Issue, Cosmetic Issue, Wheels & Tires Issue. "
            "Interior: Mild Other Odor."
        ),
        seller_type="CARVANA Wholesale",
    )


def tesla_valuation() -> Valuation:
    """The numbers as AccuTrade showed them."""
    return Valuation(
        source="accutrade",
        wholesale=15_750,          # Manheim MMR
        mmr=15_750,
        target_auction=15_925,
        instant_offer=14_425,
        retail=21_179,             # mileage-adjusted, as the marketplace showed it
        retail_median=28_470,      # book median, NOT odometer-adjusted
        odometer_adjustment=-10_025,
        common_problems=[
            "Infotainment Screen Inop", "Damage", "Charger Missing",
            "Battery Replacement", "Electric Motor(s) Wear/Failure",
        ],
        adjusted_for_mileage=True,
        adjusted_for_condition=False,
    )


def dealer_config() -> Config:
    return Config(
        buy_box=BuyBox(target_gross=1_200, target_gross_pct=0.06, min_grade=3.0,
                       max_odometer=150_000),
        costs=CostModel(),
        exits=[ExitChannel(name="wholesale", days_held=21, sell_fee=300,
                           target_gross=1_200, target_gross_pct=0.06)],
    )


def test_tesla_battery_warranty_is_expired_on_mileage():
    """8 years OR 120,000 miles, whichever comes first. This one is past."""
    covered, why = powertrain_warranty(tesla())
    assert covered is False
    assert "120,000" in why and "143,991" in why


def test_inop_and_as_is_are_detected_from_the_condition_report():
    listing = tesla()
    assert is_inoperable(listing)
    assert is_as_is(listing)


def test_out_of_warranty_ev_reserve_brackets_the_pack():
    reserve = estimate_reserve(tesla())
    assert reserve.unverified
    assert reserve.is_ev
    assert not reserve.in_warranty
    assert reserve.low <= 500              # dead 12V or a missing charge cable
    assert reserve.high >= 15_000          # a traction pack
    assert reserve.spread > 14_000


def test_the_tesla_does_not_clear_the_buy_box():
    """ADESA said max bid $17,546. The reserve says otherwise."""
    deal = score_listing(tesla(), tesla_valuation(), cfg=dealer_config())
    assert deal.score == 0.0
    assert deal.speculative
    assert any("INOP" in f for f in deal.risk_flags)
    # Worst case, the walk-away number is below the $11,500 opening bid.
    assert deal.max_bid < 11_500


def test_known_failure_modes_are_connected_to_the_no_start():
    deal = score_listing(tesla(), tesla_valuation(), cfg=dealer_config())
    flag = next(f for f in deal.risk_flags if "failure modes" in f)
    assert "Battery Replacement" in flag
    assert "Electric Motor(s) Wear/Failure" in flag


def test_the_bracket_is_reported_both_ways():
    """The spread between explanations is the size of the bet."""
    deal = score_listing(tesla(), tesla_valuation(), cfg=dealer_config())
    assert deal.max_bid_optimistic > deal.max_bid
    assert deal.risk_spread > 14_000


def test_a_running_version_of_the_same_car_scores_fine():
    """Isolate the INOP: same car, same miles, but it drives."""
    listing = tesla()
    listing.drivable = True
    listing.as_is = False
    listing.condition_report_text = "Exterior: Cosmetic Issue, Wheels & Tires Issue."
    deal = score_listing(listing, tesla_valuation(), cfg=dealer_config())
    assert deal.score > 0.0
    assert not deal.speculative
    assert deal.max_bid > 11_500


def test_in_warranty_ev_limits_the_exposure():
    """Same failure, but under 120k miles: the pack is Tesla's problem."""
    listing = tesla()
    listing.odometer = 88_000
    reserve = estimate_reserve(listing)
    assert reserve.in_warranty
    assert reserve.high < 2_000


def test_ice_car_uses_engine_transmission_exposure_not_a_pack():
    listing = Listing(
        source="test", listing_id="L9", vin="1FTFW1E84MFA12345",
        year=2019, make="Ford", model="F-150", odometer=140_000,
        current_bid=9_000, grade=3.0, drivable=False,
    )
    reserve = estimate_reserve(listing, VinDecode(vin=listing.vin, fuel_type="Gasoline"))
    assert not reserve.is_ev
    assert 3_000 < reserve.high < 12_000


def test_allow_inoperable_opts_into_the_bet():
    cfg = dealer_config()
    cfg.buy_box.allow_inoperable = True
    cfg.buy_box.allow_as_is_inop = True
    cfg.buy_box.max_powertrain_reserve = 20_000
    cfg.buy_box.max_risk_spread = 20_000
    deal = score_listing(tesla(), tesla_valuation(), cfg=cfg)
    # It can now be scored, but the worst-case max bid is still under the bid.
    assert not deal.speculative
    assert deal.max_bid < 11_500
    assert deal.score == 0.0 or deal.headroom < 0


# ---------------------------------------------------------------------------
# Capability arbitrage: the same car, priced by a shop that can do the work.
# ---------------------------------------------------------------------------

def tesla_shop_config() -> Config:
    """A dealer who does his own packs at $3,500 and retails what he fixes."""
    cfg = dealer_config()
    cfg.buy_box.max_odometer = 160_000
    cfg.buy_box.allow_inoperable = True
    cfg.buy_box.allow_as_is_inop = True
    cfg.buy_box.max_powertrain_reserve = 25_000
    cfg.capabilities = Capabilities(ev_battery_pack=3_500, ev_drive_unit=3_500)
    cfg.exits = [
        ExitChannel(name="wholesale", days_held=21, sell_fee=300,
                    target_gross=1_200, target_gross_pct=0.06),
        ExitChannel(name="retail", days_held=45, sell_fee=0,
                    recon_multiplier=1.6, target_gross=2_500,
                    target_gross_pct=0.10),
    ]
    return cfg


def test_in_house_repair_cost_replaces_market_cost():
    reserve = estimate_reserve(
        tesla(), capabilities=Capabilities(ev_battery_pack=3_500, ev_drive_unit=3_500)
    )
    assert reserve.in_house
    assert reserve.unpriced == []
    assert reserve.high == 3_500          # not the $17,000 market pack


def test_unpriced_repair_keeps_market_cost_and_says_so():
    """Priced the pack but not the drive unit: the drive unit still binds."""
    reserve = estimate_reserve(
        tesla(), capabilities=Capabilities(ev_battery_pack=3_500)
    )
    assert reserve.in_house
    assert reserve.unpriced == ["drive unit"]
    assert reserve.high == 7_000
    assert any("NOT priced" in b for b in reserve.basis)


def test_capability_turns_the_tesla_into_a_buy():
    """The car the market walks from is the one a shop with a lift buys."""
    deal = score_listing(tesla(), tesla_valuation(), cfg=tesla_shop_config())
    assert deal.exit_channel == "retail"       # the flip doesn't clear; the fix does
    assert deal.max_bid > 11_500               # room over the opening bid
    assert not deal.speculative                # priced work is not a gamble
    assert deal.score > 0


def test_wholesale_flip_alone_does_not_clear_even_at_his_cost():
    """Isolate the exit: same capability, wholesale only."""
    cfg = tesla_shop_config()
    cfg.exits = [e for e in cfg.exits if e.name == "wholesale"]
    deal = score_listing(tesla(), tesla_valuation(), cfg=cfg)
    assert deal.exit_channel == "wholesale"
    assert deal.max_bid < 11_500


def test_unpriced_powertrain_demands_more_gross():
    """'Only if there's a good amount of money on the table', made explicit."""
    cfg = tesla_shop_config()
    cfg.capabilities = Capabilities(unpriced_powertrain_min_gross=6_000)
    priced = tesla_shop_config()
    assert score_listing(tesla(), tesla_valuation(), cfg=priced).max_bid > \
        score_listing(tesla(), tesla_valuation(), cfg=cfg).max_bid
