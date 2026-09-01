"""Shared builders and fixtures.

Builders return raw dictionaries shaped like the supplied fixture JSON so tests
exercise the same validation boundary the CLI does.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from rebooking_copilot.economics import FxTable
from rebooking_copilot.models import Offer, Pnr, Policy

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"

FIXED_NOW = datetime(2026, 7, 8, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def policy() -> Policy:
    return Policy.load(ROOT / "policy.json")


@pytest.fixture
def fx(policy: Policy) -> FxTable:
    return FxTable(rates=policy.fx_rates, source=policy.fx_rate_source)


@pytest.fixture
def clock():
    return lambda: FIXED_NOW


def raw_pnr(**overrides: Any) -> dict[str, Any]:
    """A single-passenger, single-segment economy booking."""
    record: dict[str, Any] = {
        "pnr": "TEST01",
        "passengers": 1,
        "itinerary": [
            {
                "origin": "AAA",
                "destination": "BBB",
                "departure": "2026-12-01T10:00:00",
                "arrival": "2026-12-01T12:00:00",
                "carrier": "XX",
                "flightNumber": "XX100",
                "stops": 0,
                "cabin": "ECONOMY",
                "fareBasis": "YFLEX",
            }
        ],
        "ticket": {
            "pricePerPassenger": {"amount": Decimal("500.00"), "currency": "USD"},
            "totalPaid": {"amount": Decimal("500.00"), "currency": "USD"},
            "cabin": "ECONOMY",
            "refundable": False,
            "changeFeePerPassenger": {"amount": Decimal("50.00"), "currency": "USD"},
            "baggageIncludedPieces": 1,
        },
    }
    ticket_overrides = overrides.pop("ticket", {})
    segment_overrides = overrides.pop("segment", {})
    record.update(overrides)
    record["ticket"].update(ticket_overrides)
    record["itinerary"][0].update(segment_overrides)
    return record


def raw_offer(**overrides: Any) -> dict[str, Any]:
    """Same product as `raw_pnr`, priced at 300.00 USD."""
    record: dict[str, Any] = {
        "offerId": "OF-TEST",
        "route": {"origin": "AAA", "destination": "BBB", "departureDate": "2026-12-01"},
        "carrier": "XX",
        "flightNumber": "XX100",
        "departure": "2026-12-01T10:00:00",
        "arrival": "2026-12-01T12:00:00",
        "stops": 0,
        "cabin": "ECONOMY",
        "fareBasis": "YSAVER",
        "price": {"amount": Decimal("300.00"), "currency": "USD"},
        "refundable": False,
        "changeFeePerPassenger": {"amount": Decimal("50.00"), "currency": "USD"},
        "baggageIncludedPieces": 1,
        "seatsAvailable": 5,
    }
    route_overrides = overrides.pop("route", {})
    record.update(overrides)
    record["route"].update(route_overrides)
    return record


def pnr(**overrides: Any) -> Pnr:
    return Pnr.model_validate(raw_pnr(**overrides))


def offer(**overrides: Any) -> Offer:
    return Offer.model_validate(raw_offer(**overrides))


def load_supplied_fixtures() -> tuple[dict[str, Any], dict[str, Any]]:
    with open(FIXTURES / "pnrs.json", encoding="utf-8") as handle:
        pnrs = json.load(handle, parse_float=Decimal)
    with open(FIXTURES / "fares_feed.json", encoding="utf-8") as handle:
        fares = json.load(handle, parse_float=Decimal)
    return pnrs, fares
