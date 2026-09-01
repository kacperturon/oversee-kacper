"""Explanation layer.

The model is an optional writer. It receives locked facts and returns prose; it
never supplies a structured field. Any failure falls back to the template, and a
failure here never fails a PNR or the batch.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
from collections.abc import Callable
from decimal import Decimal
from typing import Any, Protocol

from .models import (
    Decision,
    DecisionOutcome,
    Explanation,
    ExplanationSource,
    LlmExplanationPayload,
    Pnr,
    Policy,
    ReasonCode,
    RecommendationFacts,
)
from .prompt import PROMPT_VERSION, SYSTEM_PROMPT

logger = logging.getLogger(__name__)

__all__ = [
    "PROMPT_VERSION",
    "SYSTEM_PROMPT",
    "ExplanationGenerator",
    "LiteLLMExplanationGenerator",
    "TemplateExplanationGenerator",
    "build_facts",
    "load_litellm_completion",
]

_REASON_TEXT: dict[ReasonCode, str] = {
    ReasonCode.EQUIVALENT_PRODUCT_SAVING: (
        "the fare is equivalent on cabin, baggage, stops, and schedule"
    ),
    ReasonCode.SAVING_BELOW_THRESHOLD: "the saving does not clear the client's minimum",
    ReasonCode.INSUFFICIENT_SEATS: "there are not enough seats for every passenger",
    ReasonCode.DEPARTURE_OUTSIDE_WINDOW: "the departure falls outside the accepted time window",
    ReasonCode.ARRIVAL_OUTSIDE_WINDOW: "the arrival falls outside the accepted time window",
    ReasonCode.ADDS_STOP: "it adds a stop to a direct journey",
    ReasonCode.BAGGAGE_REDUCED: "included baggage would be reduced",
    ReasonCode.CABIN_DOWNGRADE: "it downgrades the cabin without a large enough saving",
    ReasonCode.CABIN_DOWNGRADE_REVIEW: "it downgrades the cabin",
    ReasonCode.REFUNDABILITY_LOST: "the ticket would stop being refundable",
    ReasonCode.CARRIER_NOT_APPROVED: "the carrier is not on the client's approved list",
    ReasonCode.CROSS_CURRENCY_ESTIMATE: "the comparison relies on an illustrative exchange rate",
    ReasonCode.FUTURE_CHANGE_FEE_INCREASED: (
        "the resulting ticket would have a higher future change fee"
    ),
    ReasonCode.FUTURE_CHANGE_FEE_UNAVAILABLE: (
        "the resulting ticket's future change fee cannot be converted"
    ),
    ReasonCode.FX_RATE_UNAVAILABLE: "no exchange rate is configured for this currency",
    ReasonCode.NO_MATCHING_OFFERS: "no fares were available for this journey",
    ReasonCode.ALL_OFFERS_INVALID: "every fare for this journey failed validation",
    ReasonCode.PNR_VALIDATION_FAILED: "the booking record failed validation",
    ReasonCode.PIPELINE_ERROR: "an unexpected error interrupted this evaluation",
}

_REVIEW_QUESTION: dict[ReasonCode, str] = {
    ReasonCode.REFUNDABILITY_LOST: (
        "Confirm the client accepts losing refundability in exchange for this saving."
    ),
    ReasonCode.CROSS_CURRENCY_ESTIMATE: (
        "Confirm the live exchange rate and settlement currency before acting."
    ),
    ReasonCode.CABIN_DOWNGRADE_REVIEW: (
        "Confirm the traveller accepts the lower cabin for this saving."
    ),
    ReasonCode.CARRIER_NOT_APPROVED: ("Confirm the client approves travel on this carrier."),
    ReasonCode.FX_RATE_UNAVAILABLE: (
        "Supply an authoritative rate for this currency; no saving can be estimated without one."
    ),
    ReasonCode.FUTURE_CHANGE_FEE_INCREASED: (
        "Confirm the client accepts the resulting ticket's higher future change fee."
    ),
    ReasonCode.FUTURE_CHANGE_FEE_UNAVAILABLE: (
        "Supply an authoritative rate to compare the resulting ticket's future change fee."
    ),
    ReasonCode.PNR_VALIDATION_FAILED: (
        "Correct the booking record before this PNR can be evaluated."
    ),
    ReasonCode.ALL_OFFERS_INVALID: (
        "Investigate the fare feed; every offer for this journey failed validation."
    ),
    ReasonCode.PIPELINE_ERROR: ("Re-run this PNR after investigating the recorded error."),
}


class ExplanationGenerator(Protocol):
    def generate(self, facts: RecommendationFacts) -> Explanation: ...


Completion = Callable[..., Any]


def load_litellm_completion() -> Completion:
    """Import LiteLLM only when a configured model call is actually attempted."""
    from litellm import completion

    return completion


def build_facts(pnr: Pnr, outcome: DecisionOutcome, policy: Policy) -> RecommendationFacts:
    """Reduce the recommendation to the minimum the model needs.

    Raw itinerary, fare basis, and traveller detail are deliberately excluded.
    """
    selected = next(
        (item for item in outcome.candidates if item.offer_id == outcome.selected_offer_id),
        None,
    )
    return RecommendationFacts(
        decision=outcome.decision,
        selected_offer_id=outcome.selected_offer_id,
        estimated_net_saving=outcome.estimated_net_saving,
        currency=outcome.currency,
        passengers=pnr.passengers,
        new_fare_total=selected.economics.new_fare_total if selected else None,
        current_exchange_fee_total=(
            selected.economics.current_exchange_fee_total if selected else None
        ),
        future_change_fee_per_passenger=(
            selected.economics.future_change_fee_per_passenger if selected else None
        ),
        confidence=outcome.confidence,
        confidence_reasons=outcome.confidence_reasons,
        reason_codes=outcome.reason_codes,
        best_comparable_offer_id=outcome.best_comparable_offer_id,
        monitoring_trigger_price=(
            outcome.monitoring.trigger_price_per_passenger if outcome.monitoring else None
        ),
        policy_version=policy.version,
    )


def _money(amount: Decimal | None, currency: str | None) -> str:
    if amount is None or currency is None:
        return "no estimate"
    return f"{amount} {currency}"


def _review_question(facts: RecommendationFacts) -> str | None:
    if facts.decision is not Decision.REVIEW:
        return None
    questions = [_REVIEW_QUESTION[code] for code in facts.reason_codes if code in _REVIEW_QUESTION]
    return " ".join(questions) if questions else "Confirm this recommendation with the client."


class TemplateExplanationGenerator:
    """Deterministic explanation used offline and whenever the model path fails."""

    def generate(self, facts: RecommendationFacts) -> Explanation:
        reasons = [_REASON_TEXT[code] for code in facts.reason_codes if code in _REASON_TEXT]
        joined = "; ".join(reasons) if reasons else "no policy exceptions were recorded"
        saving = _money(facts.estimated_net_saving, facts.currency)

        if facts.decision is Decision.REBOOK:
            text = (
                f"Recommend rebooking to {facts.selected_offer_id}: estimated net saving "
                f"{saving} after the exchange fee of "
                f"{_money(facts.current_exchange_fee_total, facts.currency)} for "
                f"{facts.passengers} "
                f"passenger(s), because {joined}."
            )
            impact = "The traveller keeps an equivalent journey at a lower total cost."
        elif facts.decision is Decision.REVIEW:
            text = (
                f"Hold for human review: {facts.selected_offer_id or 'this booking'} shows "
                f"an estimated net saving of {saving}, but {joined}."
            )
            impact = "A reviewer must accept the trade-off before anything changes."
        else:
            best = facts.best_comparable_offer_id or "the best available fare"
            text = f"Do not rebook: {best} would leave {saving}, because {joined}."
            if facts.monitoring_trigger_price is not None:
                text += (
                    f" Recheck when the fare reaches "
                    f"{_money(facts.monitoring_trigger_price, facts.currency)} per passenger."
                )
            impact = "The booking is unchanged."

        return Explanation(
            explanation=text,
            traveler_impact=impact,
            review_question=_review_question(facts),
            source=ExplanationSource.TEMPLATE,
            prompt_version=PROMPT_VERSION,
            provider=None,
        )


_NUMBER = re.compile(r"(?<![\w-])-?\d+(?:\.\d+)?")


def _validate_semantics(payload: LlmExplanationPayload, facts: RecommendationFacts) -> None:
    """Reject prose that contradicts or invents values beyond the locked facts."""
    text = " ".join(
        part
        for part in (payload.explanation, payload.traveler_impact, payload.review_question)
        if part
    )
    lowered = text.casefold()
    forbidden_by_decision = {
        Decision.REBOOK: ("do not rebook", "don't rebook", "hold for review"),
        Decision.DONT_REBOOK: ("recommend rebooking", "recommend rebook", "should rebook"),
        Decision.REVIEW: ("do not rebook", "recommend rebooking", "safe to rebook"),
    }
    if any(phrase in lowered for phrase in forbidden_by_decision[facts.decision]):
        raise ValueError("model explanation contradicts the deterministic decision")

    allowed = {
        value
        for value in (
            facts.estimated_net_saving,
            facts.new_fare_total,
            facts.current_exchange_fee_total,
            (
                facts.future_change_fee_per_passenger.amount
                if facts.future_change_fee_per_passenger
                else None
            ),
            facts.monitoring_trigger_price,
            Decimal(facts.passengers),
        )
        if value is not None
    }
    stated = {Decimal(match) for match in _NUMBER.findall(text)}
    if not stated <= allowed:
        raise ValueError("model explanation contains a number absent from the locked facts")


class LiteLLMExplanationGenerator:
    """Optional model-written explanation via LiteLLM (hosted providers or Ollama)."""

    def __init__(
        self,
        model: str,
        api_base: str | None = None,
        timeout_seconds: float = 20.0,
        completion: Completion | None = None,
    ) -> None:
        self.model = model
        self.api_base = api_base
        self.timeout_seconds = timeout_seconds
        self._completion = completion
        self._fallback = TemplateExplanationGenerator()

    @classmethod
    def from_environment(cls) -> ExplanationGenerator:
        """Model configuration is environment-driven; absent config means template-only."""
        model = os.environ.get("LLM_MODEL")
        if not model:
            return TemplateExplanationGenerator()
        timeout = os.environ.get("LLM_TIMEOUT_SECONDS")
        timeout_seconds = 20.0
        if timeout:
            try:
                configured_timeout = float(timeout)
                if not math.isfinite(configured_timeout) or configured_timeout <= 0:
                    raise ValueError
                timeout_seconds = configured_timeout
            except ValueError:
                logger.warning(
                    "invalid LLM_TIMEOUT_SECONDS=%r; using the 20-second default", timeout
                )
        return cls(
            model=model,
            api_base=os.environ.get("LLM_API_BASE"),
            timeout_seconds=timeout_seconds,
        )

    def generate(self, facts: RecommendationFacts) -> Explanation:
        try:
            completion = self._completion or load_litellm_completion()
            response = completion(
                model=self.model,
                api_base=self.api_base,
                timeout=self.timeout_seconds,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": "RECOMMENDATION_FACTS:\n"
                        + facts.model_dump_json(by_alias=True, indent=2),
                    },
                ],
            )
            content = response.choices[0].message.content
            payload = LlmExplanationPayload.model_validate(json.loads(content))
            _validate_semantics(payload, facts)
        except Exception as error:  # noqa: BLE001 - any provider failure must degrade, not fail
            logger.warning(
                "explanation model unavailable, using template", extra={"error": str(error)}
            )
            return self._fallback.generate(facts)

        return Explanation(
            explanation=payload.explanation,
            traveler_impact=payload.traveler_impact,
            # A REVIEW must always state the question, even if the model omitted it.
            review_question=payload.review_question or _review_question(facts),
            source=ExplanationSource.LLM,
            prompt_version=PROMPT_VERSION,
            provider=self.model,
        )
