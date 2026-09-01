"""Boundary validation.

A malformed envelope stops the run. A malformed PNR becomes `REVIEW`. A malformed
offer is quarantined so the remaining offers can still be evaluated.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import ValidationError

from .models import Offer, Pnr, QuarantinedOffer


class EnvelopeError(Exception):
    """The top-level document is unusable; the batch cannot run."""


def _reject_duplicate_identifiers(records: list[Any], field: str, record_name: str) -> None:
    """Fail a batch whose identifiers could not support an unambiguous audit trail."""
    seen: set[str] = set()
    duplicates: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            continue
        identifier = record.get(field)
        if not isinstance(identifier, str) or not identifier.strip():
            continue
        canonical = identifier.strip()
        if canonical in seen:
            duplicates.add(canonical)
        seen.add(canonical)
    if duplicates:
        joined = ", ".join(sorted(duplicates))
        raise EnvelopeError(f"duplicate {record_name} identifier(s): {joined}")


def _describe(error: ValidationError) -> str:
    parts = []
    for item in error.errors():
        location = ".".join(str(piece) for piece in item["loc"]) or "record"
        parts.append(f"{location}: {item['msg']}")
    return "; ".join(parts)


def validate_pnr_envelope(document: Any) -> list[dict[str, Any]]:
    if not isinstance(document, dict):
        raise EnvelopeError("PNR document must be a JSON object")
    records = document.get("pnrs")
    if not isinstance(records, list):
        raise EnvelopeError("PNR document must contain a 'pnrs' array")
    _reject_duplicate_identifiers(records, "pnr", "PNR")
    return records


def validate_fares_envelope(document: Any) -> tuple[list[dict[str, Any]], datetime]:
    if not isinstance(document, dict):
        raise EnvelopeError("fares document must be a JSON object")
    offers = document.get("offers")
    if not isinstance(offers, list):
        raise EnvelopeError("fares document must contain an 'offers' array")
    _reject_duplicate_identifiers(offers, "offerId", "offer")
    captured_at = document.get("capturedAt")
    if not isinstance(captured_at, str):
        raise EnvelopeError("fares document must contain a string 'capturedAt'")
    try:
        parsed = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise EnvelopeError("fares document 'capturedAt' must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise EnvelopeError("fares document 'capturedAt' must include a UTC offset")
    return offers, parsed


def validate_pnr(record: Any) -> tuple[Pnr | None, str | None]:
    if not isinstance(record, dict):
        return None, "PNR record must be a JSON object"
    try:
        return Pnr.model_validate(record), None
    except ValidationError as error:
        return None, _describe(error)


def validate_offers(records: list[Any]) -> tuple[list[Offer], list[QuarantinedOffer]]:
    valid: list[Offer] = []
    quarantined: list[QuarantinedOffer] = []

    for record in records:
        if not isinstance(record, dict):
            quarantined.append(
                QuarantinedOffer(offer_id=None, error="offer record must be a JSON object")
            )
            continue
        try:
            valid.append(Offer.model_validate(record))
        except ValidationError as error:
            route = record.get("route") if isinstance(record.get("route"), dict) else {}
            quarantined.append(
                QuarantinedOffer(
                    offer_id=record.get("offerId"),
                    error=_describe(error),
                    origin=route.get("origin"),
                    destination=route.get("destination"),
                    departure_date=route.get("departureDate"),
                )
            )
    return valid, quarantined
