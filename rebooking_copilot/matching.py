"""Journey-key indexing and candidate lookup.

Matching narrows to the same route and the booking's local departure date plus
its adjacent dates. The schedule window is applied later as a policy rule so
cross-midnight offers are found and out-of-window offers remain in the evidence.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from .models import Offer, Pnr

JourneyKey = tuple[str, str, date]


def journey_key(origin: str, destination: str, departure_date: date) -> JourneyKey:
    return (origin, destination, departure_date)


def offer_journey_key(offer: Offer) -> JourneyKey:
    return journey_key(offer.route.origin, offer.route.destination, offer.route.departure_date)


def pnr_journey_key(pnr: Pnr) -> JourneyKey:
    segment = pnr.segment
    return journey_key(segment.origin, segment.destination, segment.departure.date())


def index_offers(offers: list[Offer]) -> dict[JourneyKey, list[Offer]]:
    index: dict[JourneyKey, list[Offer]] = defaultdict(list)
    for offer in offers:
        index[offer_journey_key(offer)].append(offer)
    return dict(index)


def candidate_offers(pnr: Pnr, index: dict[JourneyKey, list[Offer]]) -> list[Offer]:
    origin, destination, departure_date = pnr_journey_key(pnr)
    candidates: list[Offer] = []
    for day_offset in (-1, 0, 1):
        key = journey_key(origin, destination, departure_date + timedelta(days=day_offset))
        candidates.extend(index.get(key, ()))
    return candidates
