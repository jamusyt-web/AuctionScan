"""AI reading of condition reports and photos.

This is the part that doesn't exist off the shelf. Auctions publish condition
reports as prose plus 20-40 photos, and the number you actually need -- what
will this cost me to make retail-ready -- is not in the payload. Claude reads
the report and the photos and returns a structured recon estimate.

Two things keep this honest:

  * It estimates recon cost, it does not appraise the car. Valuation comes from
    MMR/AccuTrade; this only feeds the cost side of the equation.
  * Photo-based estimates run light -- you cannot see a bad transmission in a
    JPEG. The scorer applies a safety multiplier on top of whatever comes back,
    and the model is instructed to say when it can't tell.

For a full run list, use `analyze_batch` -- the Batch API is half price and
these calls are not latency-sensitive.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from ..models import ConditionAssessment, DamageItem, Listing

SYSTEM_PROMPT = """You are an experienced used-vehicle reconditioning estimator \
at a wholesale dealership. You read auction condition reports and photos and \
estimate what it will cost to make the vehicle front-line ready for a dealer \
resale -- not a retail-perfect restoration.

Rules:
- Estimate REAL wholesale recon costs: body/paint at trade rates, not retail \
collision rates. A door ding is $75-150 PDR, not $800.
- Include tires when tread is at or below 4/32", brakes when noted, and a \
standard detail.
- Do NOT estimate mechanical repairs you cannot see. If the report mentions a \
warning light, engine noise, transmission slip, or anything driveline, put it \
in mechanical_concerns and flag it -- do not guess a dollar figure.
- Announcements from the seller (frame damage, structural, salvage, true \
mileage unknown, emissions, airbag) are the most important thing in the \
report. Any of these belongs in red_flags verbatim.
- Photos taken in a dim lane hide damage. If the photos are poor, few, or \
avoid a panel, say so and lower your confidence.
- confidence is 0-1: how sure you are of the recon estimate given what you \
were shown. Sparse data means low confidence, not a bigger number."""


class DamageItemOut(BaseModel):
    panel: str = Field(description="Panel or component, e.g. 'left front door'")
    kind: str = Field(description="dent, scratch, rust, crack, chip, missing, etc.")
    severity: str = Field(description="light, moderate, or heavy")
    estimated_cost: float = Field(description="Wholesale repair cost in USD")


class ConditionOut(BaseModel):
    estimated_recon_cost: float = Field(
        description="Total wholesale recon in USD, excluding unknown mechanical work"
    )
    damage_items: list[DamageItemOut] = Field(default_factory=list)
    tire_replacement_needed: bool = False
    mechanical_concerns: list[str] = Field(
        default_factory=list,
        description="Driveline/mechanical issues noted but not priced",
    )
    cosmetic_summary: str = Field(description="One or two sentences")
    red_flags: list[str] = Field(
        default_factory=list,
        description="Announcements or findings that should stop or reprice the bid",
    )
    confidence: float = Field(description="0-1 confidence in the recon estimate")


def _build_content(listing: Listing, max_photos: int) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = []
    for url in listing.photo_urls[:max_photos]:
        content.append({"type": "image", "source": {"type": "url", "url": url}})

    facts = [
        f"Vehicle: {listing.description}",
        f"VIN: {listing.vin}",
        f"Odometer: {listing.odometer:,}" if listing.odometer else "Odometer: unknown",
        f"Auction grade: {listing.grade}" if listing.grade is not None else "",
        f"Seller type: {listing.seller_type}" if listing.seller_type else "",
    ]
    if listing.announcements:
        facts.append("Announcements: " + "; ".join(listing.announcements))
    if listing.condition_report_text:
        facts.append(f"Condition report:\n{listing.condition_report_text}")
    if not listing.photo_urls:
        facts.append("(No photos available.)")

    content.append(
        {
            "type": "text",
            "text": "\n".join(f for f in facts if f)
            + "\n\nEstimate reconditioning cost and flag anything that should "
            "change or stop the bid.",
        }
    )
    return content


def _to_assessment(parsed: ConditionOut) -> ConditionAssessment:
    return ConditionAssessment(
        source="claude",
        estimated_recon_cost=parsed.estimated_recon_cost,
        damage_items=[
            DamageItem(
                panel=d.panel,
                kind=d.kind,
                severity=d.severity,
                estimated_cost=d.estimated_cost,
            )
            for d in parsed.damage_items
        ],
        tire_replacement_needed=parsed.tire_replacement_needed,
        mechanical_concerns=parsed.mechanical_concerns,
        cosmetic_summary=parsed.cosmetic_summary,
        red_flags=parsed.red_flags,
        confidence=parsed.confidence,
    )


class ConditionAnalyzer:
    """Wraps Claude for condition assessment.

    effort defaults to "medium": this is a high-volume extraction over a run
    list, and the marginal quality of "high" does not pay for itself per car.
    Raise it when you are working a short list you intend to bid on.
    """

    def __init__(
        self,
        model: str = "claude-opus-5",
        max_photos: int = 8,
        effort: str = "medium",
        client: Any = None,
    ):
        import anthropic

        self.client = client or anthropic.Anthropic()
        self.model = model
        self.max_photos = max_photos
        self.effort = effort

    def analyze(self, listing: Listing) -> ConditionAssessment | None:
        """Assess one vehicle. Returns None if the model call fails."""
        if not listing.condition_report_text and not listing.photo_urls:
            return None

        try:
            response = self.client.messages.parse(
                model=self.model,
                max_tokens=8_000,
                system=SYSTEM_PROMPT,
                thinking={"type": "adaptive"},
                output_config={"effort": self.effort},
                messages=[
                    {"role": "user", "content": _build_content(listing, self.max_photos)}
                ],
                output_format=ConditionOut,
            )
        except Exception as exc:  # noqa: BLE001 - one bad car shouldn't stop the run
            return ConditionAssessment(
                source="claude",
                notes=f"Analysis failed: {exc}",
                confidence=0.0,
                estimated_recon_cost=0.0,
            )

        return _to_assessment(response.parsed_output)

    def analyze_batch(self, listings: list[Listing]) -> dict[str, ConditionAssessment]:
        """Assess a whole run list via the Batch API at half price.

        Submits and polls. Results come back keyed by VIN; the Batch API returns
        results in arbitrary order, so never match them up by position.
        """
        import time

        from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
        from anthropic.types.messages.batch_create_params import Request

        scoreable = [
            l for l in listings if l.condition_report_text or l.photo_urls
        ]
        if not scoreable:
            return {}

        requests_ = [
            Request(
                custom_id=listing.vin,
                params=MessageCreateParamsNonStreaming(
                    model=self.model,
                    max_tokens=8_000,
                    system=SYSTEM_PROMPT,
                    thinking={"type": "adaptive"},
                    output_config={
                        "effort": self.effort,
                        "format": {
                            "type": "json_schema",
                            "schema": ConditionOut.model_json_schema(),
                        },
                    },
                    messages=[
                        {
                            "role": "user",
                            "content": _build_content(listing, self.max_photos),
                        }
                    ],
                ),
            )
            for listing in scoreable
        ]

        batch = self.client.messages.batches.create(requests=requests_)
        while True:
            batch = self.client.messages.batches.retrieve(batch.id)
            if batch.processing_status == "ended":
                break
            time.sleep(20)

        out: dict[str, ConditionAssessment] = {}
        for result in self.client.messages.batches.results(batch.id):
            if result.result.type != "succeeded":
                continue
            text = next(
                (b.text for b in result.result.message.content if b.type == "text"),
                None,
            )
            if not text:
                continue
            try:
                out[result.custom_id] = _to_assessment(
                    ConditionOut(**json.loads(text))
                )
            except (ValueError, TypeError):
                continue
        return out
