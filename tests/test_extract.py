"""Screenshot extraction and the round trip into scoring.

The API call is stubbed. What's actually under test is the contract between
what the model returns and what the scoring pipeline reads -- a field that
extracts fine but lands in the wrong CSV column is silently worthless.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from auctionscan.ai.extract import (
    CSV_FIELDS, ListingExtraction, ScreenshotExtractor, to_csv_row,
)
from auctionscan.config import BuyBox, Capabilities, Config, CostModel, ExitChannel
from auctionscan.enrich.valuation import CsvValuation
from auctionscan.scoring import score_listing
from auctionscan.sources.csv_source import CsvSource


def tesla_extraction() -> ListingExtraction:
    """What the model should return from the two ADESA screenshots."""
    return ListingExtraction(
        vin="5YJYGDEF2MF252410",
        year=2021, make="Tesla", model="Model Y", trim="Performance",
        odometer=143_991, grade=3.1,
        current_bid=11_500,
        sale_format="timed",
        mmr=15_750, target_auction=15_925, instant_offer=14_425,
        retail=21_179, median_retail=28_470,
        drivable=False, as_is=True,
        announcements=["AS-IS", "INOP", "Refer to CR for Disclosures"],
        condition_report=(
            "Not Drivable - Mechanical, Unverified Items. Glass Issue, "
            "Cosmetic Issue, Wheels & Tires Issue. Rear bumper cover multiple "
            "heavy scratches; LF and RR wheel curb rash; R quarter panel dent."
        ),
        common_problems=[
            "Infotainment Screen Inop", "Charger Missing",
            "Battery Replacement", "Electric Motor(s) Wear/Failure",
        ],
        seller="CARVANA Wholesale", location="ADESA Washington DC",
        lot="be5be7b6", source="adesa",
    )


def acv_offer_extraction() -> ListingExtraction:
    """An ACV make-offer listing with the reserve published on the tab."""
    return ListingExtraction(
        vin="3VW6T7BU3LM012345",
        year=2020, make="Volkswagen", model="Jetta", trim="GLI",
        odometer=43_700, grade=4.3,
        reserve=10_400, make_offer=True, sale_format="make_offer",
        mmr=13_610, retail=17_200,
        source="acv", location="ACV Digital",
    )


def shop_config() -> Config:
    return Config(
        buy_box=BuyBox(target_gross=1_200, target_gross_pct=0.06, min_grade=0.0,
                       max_odometer=160_000, allow_inoperable=True,
                       allow_as_is_inop=True, max_powertrain_reserve=25_000),
        costs=CostModel(),
        capabilities=Capabilities(ev_battery_pack=3_500, ev_drive_unit=3_500),
        exits=[
            ExitChannel(name="wholesale", days_held=21, sell_fee=300,
                        target_gross=1_200, target_gross_pct=0.06),
            ExitChannel(name="retail", days_held=45, sell_fee=0,
                        recon_multiplier=1.6, target_gross=2_500,
                        target_gross_pct=0.10),
        ],
    )


def write_store(tmp_path: Path, *extractions: ListingExtraction) -> Path:
    store = tmp_path / "watchlist.csv"
    with store.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        for x in extractions:
            w.writerow(to_csv_row(x))
    return store


# --- the round trip --------------------------------------------------------

def test_extraction_survives_the_csv_round_trip(tmp_path):
    """Every field that matters must come back out the other side."""
    store = write_store(tmp_path, tesla_extraction())
    listing = next(iter(CsvSource(store)))

    assert listing.vin == "5YJYGDEF2MF252410"
    assert listing.year == 2021
    assert listing.odometer == 143_991
    assert listing.grade == 3.1
    assert listing.current_bid == 11_500
    assert listing.drivable is False
    assert listing.as_is is True
    assert "INOP" in " ".join(listing.announcements)


def test_valuation_numbers_land_in_the_right_columns(tmp_path):
    """MMR, target, instant offer and the two retails must not cross wires."""
    store = write_store(tmp_path, tesla_extraction())
    listing = next(iter(CsvSource(store)))
    val = CsvValuation().value(listing)

    assert val.mmr == 15_750
    assert val.wholesale == 15_750          # MMR is the benchmark
    assert val.target_auction == 15_925
    assert val.instant_offer == 14_425
    assert val.retail == 21_179             # mileage-adjusted
    assert val.retail_median == 28_470      # book median, kept separate
    assert "Battery Replacement" in val.common_problems


def test_a_screenshot_scores_the_same_as_hand_keyed_data(tmp_path):
    """The whole point: snap it, and it's scored."""
    store = write_store(tmp_path, tesla_extraction())
    listing = next(iter(CsvSource(store, source_label="adesa")))
    deal = score_listing(listing, CsvValuation().value(listing), cfg=shop_config())

    assert deal.powertrain_reserve.unverified          # read the INOP
    assert deal.powertrain_reserve.high == 3_500       # used his pack cost
    assert deal.exit_channel == "retail"               # picked the right exit
    assert deal.max_bid > 0


def test_make_offer_reserve_round_trips_and_stays_firm(tmp_path):
    store = write_store(tmp_path, acv_offer_extraction())
    listing = next(iter(CsvSource(store, source_label="acv")))
    assert listing.reserve == 10_400
    assert listing.make_offer is True

    deal = score_listing(listing, CsvValuation().value(listing), cfg=shop_config())
    assert deal.acquisition.price == 10_400
    assert deal.acquisition.certainty == 1.0
    assert "takes it now" in deal.acquisition.action


def test_regrabbing_a_car_updates_instead_of_duplicating(tmp_path):
    """Prices move; re-snapping the same VIN should replace, not append."""
    first = tesla_extraction()
    store = write_store(tmp_path, first)

    updated = tesla_extraction()
    updated.current_bid = 12_750
    rows = {r["VIN"]: r for r in csv.DictReader(store.open(encoding="utf-8-sig"))}
    rows[updated.vin] = to_csv_row(updated)
    with store.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows.values())

    listings = list(CsvSource(store))
    assert len(listings) == 1
    assert listings[0].current_bid == 12_750


# --- extraction mechanics --------------------------------------------------

class FakeResponse:
    def __init__(self, parsed):
        self.parsed_output = parsed


class FakeMessages:
    def __init__(self, parsed):
        self.parsed = parsed
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return FakeResponse(self.parsed)


class FakeClient:
    def __init__(self, parsed):
        self.messages = FakeMessages(parsed)


def png(tmp_path: Path, name: str = "shot.png") -> Path:
    # Smallest valid PNG header is enough; we never decode it locally.
    path = tmp_path / name
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    return path


def test_multiple_screenshots_are_sent_as_one_vehicle(tmp_path):
    """Vehicle page + AccuTrade panel + Carfax panel merge into one listing."""
    client = FakeClient(tesla_extraction())
    extractor = ScreenshotExtractor(client=client)
    shots = [png(tmp_path, f"s{i}.png") for i in range(3)]

    result = extractor.extract(shots)
    assert result.vin == "5YJYGDEF2MF252410"

    content = client.messages.calls[0]["messages"][0]["content"]
    images = [b for b in content if b["type"] == "image"]
    assert len(images) == 3
    assert content[-1]["type"] == "text"


def test_unsupported_image_type_is_rejected_clearly(tmp_path):
    bad = tmp_path / "shot.bmp"
    bad.write_bytes(b"BM")
    with pytest.raises(ValueError, match="unsupported image type"):
        ScreenshotExtractor(client=FakeClient(tesla_extraction())).extract([bad])


def test_no_images_is_an_error(tmp_path):
    with pytest.raises(ValueError, match="No screenshots"):
        ScreenshotExtractor(client=FakeClient(tesla_extraction())).extract([])


def test_inop_becomes_a_mechanical_note_the_csv_parser_reads():
    row = to_csv_row(tesla_extraction())
    assert "Not Drivable" in row["Mechanical"]


def test_absent_fields_stay_empty_rather_than_zero():
    """A missing price must not become $0 -- that would score as free."""
    row = to_csv_row(ListingExtraction(vin="1FTFW1E84MFA12345"))
    assert row["Current Bid"] == ""
    assert row["Buy Now"] == ""
    assert row["Reserve"] == ""
    assert row["MMR"] == ""
