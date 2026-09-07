"""How you actually get the car, and at what price.

The bug this fixes: scoring against `current_bid` on a timed auction is
scoring against a number that isn't for sale. A unit showing $11,500 with
zero bids and an undisclosed reserve $2,000 north of that is not an $11,500
car -- but a naive spread calculation ranks it as the best buy on the page.

Buy Now and Make Offer are different in kind, not degree. They give you a
price you can transact at *now*, so the spread against your walk-away number
is real money rather than a guess. Everything here exists to keep those two
separated from wishful thinking, and to rank them accordingly.

A disclosed reserve is the same thing again. ACV's make-offer tab publishes
the seller's number: offer it and the car is yours, offer less and you might
still get it. That is a firm price with free upside, not a gamble -- so it
scores with full certainty, and the recommended play is to try lower first.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .models import Listing


class SaleFormat(str, Enum):
    BUY_NOW = "buy_now"          # executable now at a published price
    MAKE_OFFER = "make_offer"    # you name the price; seller accepts or not
    ABSOLUTE = "absolute"        # no reserve -- the bid is the price
    TIMED = "timed"              # timed auction, reserve usually undisclosed
    LIVE = "live"                # live/simulcast lane
    UNKNOWN = "unknown"


# How much to trust that you can actually buy at the price we scored.
# This multiplies the deal score, so executable prices outrank hopeful ones.
CERTAINTY = {
    SaleFormat.BUY_NOW: 1.00,     # published price, take it or leave it
    SaleFormat.MAKE_OFFER: 0.80,  # blind offer: you control the number, not the yes
    SaleFormat.ABSOLUTE: 0.75,    # no reserve, but you can still be outbid
    SaleFormat.LIVE: 0.55,        # price discovery happens in real time
    SaleFormat.TIMED: 0.50,       # reserve met -- still biddable above you
    SaleFormat.UNKNOWN: 0.40,
}
# A timed auction whose reserve is undisclosed and unmet. The listed bid is
# not an acquisition price and should not be treated as one.
RESERVE_UNMET_CERTAINTY = 0.20
# A published reserve is a price you can hit on demand -- as good as Buy Now.
DISCLOSED_RESERVE_CERTAINTY = 1.00

# Reserves cluster near the wholesale benchmark. Used only to warn how far
# the listed bid probably sits from the real floor -- never as a buy price.
TYPICAL_RESERVE_PCT_OF_WHOLESALE = 0.92


@dataclass
class Acquisition:
    """The executable path to owning the car."""

    format: SaleFormat = SaleFormat.UNKNOWN
    price: float | None = None        # what you'd actually pay, if knowable
    certainty: float = 0.4
    executable: bool = False          # can you transact at a known price now?
    recommended_offer: float | None = None
    action: str = ""
    notes: list[str] = None           # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.notes is None:
            self.notes = []


def detect_format(listing: Listing) -> SaleFormat:
    """Work out how this car is being sold."""
    # An absolute/no-reserve announcement is authoritative: it overrides a
    # generic "timed" format label, because it removes the hidden floor.
    blob = " ".join(listing.announcements).lower()
    if "absolute" in blob or "no reserve" in blob:
        return SaleFormat.ABSOLUTE

    if listing.sale_format:
        try:
            return SaleFormat(listing.sale_format.strip().lower().replace(" ", "_"))
        except ValueError:
            pass
    if listing.buy_now:
        return SaleFormat.BUY_NOW
    if listing.make_offer:
        return SaleFormat.MAKE_OFFER
    return SaleFormat.TIMED


def plan(
    listing: Listing,
    max_bid: float,
    wholesale_value: float | None = None,
) -> Acquisition:
    """Decide what to do about this car and what it would really cost.

    `max_bid` is the walk-away number from scoring -- what the car is worth to
    you. This turns that into an instruction.
    """
    fmt = detect_format(listing)
    acq = Acquisition(format=fmt, certainty=CERTAINTY.get(fmt, 0.4))

    # Every published number you could hit on demand. Both a Buy Now price and
    # a disclosed reserve are firm; take whichever is cheaper.
    options: list[tuple[float, str]] = []
    if listing.buy_now:
        options.append((listing.buy_now, "buy_now"))
    if listing.reserve is not None:
        options.append((listing.reserve, "reserve"))

    if options:
        price, kind = min(options, key=lambda o: o[0])
        acq.price = price
        acq.certainty = DISCLOSED_RESERVE_CERTAINTY
        # A published price is transactable whether or not it's a good deal.
        # "Too expensive" is a scoring decision, not an execution one.
        acq.executable = True
        offering = fmt is SaleFormat.MAKE_OFFER or kind == "reserve"

        if price > max_bid:
            label = "Buy now" if kind == "buy_now" else "Reserve"
            acq.action = (
                f"Pass -- {label.lower()} ${price:,.0f} is "
                f"${price - max_bid:,.0f} over your max"
            )
            return acq

        under = max_bid - price
        if offering:
            acq.recommended_offer = price
            acq.action = (
                f"OFFER ${price:,.0f} -- their published reserve, takes it now "
                f"(${under:,.0f} under your max). Try lower first."
            )
            acq.notes.append(
                f"Reserve is disclosed, so ${price:,.0f} is a firm price. "
                f"Anything they accept below it is upside; you can go as low "
                f"as you like and still walk at ${max_bid:,.0f}."
            )
        else:
            acq.action = (
                f"BUY NOW at ${price:,.0f} (${under:,.0f} under your max)"
            )
        return acq

    if fmt is SaleFormat.MAKE_OFFER:
        # No published reserve: you're bidding blind against their expectation.
        acq.recommended_offer = max_bid
        acq.price = max_bid
        acq.executable = True
        acq.action = f"OFFER ${max_bid:,.0f} (no reserve shown)"
        if wholesale_value and wholesale_value > 0:
            ratio = max_bid / wholesale_value
            acq.notes.append(
                f"Offer is {ratio:.0%} of wholesale value ${wholesale_value:,.0f}"
            )
            if ratio < 0.80:
                acq.action += " (likely rejected -- well under book)"
                acq.certainty *= 0.6
            elif ratio < 0.90:
                acq.action += " (may be rejected)"
                acq.certainty *= 0.85
        return acq

    # Auction formats: the bid is a floor, not a price.
    bid = listing.current_bid or 0.0

    if fmt is SaleFormat.ABSOLUTE:
        acq.price = bid
        acq.executable = True
        acq.action = f"Bid up to ${max_bid:,.0f} (no reserve)"
        return acq

    if listing.reserve_met is True:
        acq.price = bid
        acq.executable = True
        acq.action = f"Bid up to ${max_bid:,.0f} (reserve met at ${bid:,.0f})"
        return acq

    # The case that started all this: undisclosed, unmet reserve.
    acq.price = None
    acq.executable = False
    acq.certainty = RESERVE_UNMET_CERTAINTY
    acq.notes.append(
        f"Listed bid ${bid:,.0f} is NOT an acquisition price -- reserve "
        f"undisclosed and unmet."
    )
    if wholesale_value:
        likely = wholesale_value * TYPICAL_RESERVE_PCT_OF_WHOLESALE
        acq.notes.append(
            f"Reserves typically land near ${likely:,.0f} on a car booked at "
            f"${wholesale_value:,.0f} -- assume the floor is well above the bid."
        )
    acq.action = f"Bid up to ${max_bid:,.0f}, but expect a reserve above the bid"
    return acq
