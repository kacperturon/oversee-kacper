"""Golden contract test for the serialized output, using an injected fixed clock."""

from __future__ import annotations

import json
import os
from pathlib import Path

from conftest import load_supplied_fixtures

from rebooking_copilot.pipeline import run_batch
from rebooking_copilot.reasoning import TemplateExplanationGenerator

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "tests" / "golden_output.json"


def test_serialized_output_matches_the_golden_contract(policy, clock):
    pnrs, fares = load_supplied_fixtures()
    result = run_batch(pnrs, fares, policy, clock=clock, explainer=TemplateExplanationGenerator())
    actual = json.loads(result.model_dump_json(by_alias=True, indent=2))

    regenerate = os.environ.get("REGEN_GOLDEN") == "1"
    if not GOLDEN.exists() or regenerate:
        if not regenerate:
            raise AssertionError(
                "golden file missing - regenerate deliberately with REGEN_GOLDEN=1"
            )
        GOLDEN.write_text(json.dumps(actual, indent=2) + "\n", encoding="utf-8")

    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert actual == expected


def test_money_is_serialized_as_strings_not_floats(policy, clock):
    pnrs, fares = load_supplied_fixtures()
    result = run_batch(pnrs, fares, policy, clock=clock, explainer=TemplateExplanationGenerator())
    payload = json.loads(result.model_dump_json(by_alias=True))

    saving = payload["recommendations"][0]["estimatedNetSaving"]
    assert isinstance(saving["amount"], str)
    assert saving["amount"] == "80.00"
