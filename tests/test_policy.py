"""Hard constraints, review rules, ranking, confidence, and reason codes."""

from __future__ import annotations

from decimal import Decimal

from conftest import offer, pnr

from rebooking_copilot.economics import exchange_economics
from rebooking_copilot.models import CandidateStatus, Confidence, Decision, ReasonCode
from rebooking_copilot.policy import assess_candidate, decide


def assess(booking, candidate, fx, policy):
    economics = exchange_economics(booking, candidate, fx)
    return assess_candidate(booking, candidate, economics, policy)


def test_equivalent_cheaper_offer_is_eligible(fx, policy):
    result = assess(pnr(), offer(), fx, policy)

    assert result.status is CandidateStatus.ELIGIBLE
    assert ReasonCode.EQUIVALENT_PRODUCT_SAVING in result.reason_codes


def test_saving_exactly_equal_to_threshold_is_accepted(fx, policy):
    # totalPaid 500 -> threshold is max(25, 5% of 500) = 25.
    # A 275 fare leaves 500 - 275 - 50 = 175... so price for exactly 25:
    # 500 - price - 50 = 25 -> price = 425.
    exact = offer(price={"amount": Decimal("425.00"), "currency": "USD"})

    result = assess(pnr(), exact, fx, policy)

    assert result.economics.estimated_net_saving == Decimal("25.00")
    assert result.status is CandidateStatus.ELIGIBLE


def test_saving_one_cent_below_threshold_is_rejected(fx, policy):
    just_under = offer(price={"amount": Decimal("425.01"), "currency": "USD"})

    result = assess(pnr(), just_under, fx, policy)

    assert result.status is CandidateStatus.REJECTED
    assert ReasonCode.SAVING_BELOW_THRESHOLD in result.reason_codes


def test_insufficient_seats_rejects_offer_without_splitting_pnr(fx, policy):
    booking = pnr(
        passengers=2,
        ticket={"totalPaid": {"amount": Decimal("1000.00"), "currency": "USD"}},
    )
    single_seat = offer(seatsAvailable=1)

    result = assess(booking, single_seat, fx, policy)

    assert result.status is CandidateStatus.REJECTED
    assert ReasonCode.INSUFFICIENT_SEATS in result.reason_codes


def test_refundability_loss_requires_review(fx, policy):
    booking = pnr(ticket={"refundable": True})

    result = assess(booking, offer(refundable=False), fx, policy)

    assert result.status is CandidateStatus.REVIEW
    assert ReasonCode.REFUNDABILITY_LOST in result.reason_codes


def test_cabin_downgrade_below_ten_percent_saving_is_rejected(fx, policy):
    # 500 - 460 - 50 = -10; also below the saving threshold.
    downgrade = offer(cabin="BASIC_ECONOMY", price={"amount": Decimal("460.00"), "currency": "USD"})

    result = assess(pnr(), downgrade, fx, policy)

    assert result.status is CandidateStatus.REJECTED
    assert ReasonCode.CABIN_DOWNGRADE in result.reason_codes


def test_cabin_downgrade_at_or_above_ten_percent_saving_requires_review(fx, policy):
    # 500 - 300 - 50 = 150 = 30% of totalPaid, comfortably over the 10% gate.
    downgrade = offer(cabin="BASIC_ECONOMY")

    result = assess(pnr(), downgrade, fx, policy)

    assert result.status is CandidateStatus.REVIEW
    assert ReasonCode.CABIN_DOWNGRADE_REVIEW in result.reason_codes


def test_added_stop_is_rejected(fx, policy):
    result = assess(pnr(), offer(stops=1), fx, policy)

    assert result.status is CandidateStatus.REJECTED
    assert ReasonCode.ADDS_STOP in result.reason_codes


def test_reduced_baggage_is_rejected(fx, policy):
    result = assess(pnr(), offer(baggageIncludedPieces=0), fx, policy)

    assert result.status is CandidateStatus.REJECTED
    assert ReasonCode.BAGGAGE_REDUCED in result.reason_codes


def test_approved_carrier_change_stays_eligible(fx, policy):
    booking = pnr(segment={"carrier": "AA", "flightNumber": "AA1"})
    approved = offer(carrier="UA", flightNumber="UA9")

    result = assess(booking, approved, fx, policy)

    assert result.status is CandidateStatus.ELIGIBLE
    assert ReasonCode.CARRIER_NOT_APPROVED not in result.reason_codes


def test_unapproved_carrier_change_requires_review(fx, policy):
    booking = pnr(segment={"carrier": "AA", "flightNumber": "AA1"})
    unapproved = offer(carrier="F9", flightNumber="F9310")

    result = assess(booking, unapproved, fx, policy)

    assert result.status is CandidateStatus.REVIEW
    assert ReasonCode.CARRIER_NOT_APPROVED in result.reason_codes


def test_departure_outside_two_hour_window_is_rejected(fx, policy):
    late = offer(departure="2026-12-01T14:30:00", arrival="2026-12-01T16:30:00")

    result = assess(pnr(), late, fx, policy)

    assert result.status is CandidateStatus.REJECTED
    assert ReasonCode.DEPARTURE_OUTSIDE_WINDOW in result.reason_codes


def test_departure_exactly_two_hours_earlier_is_accepted(fx, policy):
    earlier = offer(departure="2026-12-01T08:00:00", arrival="2026-12-01T10:00:00")

    result = assess(pnr(), earlier, fx, policy)

    assert result.status is CandidateStatus.ELIGIBLE


def test_adjacent_day_offer_outside_two_hours_is_rejected(fx, policy):
    next_day = offer(
        route={"departureDate": "2026-12-02"},
        departure="2026-12-02T10:00:00",
        arrival="2026-12-02T12:00:00",
    )

    result = assess(pnr(), next_day, fx, policy)

    assert result.status is CandidateStatus.REJECTED
    assert ReasonCode.DEPARTURE_OUTSIDE_WINDOW in result.reason_codes


def test_arrival_exactly_two_hours_later_is_accepted(fx, policy):
    boundary = offer(arrival="2026-12-01T14:00:00")

    result = assess(pnr(), boundary, fx, policy)

    assert result.status is CandidateStatus.ELIGIBLE


def test_arrival_more_than_two_hours_later_is_rejected(fx, policy):
    late = offer(arrival="2026-12-01T14:00:01")

    result = assess(pnr(), late, fx, policy)

    assert result.status is CandidateStatus.REJECTED
    assert ReasonCode.ARRIVAL_OUTSIDE_WINDOW in result.reason_codes


def test_same_departure_with_much_longer_journey_is_rejected(fx, policy):
    long_journey = offer(arrival="2026-12-01T19:00:00")

    result = assess(pnr(), long_journey, fx, policy)

    assert result.status is CandidateStatus.REJECTED
    assert ReasonCode.DEPARTURE_OUTSIDE_WINDOW not in result.reason_codes
    assert ReasonCode.ARRIVAL_OUTSIDE_WINDOW in result.reason_codes


def test_higher_future_change_fee_requires_review_but_is_not_charged_now(fx, policy):
    candidate = offer(changeFeePerPassenger={"amount": Decimal("75.00"), "currency": "USD"})

    result = assess(pnr(), candidate, fx, policy)

    assert result.economics.estimated_net_saving == Decimal("150.00")
    assert result.status is CandidateStatus.REVIEW
    assert ReasonCode.FUTURE_CHANGE_FEE_INCREASED in result.reason_codes


def test_equal_or_lower_future_change_fee_remains_eligible(fx, policy):
    equal = assess(pnr(), offer(offerId="EQUAL"), fx, policy)
    lower = assess(
        pnr(),
        offer(
            offerId="LOWER",
            changeFeePerPassenger={"amount": Decimal("25.00"), "currency": "USD"},
        ),
        fx,
        policy,
    )

    assert equal.status is CandidateStatus.ELIGIBLE
    assert lower.status is CandidateStatus.ELIGIBLE


def test_unconvertible_future_change_fee_requires_review_with_evidence(fx, policy):
    candidate = offer(changeFeePerPassenger={"amount": Decimal("25.00"), "currency": "GBP"})

    result = assess(pnr(), candidate, fx, policy)

    assert result.status is CandidateStatus.REVIEW
    assert ReasonCode.FUTURE_CHANGE_FEE_UNAVAILABLE in result.reason_codes
    assert result.economics.future_change_fee_per_passenger.currency == "GBP"
    assert result.economics.future_change_fee_in_booking_currency is None


def test_future_change_fee_is_compared_in_booking_currency(fx, policy):
    booking = pnr(
        ticket={
            "changeFeePerPassenger": {"amount": Decimal("50.00"), "currency": "EUR"},
            "pricePerPassenger": {"amount": Decimal("500.00"), "currency": "EUR"},
            "totalPaid": {"amount": Decimal("500.00"), "currency": "EUR"},
        }
    )
    candidate = offer(
        price={"amount": Decimal("300.00"), "currency": "EUR"},
        changeFeePerPassenger={"amount": Decimal("60.00"), "currency": "USD"},
    )

    result = assess(booking, candidate, fx, policy)

    assert result.economics.future_change_fee_in_booking_currency == Decimal("55.56")
    assert ReasonCode.FUTURE_CHANGE_FEE_INCREASED in result.reason_codes
    assert ReasonCode.CROSS_CURRENCY_ESTIMATE in result.reason_codes


def test_cross_currency_candidate_requires_review(fx, policy):
    booking = pnr(
        ticket={
            "pricePerPassenger": {"amount": Decimal("2200.00"), "currency": "EUR"},
            "totalPaid": {"amount": Decimal("2200.00"), "currency": "EUR"},
            "changeFeePerPassenger": {"amount": Decimal("0.00"), "currency": "EUR"},
        }
    )

    result = assess(
        booking, offer(price={"amount": Decimal("2100.00"), "currency": "USD"}), fx, policy
    )

    assert result.status is CandidateStatus.REVIEW
    assert ReasonCode.CROSS_CURRENCY_ESTIMATE in result.reason_codes


# --------------------------------------------------------------------- ranking


def test_highest_saving_eligible_candidate_is_selected(fx, policy):
    good = offer(offerId="OF-GOOD", price={"amount": Decimal("300.00"), "currency": "USD"})
    better = offer(offerId="OF-BETTER", price={"amount": Decimal("200.00"), "currency": "USD"})

    booking = pnr()
    outcome = decide(
        booking,
        [
            assess_candidate(booking, good, exchange_economics(booking, good, fx), policy),
            assess_candidate(booking, better, exchange_economics(booking, better, fx), policy),
        ],
        policy,
    )

    assert outcome.decision is Decision.REBOOK
    assert outcome.selected_offer_id == "OF-BETTER"
    assert outcome.confidence is Confidence.HIGH


def test_equal_saving_tie_prefers_lower_future_change_fee(fx, policy):
    booking = pnr(ticket={"changeFeePerPassenger": {"amount": "100.00", "currency": "USD"}})
    higher_fee = offer(
        offerId="OF-HIGH-FEE",
        changeFeePerPassenger={"amount": Decimal("100.00"), "currency": "USD"},
    )
    lower_fee = offer(
        offerId="OF-LOW-FEE",
        changeFeePerPassenger={"amount": Decimal("25.00"), "currency": "USD"},
    )

    outcome = decide(
        booking,
        [assess(booking, higher_fee, fx, policy), assess(booking, lower_fee, fx, policy)],
        policy,
    )

    assert outcome.decision is Decision.REBOOK
    assert outcome.selected_offer_id == "OF-LOW-FEE"


def test_review_alternative_is_retained_as_evidence_when_rebooking(fx, policy):
    booking = pnr(ticket={"refundable": True})
    safe = offer(offerId="OF-SAFE", refundable=True)
    richer_but_risky = offer(
        offerId="OF-RISKY", refundable=False, price={"amount": Decimal("100.00"), "currency": "USD"}
    )

    outcome = decide(
        booking,
        [
            assess_candidate(booking, safe, exchange_economics(booking, safe, fx), policy),
            assess_candidate(
                booking, richer_but_risky, exchange_economics(booking, richer_but_risky, fx), policy
            ),
        ],
        policy,
    )

    assert outcome.decision is Decision.REBOOK
    assert outcome.selected_offer_id == "OF-SAFE"
    assert "OF-RISKY" in [alternative.offer_id for alternative in outcome.alternatives]


def test_review_candidate_wins_when_no_eligible_candidate_exists(fx, policy):
    booking = pnr(ticket={"refundable": True})
    risky = offer(offerId="OF-RISKY", refundable=False)

    outcome = decide(
        booking,
        [assess_candidate(booking, risky, exchange_economics(booking, risky, fx), policy)],
        policy,
    )

    assert outcome.decision is Decision.REVIEW
    assert outcome.selected_offer_id == "OF-RISKY"
    assert outcome.confidence is Confidence.MEDIUM


def test_cross_currency_review_is_low_confidence(fx, policy):
    booking = pnr(
        ticket={
            "pricePerPassenger": {"amount": Decimal("2200.00"), "currency": "EUR"},
            "totalPaid": {"amount": Decimal("2200.00"), "currency": "EUR"},
            "changeFeePerPassenger": {"amount": Decimal("0.00"), "currency": "EUR"},
        }
    )
    cross = offer(price={"amount": Decimal("2100.00"), "currency": "USD"})

    outcome = decide(
        booking,
        [assess_candidate(booking, cross, exchange_economics(booking, cross, fx), policy)],
        policy,
    )

    assert outcome.decision is Decision.REVIEW
    assert outcome.confidence is Confidence.LOW


def test_no_candidates_produces_dont_rebook_with_reason_code(fx, policy):
    outcome = decide(pnr(), [], policy)

    assert outcome.decision is Decision.DONT_REBOOK
    assert ReasonCode.NO_MATCHING_OFFERS in outcome.reason_codes
    assert outcome.selected_offer_id is None


def test_dont_rebook_prefers_the_comparable_offer_as_best_evidence(fx, policy):
    """A product-equivalent near miss outranks a cheaper but non-comparable fare."""
    booking = pnr()
    comparable = offer(
        offerId="OF-COMPARABLE", price={"amount": Decimal("470.00"), "currency": "USD"}
    )
    non_comparable = offer(
        offerId="OF-DEGRADED",
        price={"amount": Decimal("440.00"), "currency": "USD"},
        stops=1,
        baggageIncludedPieces=0,
    )

    outcome = decide(
        booking,
        [
            assess_candidate(
                booking, comparable, exchange_economics(booking, comparable, fx), policy
            ),
            assess_candidate(
                booking, non_comparable, exchange_economics(booking, non_comparable, fx), policy
            ),
        ],
        policy,
    )

    assert outcome.decision is Decision.DONT_REBOOK
    assert outcome.best_comparable_offer_id == "OF-COMPARABLE"
    assert outcome.estimated_net_saving == Decimal("-20.00")


def test_monitoring_is_metadata_and_never_a_decision(fx, policy):
    booking = pnr()
    near = offer(price={"amount": Decimal("470.00"), "currency": "USD"})

    outcome = decide(
        booking,
        [assess_candidate(booking, near, exchange_economics(booking, near, fx), policy)],
        policy,
    )

    assert outcome.decision is Decision.DONT_REBOOK
    assert outcome.monitoring is not None
    assert outcome.monitoring.trigger_price_per_passenger == Decimal("425.00")
