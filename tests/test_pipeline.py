"""End-to-end pipeline behaviour, including the five supplied fixture outcomes."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
from conftest import load_supplied_fixtures, raw_offer, raw_pnr

from rebooking_copilot.cli import main
from rebooking_copilot.models import (
    Confidence,
    Decision,
    EvaluationMode,
    ExplanationSource,
    ReasonCode,
    WarningCode,
)
from rebooking_copilot.pipeline import run_batch
from rebooking_copilot.reasoning import TemplateExplanationGenerator


@pytest.fixture
def explainer():
    return TemplateExplanationGenerator()


def run(pnr_envelope, fares_envelope, policy, clock, explainer):
    fares_envelope.setdefault("capturedAt", "2026-07-08T09:00:00Z")
    return run_batch(pnr_envelope, fares_envelope, policy, clock=clock, explainer=explainer)


@pytest.fixture
def supplied(policy, clock, explainer):
    pnrs, fares = load_supplied_fixtures()
    return run(pnrs, fares, policy, clock, explainer)


def by_pnr(result, code):
    return next(item for item in result.recommendations if item.pnr == code)


# ------------------------------------------------- supplied fixture outcomes


def test_every_supplied_pnr_produces_exactly_one_recommendation(supplied):
    assert [item.pnr for item in supplied.recommendations] == [
        "QX7T2A",
        "LM9P4C",
        "RT5K8B",
        "ZC3N1D",
        "HB6W9E",
    ]


def test_clean_same_product_saving_is_a_high_confidence_rebook(supplied):
    result = by_pnr(supplied, "QX7T2A")

    assert result.decision is Decision.REBOOK
    assert result.selected_offer_id == "OF-1001"
    assert result.estimated_net_saving.amount == Decimal("80.00")
    assert result.estimated_net_saving.currency == "USD"
    assert result.confidence is Confidence.HIGH
    selected = next(item for item in result.candidates if item.offer_id == "OF-1001")
    assert selected.economics.current_exchange_fee_total == Decimal("100.00")
    assert selected.economics.future_change_fee_per_passenger.amount == Decimal("100.00")


def test_two_passenger_fee_and_downgrade_make_the_exchange_uneconomic(supplied):
    result = by_pnr(supplied, "LM9P4C")

    assert result.decision is Decision.DONT_REBOOK
    assert result.estimated_net_saving.amount == Decimal("-90.00")


def test_illustrative_fx_forces_low_confidence_review(supplied):
    result = by_pnr(supplied, "RT5K8B")

    assert result.decision is Decision.REVIEW
    assert result.estimated_net_saving.amount == Decimal("255.56")
    assert result.estimated_net_saving.currency == "EUR"
    assert result.confidence is Confidence.LOW
    assert ReasonCode.CROSS_CURRENCY_ESTIMATE in result.reason_codes


def test_refundability_loss_is_escalated_to_review(supplied):
    result = by_pnr(supplied, "ZC3N1D")

    assert result.decision is Decision.REVIEW
    assert result.estimated_net_saving.amount == Decimal("140.00")
    assert ReasonCode.REFUNDABILITY_LOST in result.reason_codes


def test_fee_dominated_booking_is_declined_but_keeps_monitoring_metadata(supplied):
    result = by_pnr(supplied, "HB6W9E")

    assert result.decision is Decision.DONT_REBOOK
    assert result.best_comparable_offer_id == "OF-5001"
    assert result.estimated_net_saving.amount == Decimal("-90.00")
    assert result.monitoring is not None
    assert result.monitoring.trigger_price_per_passenger == Decimal("85.00")


def test_non_comparable_cheap_offer_is_rejected_for_product_reasons(supplied):
    result = by_pnr(supplied, "HB6W9E")
    degraded = next(item for item in result.candidates if item.offer_id == "OF-5002")

    assert degraded.economics.estimated_net_saving == Decimal("-40.00")
    assert ReasonCode.ADDS_STOP in degraded.reason_codes
    assert ReasonCode.BAGGAGE_REDUCED in degraded.reason_codes
    assert ReasonCode.CABIN_DOWNGRADE in degraded.reason_codes


def test_provenance_is_exposed_for_audit(supplied, policy):
    assert supplied.evaluation_mode is EvaluationMode.HISTORICAL_SNAPSHOT
    assert supplied.fare_snapshot_captured_at is not None
    assert supplied.policy_version == policy.version
    result = by_pnr(supplied, "QX7T2A")
    assert result.evaluated_as_of is not None
    assert result.generated_at is not None


# ----------------------------------------------------- defensive isolation


def test_malformed_pnr_becomes_review_and_does_not_stop_the_batch(policy, clock, explainer):
    broken = raw_pnr(pnr="BROKEN", passengers=0)
    healthy = raw_pnr(pnr="HEALTHY")

    result = run({"pnrs": [broken, healthy]}, {"offers": [raw_offer()]}, policy, clock, explainer)

    assert [item.pnr for item in result.recommendations] == ["BROKEN", "HEALTHY"]
    assert by_pnr(result, "BROKEN").decision is Decision.REVIEW
    assert ReasonCode.PNR_VALIDATION_FAILED in by_pnr(result, "BROKEN").reason_codes
    assert by_pnr(result, "HEALTHY").decision is Decision.REBOOK


def test_relevant_malformed_offer_does_not_erase_safe_rebook(policy, clock, explainer):
    good = raw_offer(offerId="OF-GOOD")
    bad = raw_offer(offerId="OF-BAD", seatsAvailable=-1)

    result = run({"pnrs": [raw_pnr()]}, {"offers": [good, bad]}, policy, clock, explainer)

    assert result.quarantined_offers == 1
    recommendation = by_pnr(result, "TEST01")
    assert recommendation.decision is Decision.REBOOK
    assert recommendation.selected_offer_id == "OF-GOOD"
    assert WarningCode.OFFER_DATA_INCOMPLETE in recommendation.warning_codes
    assert recommendation.candidates[0].offer_id == "OF-GOOD"
    assert recommendation.candidate_warnings[0].offer_id == "OF-BAD"


def test_relevant_malformed_offer_does_not_erase_valid_decline(policy, clock, explainer):
    negative = raw_offer(price={"amount": Decimal("490.00"), "currency": "USD"})
    bad = raw_offer(offerId="OF-BAD", seatsAvailable=-1)

    result = run({"pnrs": [raw_pnr()]}, {"offers": [negative, bad]}, policy, clock, explainer)

    recommendation = by_pnr(result, "TEST01")
    assert recommendation.decision is Decision.DONT_REBOOK
    assert recommendation.estimated_net_saving.amount == Decimal("-40.00")
    assert WarningCode.OFFER_DATA_INCOMPLETE in recommendation.warning_codes
    assert ReasonCode.SAVING_BELOW_THRESHOLD in recommendation.reason_codes


def test_all_relevant_offers_malformed_produces_review(policy, clock, explainer):
    bad = raw_offer(offerId="OF-BAD", seatsAvailable=-1)

    result = run({"pnrs": [raw_pnr()]}, {"offers": [bad]}, policy, clock, explainer)

    recommendation = by_pnr(result, "TEST01")
    assert recommendation.decision is Decision.REVIEW
    assert recommendation.estimated_net_saving is None
    assert ReasonCode.ALL_OFFERS_INVALID in recommendation.reason_codes
    assert WarningCode.OFFER_DATA_INCOMPLETE in recommendation.warning_codes


def test_malformed_unrelated_offer_does_not_warn_another_pnr(policy, clock, explainer):
    good = raw_offer(offerId="OF-GOOD")
    unrelated_bad = raw_offer(offerId="OF-OTHER-BAD", route={"origin": "ZZZ"}, seatsAvailable=-1)

    result = run({"pnrs": [raw_pnr()]}, {"offers": [good, unrelated_bad]}, policy, clock, explainer)

    recommendation = by_pnr(result, "TEST01")
    assert recommendation.decision is Decision.REBOOK
    assert recommendation.warning_codes == ()
    assert recommendation.candidate_warnings == ()


def test_valid_feed_with_no_matching_offers_declines_with_reason_code(policy, clock, explainer):
    elsewhere = raw_offer(offerId="OF-OTHER", route={"origin": "ZZZ"})

    result = run({"pnrs": [raw_pnr()]}, {"offers": [elsewhere]}, policy, clock, explainer)

    recommendation = by_pnr(result, "TEST01")
    assert recommendation.decision is Decision.DONT_REBOOK
    assert ReasonCode.NO_MATCHING_OFFERS in recommendation.reason_codes


def test_unsupported_currency_returns_review_without_estimated_saving(policy, clock, explainer):
    booking = raw_pnr(
        ticket={
            "pricePerPassenger": {"amount": Decimal("500.00"), "currency": "JPY"},
            "totalPaid": {"amount": Decimal("500.00"), "currency": "JPY"},
            "changeFeePerPassenger": {"amount": Decimal("0.00"), "currency": "JPY"},
        }
    )

    result = run({"pnrs": [booking]}, {"offers": [raw_offer()]}, policy, clock, explainer)

    recommendation = by_pnr(result, "TEST01")
    assert recommendation.decision is Decision.REVIEW
    assert recommendation.estimated_net_saving is None
    assert ReasonCode.FX_RATE_UNAVAILABLE in recommendation.reason_codes


def test_unsupported_currency_offer_does_not_erase_safe_rebook(policy, clock, explainer):
    ordinary = raw_offer(offerId="OF-USD")
    unknown_fx = raw_offer(offerId="OF-GBP", price={"amount": Decimal("1.00"), "currency": "GBP"})

    result = run(
        {"pnrs": [raw_pnr()]}, {"offers": [ordinary, unknown_fx]}, policy, clock, explainer
    )

    recommendation = by_pnr(result, "TEST01")
    assert recommendation.decision is Decision.REBOOK
    assert recommendation.selected_offer_id == "OF-USD"
    assert WarningCode.FX_RATE_UNAVAILABLE in recommendation.warning_codes
    assert ReasonCode.FX_RATE_UNAVAILABLE not in recommendation.reason_codes
    assert recommendation.candidate_warnings[0].offer_id == "OF-GBP"


def test_unsupported_currency_offer_does_not_erase_valid_decline(policy, clock, explainer):
    ordinary = raw_offer(price={"amount": Decimal("490.00"), "currency": "USD"})
    unknown_fx = raw_offer(offerId="OF-GBP", price={"amount": Decimal("1.00"), "currency": "GBP"})

    result = run(
        {"pnrs": [raw_pnr()]}, {"offers": [ordinary, unknown_fx]}, policy, clock, explainer
    )

    recommendation = by_pnr(result, "TEST01")
    assert recommendation.decision is Decision.DONT_REBOOK
    assert recommendation.estimated_net_saving.amount == Decimal("-40.00")
    assert WarningCode.FX_RATE_UNAVAILABLE in recommendation.warning_codes
    assert ReasonCode.SAVING_BELOW_THRESHOLD in recommendation.reason_codes


def test_unsupported_future_fee_is_review_evidence_without_inventing_rate(policy, clock, explainer):
    candidate = raw_offer(changeFeePerPassenger={"amount": Decimal("25.00"), "currency": "GBP"})

    result = run({"pnrs": [raw_pnr()]}, {"offers": [candidate]}, policy, clock, explainer)

    recommendation = by_pnr(result, "TEST01")
    evidence = recommendation.candidates[0].economics
    assert recommendation.decision is Decision.REVIEW
    assert ReasonCode.FUTURE_CHANGE_FEE_UNAVAILABLE in recommendation.reason_codes
    assert WarningCode.FX_RATE_UNAVAILABLE in recommendation.warning_codes
    assert evidence.future_change_fee_per_passenger.currency == "GBP"
    assert evidence.future_change_fee_in_booking_currency is None


def test_every_unpriceable_candidate_produces_review_without_invented_saving(
    policy, clock, explainer
):
    gbp = raw_offer(offerId="OF-GBP", price={"amount": Decimal("1.00"), "currency": "GBP"})
    jpy = raw_offer(offerId="OF-JPY", price={"amount": Decimal("1.00"), "currency": "JPY"})

    result = run({"pnrs": [raw_pnr()]}, {"offers": [gbp, jpy]}, policy, clock, explainer)

    recommendation = by_pnr(result, "TEST01")
    assert recommendation.decision is Decision.REVIEW
    assert recommendation.estimated_net_saving is None
    assert recommendation.reason_codes == (ReasonCode.FX_RATE_UNAVAILABLE,)
    assert {item.offer_id for item in recommendation.candidate_warnings} == {"OF-GBP", "OF-JPY"}


def test_malformed_non_string_reference_is_isolated(policy, clock, explainer):
    broken = raw_pnr(pnr=123, passengers=0)
    healthy = raw_pnr(pnr="HEALTHY")

    result = run({"pnrs": [broken, healthy]}, {"offers": [raw_offer()]}, policy, clock, explainer)

    assert [item.pnr for item in result.recommendations] == ["UNKNOWN-1", "HEALTHY"]
    assert result.recommendations[0].decision is Decision.REVIEW
    assert result.recommendations[1].decision is Decision.REBOOK


def test_default_cli_replays_fixture_snapshot_regardless_of_wall_clock(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parent.parent
    output = tmp_path / "recommendations.json"
    monkeypatch.delenv("LLM_MODEL", raising=False)

    exit_code = main(
        [
            "--pnrs",
            str(root / "fixtures" / "pnrs.json"),
            "--fares",
            str(root / "fixtures" / "fares_feed.json"),
            "--policy",
            str(root / "policy.json"),
            "--output",
            str(output),
            "--quiet",
        ]
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    decisions = {item["pnr"]: item["decision"] for item in payload["recommendations"]}
    assert exit_code == 0
    assert decisions == {
        "QX7T2A": "REBOOK",
        "LM9P4C": "DONT_REBOOK",
        "RT5K8B": "REVIEW",
        "ZC3N1D": "REVIEW",
        "HB6W9E": "DONT_REBOOK",
    }
    assert payload["decisionCounts"] == {"REBOOK": 1, "DONT_REBOOK": 2, "REVIEW": 2}
    assert payload["evaluationMode"] == "HISTORICAL_SNAPSHOT"
    assert payload["fareSnapshotCapturedAt"] == "2026-07-08T09:00:00Z"


def test_custom_explainer_failure_falls_back_without_failing_batch(policy, clock):
    class BrokenExplainer:
        def generate(self, facts):
            raise RuntimeError("broken")

    result = run({"pnrs": [raw_pnr()]}, {"offers": [raw_offer()]}, policy, clock, BrokenExplainer())

    assert result.recommendations[0].decision is Decision.REBOOK
    assert result.recommendations[0].explanation.source is ExplanationSource.TEMPLATE


def test_unexpected_failure_in_one_pnr_does_not_stop_the_batch(
    policy, clock, explainer, monkeypatch
):
    from rebooking_copilot import pipeline

    original = pipeline.decide
    calls = {"n": 0}

    def exploding(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("unexpected boom")
        return original(*args, **kwargs)

    monkeypatch.setattr(pipeline, "decide", exploding)

    result = run(
        {"pnrs": [raw_pnr(pnr="BOOM"), raw_pnr(pnr="OK")]},
        {"offers": [raw_offer()]},
        policy,
        clock,
        explainer,
    )

    assert by_pnr(result, "BOOM").decision is Decision.REVIEW
    assert ReasonCode.PIPELINE_ERROR in by_pnr(result, "BOOM").reason_codes
    assert by_pnr(result, "OK").decision is Decision.REBOOK


def test_explanations_are_generated_only_for_rebook_and_review(policy, clock):
    class CountingExplainer(TemplateExplanationGenerator):
        def __init__(self) -> None:
            self.calls: list[Decision] = []

        def generate(self, facts):
            self.calls.append(facts.decision)
            return super().generate(facts)

    explainer = CountingExplainer()
    pnrs, fares = load_supplied_fixtures()
    run(pnrs, fares, policy, clock, explainer)

    assert Decision.DONT_REBOOK not in explainer.calls
    assert set(explainer.calls) == {Decision.REBOOK, Decision.REVIEW}


def test_declined_recommendations_still_carry_a_deterministic_explanation(supplied):
    result = by_pnr(supplied, "HB6W9E")

    assert result.explanation.explanation
