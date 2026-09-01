"""Typed contracts for input, policy, evidence, and output.

Money is `Decimal` from parsing through serialization. Passenger, stop, baggage,
and seat counts are integers. Decisions, confidence, and reasons are enums so
downstream consumers can switch on stable values rather than prose.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)
from pydantic.alias_generators import to_camel

MONEY_PRECISION = Decimal("0.01")
RATIO_PRECISION = Decimal("0.0001")

CurrencyCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
AirportCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Cabin = Literal["BASIC_ECONOMY", "ECONOMY", "PREMIUM_ECONOMY", "BUSINESS", "FIRST"]

# Ordered least to most premium; used only to detect a downgrade.
CABIN_RANK: dict[str, int] = {
    "BASIC_ECONOMY": 0,
    "ECONOMY": 1,
    "PREMIUM_ECONOMY": 2,
    "BUSINESS": 3,
    "FIRST": 4,
}


def quantize_money(amount: Decimal) -> Decimal:
    return Decimal(amount).quantize(MONEY_PRECISION, rounding=ROUND_HALF_UP)


def cabin_rank(cabin: str) -> int:
    """Unknown cabins rank below everything so they can never look like an upgrade."""
    return CABIN_RANK.get(cabin.upper(), -1)


class CamelModel(BaseModel):
    """Fixture JSON and emitted JSON are both camelCase; Python stays snake_case."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


# --------------------------------------------------------------------- enums


class Decision(str, Enum):
    REBOOK = "REBOOK"
    DONT_REBOOK = "DONT_REBOOK"
    REVIEW = "REVIEW"


class Confidence(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class CandidateStatus(str, Enum):
    ELIGIBLE = "ELIGIBLE"
    REVIEW = "REVIEW"
    REJECTED = "REJECTED"


class RecheckPriority(str, Enum):
    NORMAL = "NORMAL"
    ELEVATED = "ELEVATED"


class ExplanationSource(str, Enum):
    TEMPLATE = "TEMPLATE"
    LLM = "LLM"


class EvaluationMode(str, Enum):
    HISTORICAL_SNAPSHOT = "HISTORICAL_SNAPSHOT"


class ReasonCode(str, Enum):
    EQUIVALENT_PRODUCT_SAVING = "EQUIVALENT_PRODUCT_SAVING"
    SAVING_BELOW_THRESHOLD = "SAVING_BELOW_THRESHOLD"
    INSUFFICIENT_SEATS = "INSUFFICIENT_SEATS"
    DEPARTURE_OUTSIDE_WINDOW = "DEPARTURE_OUTSIDE_WINDOW"
    ARRIVAL_OUTSIDE_WINDOW = "ARRIVAL_OUTSIDE_WINDOW"
    ADDS_STOP = "ADDS_STOP"
    BAGGAGE_REDUCED = "BAGGAGE_REDUCED"
    CABIN_DOWNGRADE = "CABIN_DOWNGRADE"
    CABIN_DOWNGRADE_REVIEW = "CABIN_DOWNGRADE_REVIEW"
    REFUNDABILITY_LOST = "REFUNDABILITY_LOST"
    CARRIER_NOT_APPROVED = "CARRIER_NOT_APPROVED"
    CROSS_CURRENCY_ESTIMATE = "CROSS_CURRENCY_ESTIMATE"
    FUTURE_CHANGE_FEE_INCREASED = "FUTURE_CHANGE_FEE_INCREASED"
    FUTURE_CHANGE_FEE_UNAVAILABLE = "FUTURE_CHANGE_FEE_UNAVAILABLE"
    FX_RATE_UNAVAILABLE = "FX_RATE_UNAVAILABLE"
    NO_MATCHING_OFFERS = "NO_MATCHING_OFFERS"
    ALL_OFFERS_INVALID = "ALL_OFFERS_INVALID"
    PNR_VALIDATION_FAILED = "PNR_VALIDATION_FAILED"
    PIPELINE_ERROR = "PIPELINE_ERROR"


class WarningCode(str, Enum):
    """Non-authoritative evidence about alternatives excluded from evaluation."""

    OFFER_DATA_INCOMPLETE = "OFFER_DATA_INCOMPLETE"
    FX_RATE_UNAVAILABLE = "FX_RATE_UNAVAILABLE"


class ConfidenceReason(str, Enum):
    COMPLETE_SAME_CURRENCY_DATA = "COMPLETE_SAME_CURRENCY_DATA"
    CLIENT_DECISION_REQUIRED = "CLIENT_DECISION_REQUIRED"
    ILLUSTRATIVE_FX_RATE = "ILLUSTRATIVE_FX_RATE"
    NO_ACCEPTABLE_OFFER = "NO_ACCEPTABLE_OFFER"
    DATA_VALIDATION_FAILURE = "DATA_VALIDATION_FAILURE"


# --------------------------------------------------------------------- money


class Money(CamelModel):
    model_config = ConfigDict(frozen=True)

    amount: Decimal
    currency: CurrencyCode

    @field_validator("amount", mode="after")
    @classmethod
    def _quantize(cls, value: Decimal) -> Decimal:
        return quantize_money(value)


class NonNegativeMoney(Money):
    """Input amounts: a negative fare or fee is malformed data, not a discount."""

    @field_validator("amount", mode="before")
    @classmethod
    def _non_negative(cls, value: Decimal) -> Decimal:
        if Decimal(str(value)) < 0:
            raise ValueError("amount must not be negative")
        return value


def _naive(value: datetime) -> datetime:
    """Fixture timestamps are airport-local wall-clock times without an offset.

    An offset-bearing timestamp is ambiguous against local-date matching, so it is
    rejected at the boundary rather than silently reinterpreted.
    """
    if value.tzinfo is not None:
        raise ValueError("timestamp must be airport-local without a UTC offset")
    return value


# ------------------------------------------------------------ input records


class Segment(CamelModel):
    origin: AirportCode
    destination: AirportCode
    departure: datetime
    arrival: datetime
    carrier: str = Field(min_length=1)
    flight_number: str = Field(min_length=1)
    stops: int = Field(ge=0)
    cabin: Cabin
    fare_basis: str = Field(min_length=1)

    _check_departure = field_validator("departure", "arrival")(_naive)

    @model_validator(mode="after")
    def _chronological(self) -> Segment:
        if self.arrival <= self.departure:
            raise ValueError("arrival must be after departure")
        return self


class Ticket(CamelModel):
    price_per_passenger: NonNegativeMoney
    total_paid: NonNegativeMoney
    cabin: Cabin
    refundable: bool
    change_fee_per_passenger: NonNegativeMoney
    baggage_included_pieces: int = Field(ge=0)


class Pnr(CamelModel):
    pnr: Identifier
    passengers: int = Field(gt=0)
    itinerary: list[Segment] = Field(min_length=1)
    ticket: Ticket

    @property
    def segment(self) -> Segment:
        return self.itinerary[0]

    @model_validator(mode="after")
    def _single_segment_only(self) -> Pnr:
        if len(self.itinerary) != 1:
            raise ValueError("multi-segment itineraries are outside the prototype scope")
        return self

    @model_validator(mode="after")
    def _totals_are_consistent(self) -> Pnr:
        """`totalPaid` is authoritative but must agree with the per-passenger price."""
        expected = quantize_money(self.ticket.price_per_passenger.amount * self.passengers)
        if expected != self.ticket.total_paid.amount:
            raise ValueError(
                "ticket.totalPaid does not equal pricePerPassenger x passengers "
                f"({self.ticket.total_paid.amount} != {expected})"
            )
        if self.ticket.price_per_passenger.currency != self.ticket.total_paid.currency:
            raise ValueError("ticket.totalPaid currency differs from pricePerPassenger")
        if self.ticket.cabin != self.segment.cabin:
            raise ValueError("ticket cabin differs from itinerary segment cabin")
        return self


class Route(CamelModel):
    origin: AirportCode
    destination: AirportCode
    departure_date: date


class Offer(CamelModel):
    offer_id: Identifier
    route: Route
    carrier: str = Field(min_length=1)
    flight_number: str = Field(min_length=1)
    departure: datetime
    arrival: datetime
    stops: int = Field(ge=0)
    cabin: Cabin
    fare_basis: str = Field(min_length=1)
    price: NonNegativeMoney
    refundable: bool
    change_fee_per_passenger: NonNegativeMoney
    baggage_included_pieces: int = Field(ge=0)
    seats_available: int = Field(ge=0)

    _check_departure = field_validator("departure", "arrival")(_naive)

    @model_validator(mode="after")
    def _route_and_times_are_consistent(self) -> Offer:
        if self.arrival <= self.departure:
            raise ValueError("arrival must be after departure")
        if self.route.departure_date != self.departure.date():
            raise ValueError("route.departureDate differs from departure timestamp")
        return self


# -------------------------------------------------------------------- policy


class Policy(CamelModel):
    """Client business rules. Versioned so a recommendation can be replayed."""

    version: str
    min_saving_absolute: Decimal = Field(ge=0)
    min_saving_percent_of_total_paid: Decimal = Field(ge=0, le=1)
    cabin_downgrade_min_saving_percent: Decimal = Field(ge=0, le=1)
    schedule_tolerance_hours: Decimal = Field(ge=0)
    near_threshold_percentage_points: Decimal = Field(ge=0, le=1)
    approved_carriers: tuple[str, ...]
    fx_rates: dict[str, Decimal]
    fx_rate_source: str

    @model_validator(mode="after")
    def _safe_policy_values(self) -> Policy:
        if self.near_threshold_percentage_points > self.min_saving_percent_of_total_paid:
            raise ValueError("near-threshold points cannot exceed the percentage threshold")
        if not self.approved_carriers or any(
            not carrier or carrier != carrier.upper() for carrier in self.approved_carriers
        ):
            raise ValueError("approved carriers must be non-empty uppercase codes")
        for pair, rate in self.fx_rates.items():
            currencies = pair.split("/")
            if (
                len(currencies) != 2
                or any(
                    len(currency) != 3 or currency != currency.upper() for currency in currencies
                )
                or rate <= 0
            ):
                raise ValueError(f"invalid FX rate entry: {pair}")
        return self

    @classmethod
    def load(cls, path: Path) -> Policy:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle, parse_float=Decimal)
        raw.pop("_comment", None)
        return cls.model_validate(raw)


# ------------------------------------------------------------------ evidence


class Conversion(CamelModel):
    original: Money
    converted: Money
    quote_pair: str
    quoted_rate: Decimal
    applied_pair: str
    applied_rate: Decimal
    source: str


class CandidateEconomics(CamelModel):
    currency: CurrencyCode
    new_fare_total: Decimal
    current_exchange_fee_total: Decimal
    future_change_fee_per_passenger: Money
    future_change_fee_in_booking_currency: Decimal | None
    estimated_net_saving: Decimal
    net_saving_percent_of_total_paid: Decimal
    fare_conversion: Conversion | None = None
    future_change_fee_conversion: Conversion | None = None


class CandidateAssessment(CamelModel):
    offer_id: str
    status: CandidateStatus
    reason_codes: tuple[ReasonCode, ...]
    economics: CandidateEconomics
    is_comparable: bool


class CandidateWarning(CamelModel):
    offer_id: str | None
    warning_codes: tuple[WarningCode, ...]


class Monitoring(CamelModel):
    """Scheduling metadata attached to a decision; never a decision itself."""

    recheck_priority: RecheckPriority
    trigger_price_per_passenger: Decimal | None = None
    currency: CurrencyCode | None = None


class DecisionOutcome(CamelModel):
    decision: Decision
    selected_offer_id: str | None
    estimated_net_saving: Decimal | None
    currency: CurrencyCode | None
    confidence: Confidence
    confidence_reasons: tuple[ConfidenceReason, ...]
    reason_codes: tuple[ReasonCode, ...]
    warning_codes: tuple[WarningCode, ...] = ()
    candidate_warnings: tuple[CandidateWarning, ...] = ()
    candidates: tuple[CandidateAssessment, ...] = ()
    alternatives: tuple[CandidateAssessment, ...] = ()
    best_comparable_offer_id: str | None = None
    monitoring: Monitoring | None = None


# ------------------------------------------------------- explanation layer


class RecommendationFacts(CamelModel):
    """The only payload the model sees: locked evidence, no traveller detail."""

    decision: Decision
    selected_offer_id: str | None
    estimated_net_saving: Decimal | None
    currency: CurrencyCode | None
    passengers: int
    new_fare_total: Decimal | None
    current_exchange_fee_total: Decimal | None
    future_change_fee_per_passenger: Money | None
    confidence: Confidence
    confidence_reasons: tuple[ConfidenceReason, ...]
    reason_codes: tuple[ReasonCode, ...]
    best_comparable_offer_id: str | None = None
    monitoring_trigger_price: Decimal | None = None
    policy_version: str


class LlmExplanationPayload(CamelModel):
    """Strict contract for model output; anything else triggers the fallback."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    explanation: str = Field(min_length=1, max_length=900)
    traveler_impact: str | None = Field(default=None, max_length=300)
    review_question: str | None = Field(default=None, max_length=300)


class Explanation(CamelModel):
    explanation: str
    traveler_impact: str | None = None
    review_question: str | None = None
    source: ExplanationSource
    prompt_version: str
    provider: str | None = None


# --------------------------------------------------------------------- output


class QuarantinedOffer(CamelModel):
    offer_id: str | None
    error: str
    origin: str | None = None
    destination: str | None = None
    departure_date: str | None = None


class Recommendation(CamelModel):
    pnr: str
    decision: Decision
    selected_offer_id: str | None
    estimated_net_saving: Money | None
    confidence: Confidence
    confidence_reasons: tuple[ConfidenceReason, ...]
    reason_codes: tuple[ReasonCode, ...]
    warning_codes: tuple[WarningCode, ...] = ()
    candidate_warnings: tuple[CandidateWarning, ...] = ()
    explanation: Explanation
    best_comparable_offer_id: str | None = None
    monitoring: Monitoring | None = None
    candidates: tuple[CandidateAssessment, ...] = ()
    alternatives: tuple[CandidateAssessment, ...] = ()
    evaluated_as_of: datetime | None = None
    generated_at: datetime
    fx_rate_source: str
    policy_version: str
    validation_error: str | None = None


class BatchResult(CamelModel):
    evaluation_mode: EvaluationMode
    generated_at: datetime
    evaluated_as_of: datetime | None
    fare_snapshot_captured_at: datetime | None
    policy_version: str
    fx_rate_source: str
    pnrs_evaluated: int
    quarantined_offers: int
    decision_counts: dict[str, int]
    recommendations: tuple[Recommendation, ...]
