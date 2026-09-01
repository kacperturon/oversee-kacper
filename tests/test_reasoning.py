"""Explanation layer: template output, LiteLLM adapter, and fallback behaviour.

The provider is mocked at the LiteLLM boundary; no network or API key is used.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from conftest import offer, pnr

from rebooking_copilot.economics import exchange_economics
from rebooking_copilot.models import Decision, ExplanationSource
from rebooking_copilot.policy import assess_candidate, decide
from rebooking_copilot.reasoning import (
    SYSTEM_PROMPT,
    LiteLLMExplanationGenerator,
    TemplateExplanationGenerator,
    build_facts,
)

ROOT = Path(__file__).resolve().parent.parent


def outcome_for(booking, candidate, fx, policy):
    assessment = assess_candidate(
        booking, candidate, exchange_economics(booking, candidate, fx), policy
    )
    return decide(booking, [assessment], policy)


@pytest.fixture
def rebook_facts(fx, policy):
    booking = pnr()
    return build_facts(booking, outcome_for(booking, offer(), fx, policy), policy)


@pytest.fixture
def review_facts(fx, policy):
    booking = pnr(ticket={"refundable": True})
    return build_facts(booking, outcome_for(booking, offer(refundable=False), fx, policy), policy)


class FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = FakeMessage(content)


class FakeResponse:
    def __init__(self, content: str) -> None:
        self.choices = [FakeChoice(content)]


VALID_MODEL_JSON = json.dumps(
    {
        "explanation": "The same flight is cheaper and the saving survives the exchange fee.",
        "travelerImpact": "The traveller keeps the same itinerary.",
        "reviewQuestion": None,
    }
)


# ------------------------------------------------------------------- template


def test_template_explains_a_rebook_without_a_model(rebook_facts):
    explanation = TemplateExplanationGenerator().generate(rebook_facts)

    assert explanation.source is ExplanationSource.TEMPLATE
    assert "150.00" in explanation.explanation
    assert explanation.review_question is None


def test_template_states_what_a_reviewer_must_confirm(review_facts):
    explanation = TemplateExplanationGenerator().generate(review_facts)

    assert explanation.review_question is not None
    assert "refundab" in explanation.review_question.lower()


def test_facts_payload_excludes_raw_traveller_and_booking_detail(rebook_facts):
    payload = rebook_facts.model_dump()

    assert "itinerary" not in payload
    assert "ticket" not in payload
    assert "pnr_reference" not in payload
    assert payload["decision"] == Decision.REBOOK.value


# ------------------------------------------------------------ litellm adapter


def test_template_only_configuration_does_not_load_litellm(monkeypatch):
    monkeypatch.delenv("LLM_MODEL", raising=False)

    with patch("rebooking_copilot.reasoning.load_litellm_completion") as loader:
        generator = LiteLLMExplanationGenerator.from_environment()

    assert isinstance(generator, TemplateExplanationGenerator)
    loader.assert_not_called()


def test_invalid_timeout_uses_default_without_breaking_fallback_selection(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "ollama/qwen3:8b")
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "not-a-number")

    generator = LiteLLMExplanationGenerator.from_environment()

    assert isinstance(generator, LiteLLMExplanationGenerator)
    assert generator.timeout_seconds == 20.0


def test_model_output_is_used_when_it_satisfies_the_schema(rebook_facts):
    completion = Mock(return_value=FakeResponse(VALID_MODEL_JSON))
    generator = LiteLLMExplanationGenerator(model="ollama/qwen3:8b", completion=completion)
    explanation = generator.generate(rebook_facts)

    assert explanation.source is ExplanationSource.LLM
    assert explanation.explanation.startswith("The same flight is cheaper")


def test_provider_timeout_falls_back_to_the_template(rebook_facts):
    completion = Mock(side_effect=TimeoutError("timed out"))
    generator = LiteLLMExplanationGenerator(model="ollama/qwen3:8b", completion=completion)
    explanation = generator.generate(rebook_facts)

    assert explanation.source is ExplanationSource.TEMPLATE
    assert "150.00" in explanation.explanation


def test_provider_error_falls_back_to_the_template(rebook_facts):
    completion = Mock(side_effect=RuntimeError("provider exploded"))
    generator = LiteLLMExplanationGenerator(model="ollama/qwen3:8b", completion=completion)
    explanation = generator.generate(rebook_facts)

    assert explanation.source is ExplanationSource.TEMPLATE


def test_malformed_json_falls_back_to_the_template(rebook_facts):
    completion = Mock(return_value=FakeResponse("not json at all"))
    generator = LiteLLMExplanationGenerator(model="ollama/qwen3:8b", completion=completion)
    explanation = generator.generate(rebook_facts)

    assert explanation.source is ExplanationSource.TEMPLATE


def test_schema_violation_falls_back_to_the_template(rebook_facts):
    completion = Mock()
    generator = LiteLLMExplanationGenerator(model="ollama/qwen3:8b", completion=completion)
    extra_field = json.dumps(
        {
            "explanation": "Fine.",
            "travelerImpact": None,
            "reviewQuestion": None,
            "decision": "REBOOK",
        }
    )

    completion.return_value = FakeResponse(extra_field)
    explanation = generator.generate(rebook_facts)

    assert explanation.source is ExplanationSource.TEMPLATE


def test_model_that_contradicts_decision_or_money_falls_back(rebook_facts):
    completion = Mock()
    generator = LiteLLMExplanationGenerator(model="ollama/qwen3:8b", completion=completion)
    hallucinated = json.dumps(
        {
            "explanation": "Do not rebook; the saving is actually 9999.00 USD.",
            "travelerImpact": None,
            "reviewQuestion": None,
        }
    )

    completion.return_value = FakeResponse(hallucinated)
    explanation = generator.generate(rebook_facts)

    assert explanation.source is ExplanationSource.TEMPLATE
    assert rebook_facts.decision is Decision.REBOOK
    assert rebook_facts.estimated_net_saving == Decimal("150.00")


def test_adapter_passes_configured_model_and_api_base(rebook_facts):
    completion = Mock(return_value=FakeResponse(VALID_MODEL_JSON))
    generator = LiteLLMExplanationGenerator(
        model="ollama/qwen3:8b",
        api_base="http://localhost:11434",
        timeout_seconds=7.5,
        completion=completion,
    )
    generator.generate(rebook_facts)

    kwargs = completion.call_args.kwargs
    assert kwargs["model"] == "ollama/qwen3:8b"
    assert kwargs["api_base"] == "http://localhost:11434"
    assert kwargs["timeout"] == 7.5
    assert kwargs["messages"][0]["role"] == "system"
    assert "TEST01" not in kwargs["messages"][1]["content"]
    assert "pnrReference" not in kwargs["messages"][1]["content"]


def test_system_prompt_matches_the_documented_contract():
    """The prompt doc is the contract; drift between it and the code is a bug."""
    contract = (ROOT / "prompts" / "reasoning_prompt.md").read_text(encoding="utf-8")

    assert SYSTEM_PROMPT.strip() in contract
