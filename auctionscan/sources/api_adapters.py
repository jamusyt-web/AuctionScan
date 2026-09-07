"""Marketplace API adapters (credentialed).

These are the sanctioned path: official APIs, your own dealer credentials, a
signed data agreement. They are scaffolds -- the request shape is right, but
you must confirm exact field names against each portal's current spec when you
are granted access, because those specs are behind a login and change.

  Manheim   https://developer.manheim.com  (OAuth2 client credentials;
            Searches + Inventory + MMR Valuations. MMR moved to the Cox
            Automotive API Storefront -- ask your Cox rep for onboarding.)
  ACV       Smart Acquisition Manager exposes a buying API to dealers with a
            platform to integrate. Contact your ACV rep; there is no public
            self-serve portal.
  OPENLANE  No public developer portal. Integration is via partnership.

Fill in `_parse_listing` once you can see a real payload. Everything else --
auth, paging, retry -- is already here.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from typing import Any

import requests

from ..models import Listing
from .base import ListingSource


class ApiNotConfigured(RuntimeError):
    """Raised when an adapter is used without credentials."""


class OAuth2Client:
    """Client-credentials OAuth with token caching."""

    def __init__(self, token_url: str, client_id: str, client_secret: str):
        self.token_url = token_url
        self.client_id = client_id
        self.client_secret = client_secret
        self._token: str | None = None
        self._expires_at: float = 0.0

    def token(self) -> str:
        if self._token and time.time() < self._expires_at - 60:
            return self._token
        resp = requests.post(
            self.token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            timeout=30,
        )
        resp.raise_for_status()
        payload = resp.json()
        self._token = payload["access_token"]
        self._expires_at = time.time() + float(payload.get("expires_in", 3600))
        return self._token

    def get(self, url: str, **params: Any) -> dict[str, Any]:
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {self.token()}"},
            params=params,
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()


class ManheimSource(ListingSource):
    """Manheim Marketplace search.

    Requires MANHEIM_CLIENT_ID / MANHEIM_CLIENT_SECRET and an approved
    application on the developer portal.
    """

    name = "manheim"
    BASE = "https://api.manheim.com"

    def __init__(self, search_params: dict[str, Any] | None = None, page_size: int = 100):
        cid = os.getenv("MANHEIM_CLIENT_ID")
        secret = os.getenv("MANHEIM_CLIENT_SECRET")
        if not (cid and secret):
            raise ApiNotConfigured(
                "Set MANHEIM_CLIENT_ID and MANHEIM_CLIENT_SECRET. "
                "Apply at https://developer.manheim.com"
            )
        self.client = OAuth2Client(f"{self.BASE}/oauth2/token", cid, secret)
        self.search_params = search_params or {}
        self.page_size = page_size

    def fetch(self) -> Iterator[Listing]:
        offset = 0
        while True:
            page = self.client.get(
                f"{self.BASE}/searches",
                count=self.page_size,
                offset=offset,
                **self.search_params,
            )
            items = page.get("items", [])
            if not items:
                return
            for item in items:
                yield self._parse_listing(item)
            offset += len(items)
            if len(items) < self.page_size:
                return

    def _parse_listing(self, item: dict[str, Any]) -> Listing:
        # CONFIRM these keys against a live payload before relying on them.
        return Listing(
            source=self.name,
            listing_id=str(item.get("id") or item.get("uid") or ""),
            vin=(item.get("vin") or "").upper(),
            year=item.get("year"),
            make=item.get("make", ""),
            model=item.get("model", ""),
            trim=item.get("trim", ""),
            odometer=item.get("odometer"),
            current_bid=item.get("currentBid"),
            buy_now=item.get("buyNowPrice"),
            grade=item.get("conditionGrade"),
            announcements=item.get("announcements", []) or [],
            condition_report_text=item.get("conditionReport", "") or "",
            photo_urls=item.get("imageUrls", []) or [],
            location=item.get("location", "") or "",
            url=item.get("vdpUrl", "") or "",
            raw=item,
        )


class AcvSource(ListingSource):
    """ACV Auctions marketplace.

    Requires ACV_API_TOKEN issued to your dealership. Ask your ACV rep about
    Smart Acquisition Manager API access.
    """

    name = "acv"
    BASE = "https://api.acvauctions.com"

    def __init__(self, filters: dict[str, Any] | None = None):
        token = os.getenv("ACV_API_TOKEN")
        if not token:
            raise ApiNotConfigured(
                "Set ACV_API_TOKEN. Contact your ACV rep for API access."
            )
        self.token = token
        self.filters = filters or {}

    def fetch(self) -> Iterator[Listing]:
        page = 1
        while True:
            resp = requests.get(
                f"{self.BASE}/v1/listings",
                headers={"Authorization": f"Bearer {self.token}"},
                params={"page": page, **self.filters},
                timeout=60,
            )
            resp.raise_for_status()
            items = resp.json().get("listings", [])
            if not items:
                return
            for item in items:
                yield self._parse_listing(item)
            page += 1

    def _parse_listing(self, item: dict[str, Any]) -> Listing:
        # CONFIRM these keys against a live payload before relying on them.
        vehicle = item.get("vehicle", item)
        return Listing(
            source=self.name,
            listing_id=str(item.get("id", "")),
            vin=(vehicle.get("vin") or "").upper(),
            year=vehicle.get("year"),
            make=vehicle.get("make", ""),
            model=vehicle.get("model", ""),
            trim=vehicle.get("trim", ""),
            odometer=vehicle.get("odometer"),
            current_bid=item.get("current_bid"),
            buy_now=item.get("buy_now_price"),
            grade=item.get("condition_grade"),
            announcements=item.get("announcements", []) or [],
            condition_report_text=item.get("condition_report_summary", "") or "",
            photo_urls=[p.get("url") for p in item.get("photos", []) if p.get("url")],
            location=item.get("location", "") or "",
            url=item.get("url", "") or "",
            raw=item,
        )
