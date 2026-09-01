"""Hard constraints, review rules, ranking, confidence, and reason codes.

Every financially or operationally consequential judgement lives here, in
deterministic code, so it is testable and replayable.
"""

from __future__ import annotations

from decimal import Decimal

from .economics import minimum_saving, trigger_price_per_passenger
from .models import (
    CandidateAssessment,
    CandidateEconomics,
    CandidateStatus,
    Confidence,
    ConfidenceReason,
    Decision,
    DecisionOutcome,
    Monitoring,
    Offer,
    Pnr,
    Policy,
    ReasonCode,
    RecheckPriority,
    cabin_rank,
)

SECONDS_PER_HOUR = Decimal("3600")

# Review triggers are client-policy questions, not data-quality problems.
_CLIENT_DECISION_CODES = frozenset(
    {
        ReasonCode.REFUNDABILITY_LOST,
        ReasonCode.CARRIER_NOT_APPROVED,
        ReasonCode.CABIN_DOWNGRADE_REVIEW,
        ReasonCode.FUTURE_CHANGE_FEE_INCREASED,
    }
)
_DATA_UNCERTAINTY_CODES = frozenset(
    {
        ReasonCode.CROSS_CURRENCY_ESTIMATE,
        ReasonCode.FX_RATE_UNAVAILABLE,
        ReasonCode.FUTURE_CHANGE_FEE_UNAVAILABLE,
    }
)


def _hours_apart(left, right) -> Decimal:
    delta = abs((left - right).total_seconds())
    return Decimal(str(delta)) / SECONDS_PER_HOUR


def assess_candidate(
    pnr: Pnr, offer: Offer, economics: CandidateEconomics, policy: Policy
) -> CandidateAssessment:
    """Classify one offer as eligible, review-worthy, or rejected."""
    ticket = pnr.ticket
    total_paid = ticket.total_paid.amount
    saving = economics.estimated_net_saving

    product_rejections: list[ReasonCode] = []
    review_codes: list[ReasonCode] = []

    if offer.seats_available < pnr.passengers:
        # A PNR is indivisible: partial seat availability is not a partial booking.
        product_rejections.append(ReasonCode.INSUFFICIENT_SEATS)

    if _hours_apart(pnr.segment.departure, offer.departure) > policy.schedule_tolerance_hours:
        product_rejections.append(ReasonCode.DEPARTURE_OUTSIDE_WINDOW)

    if _hours_apart(pnr.segment.arrival, offer.arrival) > policy.schedule_tolerance_hours:
        product_rejections.append(ReasonCode.ARRIVAL_OUTSIDE_WINDOW)

    if offer.stops > pnr.segment.stops:
        product_rejections.append(ReasonCode.ADDS_STOP)

    if offer.baggage_included_pieces < ticket.baggage_included_pieces:
        product_rejections.append(ReasonCode.BAGGAGE_REDUCED)

    if cabin_rank(offer.cabin) < cabin_rank(ticket.cabin):
        downgrade_gate = total_paid * policy.cabin_downgrade_min_saving_percent
        if saving >= downgrade_gate:
            review_codes.append(ReasonCode.CABIN_DOWNGRADE_REVIEW)
        else:
            product_rejections.append(ReasonCode.CABIN_DOWNGRADE)

    if ticket.refundable and not offer.refundable:
        review_codes.append(ReasonCode.REFUNDABILITY_LOST)

    if offer.carrier != pnr.segment.carrier and offer.carrier not in policy.approved_carriers:
        review_codes.append(ReasonCode.CARRIER_NOT_APPROVED)

    if economics.fare_conversion is not None or economics.future_change_fee_conversion is not None:
        # The MVP rate is illustrative, so it may inform but never authorize.
        review_codes.append(ReasonCode.CROSS_CURRENCY_ESTIMATE)

    future_fee = economics.future_change_fee_in_booking_currency
    if future_fee is None:
        review_codes.append(ReasonCode.FUTURE_CHANGE_FEE_UNAVAILABLE)
    else:
        current_fee = economics.current_exchange_fee_total / pnr.passengers
        if future_fee > current_fee:
            review_codes.append(ReasonCode.FUTURE_CHANGE_FEE_INCREASED)

    threshold = minimum_saving(total_paid, policy)
    meets_threshold = saving >= threshold

    reason_codes = list(product_rejections)
    if not meets_threshold:
        reason_codes.append(ReasonCode.SAVING_BELOW_THRESHOLD)
    reason_codes.extend(review_codes)

    if product_rejections or not meets_threshold:
        status = CandidateStatus.REJECTED
    elif review_codes:
        status = CandidateStatus.REVIEW
    else:
        status = CandidateStatus.ELIGIBLE
        reason_codes.append(ReasonCode.EQUIVALENT_PRODUCT_SAVING)

    return CandidateAssessment(
        offer_id=offer.offer_id,
        status=status,
        reason_codes=tuple(reason_codes),
        economics=economics,
        # Comparable means "same usable product"; failing only on price still counts.
        is_comparable=not product_rejections,
    )


def _confidence_for(
    reason_codes: tuple[ReasonCode, ...], decision: Decision
) -> tuple[Confidence, tuple[ConfidenceReason, ...]]:
    if any(code in _DATA_UNCERTAINTY_CODES for code in reason_codes):
        return Confidence.LOW, (ConfidenceReason.ILLUSTRATIVE_FX_RATE,)
    if any(code in _CLIENT_DECISION_CODES for code in reason_codes):
        return Confidence.MEDIUM, (ConfidenceReason.CLIENT_DECISION_REQUIRED,)
    if decision is Decision.DONT_REBOOK:
        return Confidence.HIGH, (
            ConfidenceReason.COMPLETE_SAME_CURRENCY_DATA,
            ConfidenceReason.NO_ACCEPTABLE_OFFER,
        )
    return Confidence.HIGH, (ConfidenceReason.COMPLETE_SAME_CURRENCY_DATA,)


def _monitoring(pnr: Pnr, best: CandidateAssessment | None, policy: Policy) -> Monitoring:
    """Scheduling metadata for a declined booking: when to look again, and at what price."""
    if best is None or not best.is_comparable:
        return Monitoring(recheck_priority=RecheckPriority.NORMAL)

    threshold = minimum_saving(pnr.ticket.total_paid.amount, policy)
    trigger = trigger_price_per_passenger(
        total_paid=pnr.ticket.total_paid.amount,
        exchange_fee_total=best.economics.current_exchange_fee_total,
        minimum_saving=threshold,
        passengers=pnr.passengers,
    )
    near_gate = max(
        Decimal("0"),
        threshold - pnr.ticket.total_paid.amount * policy.near_threshold_percentage_points,
    )
    priority = (
        RecheckPriority.ELEVATED
        if best.economics.estimated_net_saving >= near_gate
        else RecheckPriority.NORMAL
    )
    return Monitoring(
        recheck_priority=priority,
        trigger_price_per_passenger=trigger if trigger >= 0 else None,
        currency=pnr.ticket.total_paid.currency if trigger >= 0 else None,
    )


def decide(pnr: Pnr, assessments: list[CandidateAssessment], policy: Policy) -> DecisionOutcome:
    """Select the best candidate and assign the decision, confidence, and reasons."""
    candidates = tuple(assessments)

    if not candidates:
        return DecisionOutcome(
            decision=Decision.DONT_REBOOK,
            selected_offer_id=None,
            estimated_net_saving=None,
            currency=pnr.ticket.total_paid.currency,
            confidence=Confidence.HIGH,
            confidence_reasons=(ConfidenceReason.NO_ACCEPTABLE_OFFER,),
            reason_codes=(ReasonCode.NO_MATCHING_OFFERS,),
            monitoring=Monitoring(recheck_priority=RecheckPriority.NORMAL),
        )

    def ranking_key(item: CandidateAssessment) -> tuple[Decimal, Decimal, str]:
        future_fee = item.economics.future_change_fee_in_booking_currency
        return (
            -item.economics.estimated_net_saving,
            future_fee if future_fee is not None else Decimal("Infinity"),
            item.offer_id,
        )

    eligible = sorted(
        [item for item in candidates if item.status is CandidateStatus.ELIGIBLE],
        key=ranking_key,
    )
    reviewable = sorted(
        [item for item in candidates if item.status is CandidateStatus.REVIEW],
        key=ranking_key,
    )

    if eligible:
        selected = eligible[0]
        decision = Decision.REBOOK
        # Surface review options that a human might still prefer or should know about.
        alternatives = tuple(
            item
            for item in reviewable
            if item.economics.estimated_net_saving > selected.economics.estimated_net_saving
        ) + tuple(eligible[1:])
    elif reviewable:
        selected = reviewable[0]
        decision = Decision.REVIEW
        alternatives = tuple(reviewable[1:])
    else:
        # Nothing actionable: rank comparable near-misses above degraded products.
        ranked = sorted(candidates, key=lambda item: (not item.is_comparable, *ranking_key(item)))
        best = ranked[0]
        confidence, confidence_reasons = _confidence_for(best.reason_codes, Decision.DONT_REBOOK)
        return DecisionOutcome(
            decision=Decision.DONT_REBOOK,
            selected_offer_id=None,
            estimated_net_saving=best.economics.estimated_net_saving,
            currency=best.economics.currency,
            confidence=confidence,
            confidence_reasons=confidence_reasons,
            reason_codes=best.reason_codes,
            candidates=candidates,
            alternatives=tuple(ranked[1:]),
            best_comparable_offer_id=best.offer_id if best.is_comparable else None,
            monitoring=_monitoring(pnr, best, policy),
        )

    confidence, confidence_reasons = _confidence_for(selected.reason_codes, decision)
    return DecisionOutcome(
        decision=decision,
        selected_offer_id=selected.offer_id,
        estimated_net_saving=selected.economics.estimated_net_saving,
        currency=selected.economics.currency,
        confidence=confidence,
        confidence_reasons=confidence_reasons,
        reason_codes=selected.reason_codes,
        candidates=candidates,
        alternatives=alternatives,
        best_comparable_offer_id=selected.offer_id if selected.is_comparable else None,
    )
