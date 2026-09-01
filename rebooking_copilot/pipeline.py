"""Per-PNR orchestration with record-level fault isolation.

The batch fails only on an unusable envelope. Every other failure is converted
into an explicit outcome for the affected record.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from .economics import FxRateUnavailable, FxTable, exchange_economics
from .matching import candidate_offers, index_offers, pnr_journey_key
from .models import (
    BatchResult,
    CandidateAssessment,
    CandidateWarning,
    Confidence,
    ConfidenceReason,
    Decision,
    DecisionOutcome,
    EvaluationMode,
    Explanation,
    Money,
    Monitoring,
    Offer,
    Pnr,
    Policy,
    QuarantinedOffer,
    ReasonCode,
    RecheckPriority,
    Recommendation,
    RecommendationFacts,
    WarningCode,
)
from .policy import assess_candidate, decide
from .reasoning import ExplanationGenerator, TemplateExplanationGenerator, build_facts
from .validation import (
    validate_fares_envelope,
    validate_offers,
    validate_pnr,
    validate_pnr_envelope,
)

logger = logging.getLogger(__name__)

Clock = Callable[[], datetime]

_EXPLAINED_DECISIONS = frozenset({Decision.REBOOK, Decision.REVIEW})


def _unresolved_outcome(
    reason: ReasonCode,
    confidence_reason: ConfidenceReason,
    *,
    candidates: tuple[CandidateAssessment, ...] = (),
    warning_codes: tuple[WarningCode, ...] = (),
    candidate_warnings: tuple[CandidateWarning, ...] = (),
) -> DecisionOutcome:
    """A booking we cannot price safely: hand it to a human with the cause attached."""
    return DecisionOutcome(
        decision=Decision.REVIEW,
        selected_offer_id=None,
        estimated_net_saving=None,
        currency=None,
        confidence=Confidence.LOW,
        confidence_reasons=(confidence_reason,),
        reason_codes=(reason,),
        warning_codes=warning_codes,
        candidate_warnings=candidate_warnings,
        candidates=candidates,
        alternatives=candidates,
        monitoring=Monitoring(recheck_priority=RecheckPriority.NORMAL),
    )


def _relevant_quarantine(pnr: Pnr, quarantined: list[QuarantinedOffer]) -> list[QuarantinedOffer]:
    """Return unusable offers that can be attributed to this booking's journey."""
    origin, destination, departure_date = pnr_journey_key(pnr)
    wanted = {(departure_date + timedelta(days=offset)).isoformat() for offset in (-1, 0, 1)}
    return [
        item
        for item in quarantined
        if item.origin == origin
        and item.destination == destination
        and item.departure_date in wanted
    ]


def evaluate_pnr(
    pnr: Pnr,
    offers: list[Offer],
    quarantined: list[QuarantinedOffer],
    policy: Policy,
    fx: FxTable,
) -> DecisionOutcome:
    """Deterministic evaluation of one validated booking."""
    relevant_quarantine = _relevant_quarantine(pnr, quarantined)
    malformed_warnings = tuple(
        CandidateWarning(
            offer_id=item.offer_id,
            warning_codes=(WarningCode.OFFER_DATA_INCOMPLETE,),
        )
        for item in relevant_quarantine
    )
    if not offers:
        if relevant_quarantine:
            return _unresolved_outcome(
                ReasonCode.ALL_OFFERS_INVALID,
                ConfidenceReason.DATA_VALIDATION_FAILURE,
                warning_codes=(WarningCode.OFFER_DATA_INCOMPLETE,),
                candidate_warnings=malformed_warnings,
            )
        return decide(pnr, [], policy)

    assessments: list[CandidateAssessment] = []
    fx_failures: list[Offer] = []
    for offer in offers:
        try:
            economics = exchange_economics(pnr, offer, fx)
        except FxRateUnavailable:
            # Never invent a rate; the booking goes to a human instead.
            fx_failures.append(offer)
            continue
        assessments.append(assess_candidate(pnr, offer, economics, policy))

    evidence = tuple(assessments)
    fx_warnings = tuple(
        CandidateWarning(
            offer_id=offer.offer_id,
            warning_codes=(WarningCode.FX_RATE_UNAVAILABLE,),
        )
        for offer in fx_failures
    )
    future_fee_warnings = tuple(
        CandidateWarning(
            offer_id=assessment.offer_id,
            warning_codes=(WarningCode.FX_RATE_UNAVAILABLE,),
        )
        for assessment in assessments
        if assessment.economics.future_change_fee_in_booking_currency is None
    )
    if not assessments and fx_failures:
        return _unresolved_outcome(
            ReasonCode.FX_RATE_UNAVAILABLE,
            ConfidenceReason.ILLUSTRATIVE_FX_RATE,
            candidates=evidence,
            warning_codes=(WarningCode.FX_RATE_UNAVAILABLE,),
            candidate_warnings=fx_warnings,
        )

    outcome = decide(pnr, assessments, policy)
    warning_codes = ((WarningCode.OFFER_DATA_INCOMPLETE,) if malformed_warnings else ()) + (
        (WarningCode.FX_RATE_UNAVAILABLE,) if fx_warnings or future_fee_warnings else ()
    )
    return outcome.model_copy(
        update={
            "warning_codes": warning_codes,
            "candidate_warnings": malformed_warnings + fx_warnings + future_fee_warnings,
        }
    )


def _recommendation(
    pnr_id: str,
    outcome: DecisionOutcome,
    explanation: Explanation,
    *,
    generated_at: datetime,
    evaluated_as_of: datetime | None,
    policy: Policy,
    validation_error: str | None = None,
) -> Recommendation:
    saving = (
        Money(amount=outcome.estimated_net_saving, currency=outcome.currency)
        if outcome.estimated_net_saving is not None and outcome.currency is not None
        else None
    )
    return Recommendation(
        pnr=pnr_id,
        decision=outcome.decision,
        selected_offer_id=outcome.selected_offer_id,
        estimated_net_saving=saving,
        confidence=outcome.confidence,
        confidence_reasons=outcome.confidence_reasons,
        reason_codes=outcome.reason_codes,
        warning_codes=outcome.warning_codes,
        candidate_warnings=outcome.candidate_warnings,
        explanation=explanation,
        best_comparable_offer_id=outcome.best_comparable_offer_id,
        monitoring=outcome.monitoring,
        candidates=outcome.candidates,
        alternatives=outcome.alternatives,
        evaluated_as_of=evaluated_as_of,
        generated_at=generated_at,
        fx_rate_source=policy.fx_rate_source,
        policy_version=policy.version,
        validation_error=validation_error,
    )


def run_batch(
    pnr_document: Any,
    fares_document: Any,
    policy: Policy,
    *,
    clock: Clock,
    explainer: ExplanationGenerator | None = None,
    fx: FxTable | None = None,
) -> BatchResult:
    """Evaluate every PNR in the document. Raises `EnvelopeError` on bad envelopes."""
    explainer = explainer or TemplateExplanationGenerator()
    template = TemplateExplanationGenerator()
    fx = fx or FxTable(rates=policy.fx_rates, source=policy.fx_rate_source)

    raw_pnrs = validate_pnr_envelope(pnr_document)
    raw_offers, evaluated_as_of = validate_fares_envelope(fares_document)
    offers, quarantined = validate_offers(raw_offers)
    index = index_offers(offers)

    generated_at = clock()
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=timezone.utc)
    else:
        generated_at = generated_at.astimezone(timezone.utc)
    recommendations: list[Recommendation] = []
    for position, raw in enumerate(raw_pnrs, start=1):
        raw_reference = raw.get("pnr") if isinstance(raw, dict) else None
        reference = (
            raw_reference
            if isinstance(raw_reference, str) and raw_reference.strip()
            else f"UNKNOWN-{position}"
        )
        record, error = validate_pnr(raw)

        if record is None:
            outcome = DecisionOutcome(
                decision=Decision.REVIEW,
                selected_offer_id=None,
                estimated_net_saving=None,
                currency=None,
                confidence=Confidence.LOW,
                confidence_reasons=(ConfidenceReason.DATA_VALIDATION_FAILURE,),
                reason_codes=(ReasonCode.PNR_VALIDATION_FAILED,),
            )
            facts = None
        else:
            try:
                outcome = evaluate_pnr(
                    record, candidate_offers(record, index), quarantined, policy, fx
                )
                facts = build_facts(record, outcome, policy)
            except Exception as unexpected:  # noqa: BLE001 - isolate one record, keep the batch
                logger.exception("unexpected failure evaluating PNR %s", reference)
                outcome = DecisionOutcome(
                    decision=Decision.REVIEW,
                    selected_offer_id=None,
                    estimated_net_saving=None,
                    currency=None,
                    confidence=Confidence.LOW,
                    confidence_reasons=(ConfidenceReason.DATA_VALIDATION_FAILURE,),
                    reason_codes=(ReasonCode.PIPELINE_ERROR,),
                )
                facts = None
                error = f"{type(unexpected).__name__}: {unexpected}"

        if facts is None:
            facts = _placeholder_facts(outcome, policy)

        # Spend model calls only where a human reads the nuance.
        generator = explainer if outcome.decision in _EXPLAINED_DECISIONS else template
        try:
            explanation = generator.generate(facts)
        except Exception:  # noqa: BLE001 - custom explainers must not fail a batch
            logger.exception("explanation generator failed for PNR %s", reference)
            explanation = template.generate(facts)

        recommendations.append(
            _recommendation(
                reference,
                outcome,
                explanation,
                generated_at=generated_at,
                evaluated_as_of=evaluated_as_of,
                policy=policy,
                validation_error=error,
            )
        )

    counts: dict[str, int] = {decision.value: 0 for decision in Decision}
    for item in recommendations:
        counts[item.decision.value] += 1

    return BatchResult(
        evaluation_mode=EvaluationMode.HISTORICAL_SNAPSHOT,
        generated_at=generated_at,
        evaluated_as_of=evaluated_as_of,
        fare_snapshot_captured_at=evaluated_as_of,
        policy_version=policy.version,
        fx_rate_source=policy.fx_rate_source,
        pnrs_evaluated=len(recommendations),
        quarantined_offers=len(quarantined),
        decision_counts=counts,
        recommendations=tuple(recommendations),
    )


def _placeholder_facts(outcome: DecisionOutcome, policy: Policy) -> RecommendationFacts:
    """Facts for a record that never reached candidate evaluation."""
    return RecommendationFacts(
        decision=outcome.decision,
        selected_offer_id=None,
        estimated_net_saving=None,
        currency=None,
        passengers=0,
        new_fare_total=None,
        current_exchange_fee_total=None,
        future_change_fee_per_passenger=None,
        confidence=outcome.confidence,
        confidence_reasons=outcome.confidence_reasons,
        reason_codes=outcome.reason_codes,
        policy_version=policy.version,
    )
