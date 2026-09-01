"""Boundary validation: envelopes, PNR records, and offer quarantine."""

from __future__ import annotations

from decimal import Decimal

import pytest
from conftest import raw_offer, raw_pnr
from pydantic import ValidationError

from rebooking_copilot.models import Policy
from rebooking_copilot.validation import (
    EnvelopeError,
    validate_fares_envelope,
    validate_offers,
    validate_pnr,
    validate_pnr_envelope,
)


def test_valid_pnr_passes_validation():
    record, error = validate_pnr(raw_pnr())

    assert error is None
    assert record is not None and record.pnr == "TEST01"


def test_totals_inconsistent_with_per_passenger_price_is_rejected():
    record, error = validate_pnr(
        raw_pnr(
            passengers=2,
            ticket={"totalPaid": {"amount": Decimal("999.00"), "currency": "USD"}},
        )
    )

    assert record is None
    assert error is not None and "totalPaid" in error


def test_zero_passengers_is_rejected():
    record, error = validate_pnr(raw_pnr(passengers=0))

    assert record is None
    assert error is not None


def test_negative_amount_is_rejected():
    record, error = validate_pnr(
        raw_pnr(ticket={"changeFeePerPassenger": {"amount": Decimal("-1.00"), "currency": "USD"}})
    )

    assert record is None
    assert error is not None


def test_multi_segment_itinerary_is_out_of_scope_and_rejected():
    extra_leg = raw_pnr()
    extra_leg["itinerary"].append(dict(extra_leg["itinerary"][0]))

    record, error = validate_pnr(extra_leg)

    assert record is None
    assert error is not None and "segment" in error.lower()


def test_timezone_aware_departure_is_rejected_as_ambiguous():
    record, error = validate_pnr(raw_pnr(segment={"departure": "2026-12-01T10:00:00+02:00"}))

    assert record is None
    assert error is not None


def test_malformed_offer_is_quarantined_and_valid_offers_survive():
    good = raw_offer(offerId="OF-GOOD")
    bad = raw_offer(offerId="OF-BAD", seatsAvailable=-3)

    offers, quarantined = validate_offers([good, bad])

    assert [item.offer_id for item in offers] == ["OF-GOOD"]
    assert len(quarantined) == 1
    assert quarantined[0].offer_id == "OF-BAD"
    assert quarantined[0].error


def test_offer_missing_required_field_is_quarantined_with_identifier():
    broken = raw_offer(offerId="OF-BROKEN")
    del broken["price"]

    offers, quarantined = validate_offers([broken])

    assert offers == []
    assert quarantined[0].offer_id == "OF-BROKEN"


def test_offer_without_identifier_is_still_quarantined():
    anonymous = raw_offer()
    del anonymous["offerId"]

    offers, quarantined = validate_offers([anonymous])

    assert offers == []
    assert quarantined[0].offer_id is None


def test_malformed_top_level_envelope_raises():
    with pytest.raises(EnvelopeError):
        validate_pnr_envelope({"not_pnrs": []})


def test_envelope_with_non_list_records_raises():
    with pytest.raises(EnvelopeError):
        validate_pnr_envelope({"pnrs": {"pnr": "X"}})


def test_valid_envelope_returns_raw_records():
    records = validate_pnr_envelope({"pnrs": [raw_pnr()]})

    assert len(records) == 1


def test_duplicate_pnr_identifiers_fail_the_envelope_visibly():
    with pytest.raises(EnvelopeError, match="duplicate PNR identifier.*TEST01"):
        validate_pnr_envelope({"pnrs": [raw_pnr(), raw_pnr()]})


def test_duplicate_identifiers_are_compared_after_trimming_whitespace():
    with pytest.raises(EnvelopeError, match="duplicate PNR identifier.*TEST01"):
        validate_pnr_envelope({"pnrs": [raw_pnr(), raw_pnr(pnr=" TEST01 ")]})


def test_duplicate_offer_identifiers_fail_the_envelope_visibly():
    with pytest.raises(EnvelopeError, match="duplicate offer identifier.*OF-TEST"):
        validate_fares_envelope(
            {
                "capturedAt": "2026-07-08T09:00:00Z",
                "offers": [raw_offer(), raw_offer()],
            }
        )


def test_fare_snapshot_timestamp_must_be_valid_and_timezone_aware():
    with pytest.raises(EnvelopeError):
        validate_fares_envelope({"capturedAt": "not-a-date", "offers": []})
    with pytest.raises(EnvelopeError):
        validate_fares_envelope({"capturedAt": "2026-07-08T09:00:00", "offers": []})


def test_offer_route_date_must_match_departure_timestamp():
    offers, quarantined = validate_offers([raw_offer(route={"departureDate": "2026-12-02"})])

    assert offers == []
    assert len(quarantined) == 1


def test_tiny_negative_amount_is_rejected_before_cent_rounding():
    record, error = validate_pnr(
        raw_pnr(ticket={"changeFeePerPassenger": {"amount": "-0.001", "currency": "USD"}})
    )

    assert record is None
    assert error is not None


def test_policy_rejects_negative_thresholds_and_fx_rates(policy):
    raw = policy.model_dump(by_alias=True)
    raw["minSavingAbsolute"] = "-1"
    with pytest.raises(ValidationError):
        Policy.model_validate(raw)

    raw = policy.model_dump(by_alias=True)
    raw["fxRates"] = {"EUR/USD": "-1.08"}
    with pytest.raises(ValidationError):
        Policy.model_validate(raw)
