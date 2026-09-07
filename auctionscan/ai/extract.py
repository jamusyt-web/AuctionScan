"""Pull a listing off a screenshot.

There are no run lists any more -- the marketplaces are all live platforms and
nothing exports. But everything the scorer needs is already rendered on the
page you're looking at: VIN, miles, grade, announcements, the damage table,
the bid or Buy Now or reserve, and the AccuTrade panel with MMR, target
auction, instant offer and retail sitting right there in the sidebar.

So the input is a screenshot. You're on the page already; snap it and the
tool reads it. No API key for the marketplace, no automated access to
anything, nothing that touches your dealer accounts. It only ever sees a
picture you chose to take.

Send several shots of the same car in one call -- the vehicle page, the
AccuTrade panel, the Carfax panel -- and they get merged into one listing.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

MEDIA_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp",
}

SYSTEM_PROMPT = """You read screenshots of wholesale vehicle auction listings \
(ADESA/OPENLANE, Manheim, ACV) and extract them into structured data for a \
dealer's buying tool.

Rules:
- Transcribe EXACTLY what is shown. Never infer, average, or fill in a \
plausible number. If a field is not visible, leave it null.
- Numbers matter more than anything else here. Read every dollar figure \
character by character. A misread MMR costs real money.
- Distinguish the price types carefully, they are NOT interchangeable:
  * current_bid -- the live/opening bid on a timed or live auction
  * buy_now -- a published take-it-now price
  * reserve -- the seller's reserve WHEN IT IS SHOWN (ACV's make-offer tab \
publishes it). Do not guess a reserve that isn't displayed.
- Distinguish the valuation numbers, which usually come from an AccuTrade / \
VIN-dow sidebar panel:
  * mmr -- labeled "Manheim"
  * target_auction -- labeled "Target Auction"
  * instant_offer -- labeled "Instant Offer"
  * retail -- a MILEAGE-ADJUSTED retail figure (e.g. a marketplace's own \
"Retail" next to its wholesale number)
  * median_retail -- labeled "Median Retail" or "Cars.com Median Retail". \
This one is NOT odometer-adjusted; keep it separate from retail.
- sale_format: "buy_now" if a buy-now price is shown, "make_offer" if there \
is an offer tab or offer button, "absolute" if announced no-reserve, \
otherwise "timed" for a timed sale or "live" for a live lane.
- drivable: false if the listing says INOP, "Not Drivable", no-start or \
similar. Leave null if it simply isn't mentioned -- null is not "yes".
- as_is: true if announced AS-IS, "no arbitration", or a red light.
- announcements: transcribe every disclosure verbatim, including AS-IS and \
INOP badges.
- condition_report: summarize the mechanical/exterior/interior notes and the \
itemized damage table into a few sentences, keeping part names and severities.
- common_problems: the "Common Problems" list from the AccuTrade panel, if \
shown, split into separate items.
- If several screenshots show the same vehicle, merge them into one record.
- If the screenshots clearly show MORE THAN ONE vehicle, extract only the one \
the page is actually about (the detail page), not sidebar suggestions."""


class ListingExtraction(BaseModel):
    """One vehicle, as read off the screen."""

    vin: str | None = Field(default=None, description="17-character VIN")
    year: int | None = None
    make: str | None = None
    model: str | None = None
    trim: str | None = None
    odometer: int | None = None
    grade: float | None = Field(default=None, description="Condition grade 0-5")

    current_bid: float | None = None
    buy_now: float | None = None
    reserve: float | None = Field(
        default=None, description="Only if a reserve figure is actually displayed"
    )
    reserve_met: bool | None = None
    make_offer: bool = Field(
        default=False, description="True if an offer channel is available"
    )
    sale_format: str | None = Field(
        default=None, description="buy_now | make_offer | absolute | timed | live"
    )

    mmr: float | None = Field(default=None, description="Labeled 'Manheim'")
    target_auction: float | None = None
    instant_offer: float | None = None
    retail: float | None = Field(
        default=None, description="Mileage-adjusted retail, NOT a median"
    )
    median_retail: float | None = Field(
        default=None, description="Book median; not odometer-adjusted"
    )

    drivable: bool | None = None
    as_is: bool = False
    announcements: list[str] = Field(default_factory=list)
    condition_report: str | None = None
    common_problems: list[str] = Field(default_factory=list)

    seller: str | None = None
    location: str | None = None
    lot: str | None = None
    source: str | None = Field(
        default=None, description="adesa, manheim, acv, or another marketplace"
    )

    notes: str | None = Field(
        default=None, description="Anything ambiguous or unreadable in the images"
    )


def _image_block(path: Path) -> dict[str, Any]:
    media_type = MEDIA_TYPES.get(path.suffix.lower())
    if not media_type:
        raise ValueError(
            f"{path.name}: unsupported image type. Use "
            f"{', '.join(sorted(MEDIA_TYPES))}"
        )
    data = base64.standard_b64encode(path.read_bytes()).decode("utf-8")
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": media_type, "data": data},
    }


class ScreenshotExtractor:
    """Reads auction listings out of screenshots.

    Effort defaults to high: this is a handful of images per car, not a bulk
    pass, and a misread price is the most expensive mistake the tool can make.
    """

    def __init__(
        self,
        model: str = "claude-opus-5",
        effort: str = "high",
        client: Any = None,
    ):
        # Only reach for the SDK if we actually need to build a client, so an
        # injected one (tests, a shared session) works without it installed.
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self.client = client
        self.model = model
        self.effort = effort

    def extract(
        self, image_paths: list[Path], extra_text: str = ""
    ) -> ListingExtraction:
        """Read one vehicle out of one or more screenshots of the same car."""
        if not image_paths:
            raise ValueError("No screenshots given")

        content: list[dict[str, Any]] = [_image_block(p) for p in image_paths]
        prompt = (
            f"Extract the vehicle listing from these {len(image_paths)} "
            f"screenshot(s) of the same car."
        )
        if extra_text:
            prompt += f"\n\nAdditional context pasted from the page:\n{extra_text}"
        content.append({"type": "text", "text": prompt})

        response = self.client.messages.parse(
            model=self.model,
            max_tokens=8_000,
            system=SYSTEM_PROMPT,
            thinking={"type": "adaptive"},
            output_config={"effort": self.effort},
            messages=[{"role": "user", "content": content}],
            output_format=ListingExtraction,
        )
        return response.parsed_output


# Column order matches what CsvSource and CsvValuation already read, so an
# extracted listing drops straight into the existing pipeline.
CSV_FIELDS = [
    "Lot", "VIN", "Year", "Make", "Model", "Trim", "Miles", "Grade",
    "Current Bid", "Buy Now", "Reserve", "Reserve Met", "Make Offer",
    "Sale Format", "Announcements", "Mechanical", "Condition Report",
    "Common Problems", "Seller Type", "Location", "Source",
    "MMR", "Target Auction", "Instant Offer", "Retail", "Median Retail",
]


def to_csv_row(x: ListingExtraction) -> dict[str, Any]:
    """Flatten an extraction into a row the CSV pipeline understands."""
    mechanical = ""
    if x.drivable is False:
        mechanical = "Not Drivable - INOP"
    return {
        "Lot": x.lot or "",
        "VIN": (x.vin or "").upper(),
        "Year": x.year or "",
        "Make": x.make or "",
        "Model": x.model or "",
        "Trim": x.trim or "",
        "Miles": x.odometer or "",
        "Grade": x.grade if x.grade is not None else "",
        "Current Bid": x.current_bid or "",
        "Buy Now": x.buy_now or "",
        "Reserve": x.reserve or "",
        "Reserve Met": (
            "" if x.reserve_met is None else ("yes" if x.reserve_met else "no")
        ),
        "Make Offer": "yes" if x.make_offer else "",
        "Sale Format": x.sale_format or "",
        "Announcements": "; ".join(x.announcements),
        "Mechanical": mechanical,
        "Condition Report": x.condition_report or "",
        "Common Problems": "; ".join(x.common_problems),
        "Seller Type": x.seller or "",
        "Location": x.location or "",
        "Source": x.source or "",
        "MMR": x.mmr or "",
        "Target Auction": x.target_auction or "",
        "Instant Offer": x.instant_offer or "",
        "Retail": x.retail or "",
        "Median Retail": x.median_retail or "",
    }
