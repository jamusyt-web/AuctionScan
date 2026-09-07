"""How you buy the car, and whether the spread is real.

The scenario driving all of this: a timed auction showing a low bid with zero
bidders looks like the best deal on the page, but the undisclosed reserve sits
thousands above it. Buy Now and Make Offer give you a number you can actually
transact at. The scoring has to tell those apart.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from auctionscan.acquisition import SaleFormat, detect_format, plan
from auctionscan.config import BuyBox, Config, CostModel, ExitChannel
from auctionscan.models import Listing, Valuation
from auctionscan.scoring import rank, score_listing


def listing(**kw) -> Listing:
    base = dict(
        source="adesa", listing_id="L1", vin="1FTFW1E84MFA12345",
        year=2021, make="Ford", model="F-150", odometer=60_000,
        current_bid=18_000, grade=4.0,
    )
    base.update(kw)
    return Listing(**base)


def cfg() -> Config:
    return Config(
        buy_box=BuyBox(target_gross=1_000, target_gross_pct=0.0, min_grade=3.0),
        costs=CostModel(buy_fee=400, transport_minimum=200,
                        default_transport_miles=100, transport_per_mile=1.0,
                        floorplan_daily=10, baseline_recon=500),
        exits=[ExitChannel(name="wholesale", days_held=20, sell_fee=300,
                           target_gross=1_000, target_gross_pct=0.0)],
    )


def val(wholesale: float = 25_000) -> Valuation:
    return Valuation(source="accutrade", wholesale=wholesale, mmr=wholesale,
                     adjusted_for_mileage=True, adjusted_for_condition=True)


# --- format detection ------------------------------------------------------

def test_buy_now_price_implies_buy_now_format():
    assert detect_format(listing(buy_now=21_000)) is SaleFormat.BUY_NOW


def test_make_offer_flag_is_detected():
    assert detect_format(listing(make_offer=True)) is SaleFormat.MAKE_OFFER


def test_bare_auction_defaults_to_timed_not_absolute():
    """Absent evidence, assume there IS a reserve. That's the safe default."""
    assert detect_format(listing()) is SaleFormat.TIMED


def test_no_reserve_announcement_is_honored():
    assert detect_format(listing(announcements=["ABSOLUTE - no reserve"])) \
        is SaleFormat.ABSOLUTE


# --- the phantom-deal case -------------------------------------------------

def test_undisclosed_unmet_reserve_yields_no_acquisition_price():
    """0 bids at $11,500 is not a $11,500 car."""
    acq = plan(listing(current_bid=11_500), max_bid=20_000, wholesale_value=25_000)
    assert acq.format is SaleFormat.TIMED
    assert acq.price is None
    assert not acq.executable
    assert acq.certainty <= 0.25
    assert any("NOT an acquisition price" in n for n in acq.notes)


def test_reserve_auction_headroom_is_not_measured_off_the_listed_bid():
    deal = score_listing(listing(current_bid=11_500), val(), cfg=cfg())
    assert deal.acquisition.price is None
    # Falls back to the bid for display, but the score is heavily discounted.
    assert deal.acquisition.certainty <= 0.25


def test_buy_now_beats_an_identical_timed_car_on_score():
    """Same economics, but one price is real and the other is a guess."""
    timed = score_listing(listing(current_bid=20_000), val(), cfg=cfg())
    buy_now = score_listing(
        listing(current_bid=20_000, buy_now=20_000), val(), cfg=cfg()
    )
    assert buy_now.executable and not timed.executable
    assert buy_now.score > timed.score
    assert rank([timed, buy_now])[0] is buy_now


# --- buy now ---------------------------------------------------------------

def test_buy_now_under_max_says_buy_and_prices_the_spread():
    deal = score_listing(listing(buy_now=20_000), val(), cfg=cfg())
    assert deal.executable
    assert deal.headroom == deal.max_bid - 20_000
    assert deal.acquisition.action.startswith("BUY NOW")


def test_buy_now_over_max_says_pass():
    deal = score_listing(listing(buy_now=40_000), val(), cfg=cfg())
    assert "Pass" in deal.acquisition.action
    assert deal.headroom < 0
    assert deal.score == 0.0


def test_buy_now_headroom_uses_the_buy_now_price_not_the_bid():
    """A cheap standing bid must not inflate a Buy Now spread."""
    deal = score_listing(listing(current_bid=5_000, buy_now=20_000), val(), cfg=cfg())
    assert deal.headroom == deal.max_bid - 20_000


# --- make offer ------------------------------------------------------------

def test_make_offer_recommends_your_max_as_the_offer():
    deal = score_listing(listing(make_offer=True), val(), cfg=cfg())
    assert deal.acquisition.recommended_offer == deal.max_bid
    assert deal.acquisition.action.startswith("OFFER")


def test_lowball_offer_is_flagged_as_likely_rejected():
    """On a cheap car, fixed costs eat so much that your max is a lowball.

    $1,600 of buy fee, transport, holding and recon is 27% of a $6,000 car.
    The offer you can justify lands far under book, and the seller says no.
    """
    deal = score_listing(listing(make_offer=True, current_bid=3_000),
                         val(wholesale=6_000), cfg=cfg())
    assert deal.acquisition.recommended_offer / 6_000 < 0.80
    assert "likely rejected" in deal.acquisition.action
    assert deal.acquisition.certainty < 0.8


def test_offer_near_book_is_not_flagged():
    """On an expensive car the same fixed costs barely move the ratio."""
    deal = score_listing(listing(make_offer=True), val(wholesale=60_000), cfg=cfg())
    assert deal.acquisition.recommended_offer / 60_000 > 0.90
    assert "rejected" not in deal.acquisition.action


# --- disclosed reserves ----------------------------------------------------

def test_disclosed_reserve_under_max_is_executable():
    acq = plan(listing(reserve=19_000), max_bid=20_000, wholesale_value=25_000)
    assert acq.executable and acq.price == 19_000


def test_disclosed_reserve_over_max_is_a_pass():
    """The price is known and transactable -- it's just too much money."""
    acq = plan(listing(reserve=30_000), max_bid=20_000, wholesale_value=25_000)
    assert acq.price == 30_000
    assert "Pass" in acq.action


def test_reserve_met_makes_the_bid_meaningful_again():
    acq = plan(listing(current_bid=19_000, reserve_met=True), max_bid=20_000)
    assert acq.executable and acq.price == 19_000
    assert "reserve met" in acq.action


def test_known_price_over_max_scores_zero_not_a_thin_deal():
    """You know the price and it's over your number. That's a pass."""
    deal = score_listing(listing(buy_now=40_000), val(), cfg=cfg())
    assert deal.score == 0.0


def test_absolute_announcement_overrides_a_timed_format_label():
    """No-reserve is a hard fact; it beats a generic 'timed' column."""
    l = listing(sale_format="timed", announcements=["ABSOLUTE - no reserve"])
    assert detect_format(l) is SaleFormat.ABSOLUTE
    assert plan(l, max_bid=20_000).executable


def test_reserve_auction_ranks_below_an_equivalent_known_price():
    """The whole point: don't let a hidden reserve masquerade as a deal."""
    hidden = score_listing(listing(current_bid=18_000), val(), cfg=cfg())
    known = score_listing(
        listing(current_bid=18_000, reserve_met=True), val(), cfg=cfg()
    )
    assert known.score > hidden.score
    assert rank([hidden, known])[0] is known


def test_executable_flag_separates_real_prices_from_hopeful_ones():
    assert score_listing(listing(buy_now=20_000), val(), cfg=cfg()).executable
    assert score_listing(listing(make_offer=True), val(), cfg=cfg()).executable
    assert score_listing(
        listing(reserve_met=True), val(), cfg=cfg()
    ).executable
    assert not score_listing(listing(), val(), cfg=cfg()).executable


# --- ACV make offer with a published reserve -------------------------------
# The seller's number is on screen. Offer it and the car is yours; offer less
# and you might still get it. That's a firm price with free upside.

def test_disclosed_reserve_on_make_offer_is_a_firm_price():
    deal = score_listing(
        listing(make_offer=True, reserve=12_000, current_bid=9_000),
        val(), cfg=cfg(),
    )
    acq = deal.acquisition
    assert acq.executable
    assert acq.price == 12_000
    assert acq.recommended_offer == 12_000
    assert acq.certainty == 1.0            # as certain as a Buy Now
    assert "takes it now" in acq.action
    assert "Try lower first" in acq.action


def test_disclosed_reserve_scores_like_a_buy_now_not_a_blind_offer():
    """Same price, but one is published and one is a guess."""
    published = score_listing(
        listing(make_offer=True, reserve=20_000), val(), cfg=cfg()
    )
    blind = score_listing(listing(make_offer=True), val(), cfg=cfg())
    assert published.acquisition.certainty > blind.acquisition.certainty
    assert published.score > blind.score


def test_headroom_is_measured_against_their_published_reserve():
    deal = score_listing(
        listing(make_offer=True, reserve=12_000, current_bid=5_000),
        val(), cfg=cfg(),
    )
    assert deal.headroom == deal.max_bid - 12_000


def test_reserve_over_max_is_a_pass_even_though_you_could_offer():
    deal = score_listing(
        listing(make_offer=True, reserve=40_000), val(), cfg=cfg()
    )
    assert "Pass" in deal.acquisition.action
    assert deal.score == 0.0          # known price over the max is not a deal


def test_upside_below_the_reserve_is_called_out():
    deal = score_listing(
        listing(make_offer=True, reserve=12_000), val(), cfg=cfg()
    )
    assert any("upside" in n for n in deal.acquisition.notes)


def test_cheaper_of_buy_now_and_reserve_wins():
    """If both are published, you'd obviously take the lower one."""
    acq = plan(listing(buy_now=21_000, reserve=19_500), max_bid=23_000)
    assert acq.price == 19_500
    assert acq.executable


def test_blind_offer_still_uses_the_ratio_read():
    """No published reserve means the acceptance guess is all we have."""
    deal = score_listing(
        listing(make_offer=True, current_bid=3_000), val(wholesale=6_000), cfg=cfg()
    )
    assert deal.acquisition.recommended_offer == deal.max_bid
    assert "likely rejected" in deal.acquisition.action
