"""Command-line entry point: load fixtures, run the batch, write JSON."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .models import Policy
from .pipeline import run_batch
from .reasoning import LiteLLMExplanationGenerator
from .validation import EnvelopeError

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_POLICY = PACKAGE_ROOT / "policy.json"


def load_json(path: Path) -> Any:
    """Parse numbers as `Decimal` so money never passes through a float."""
    with open(path, encoding="utf-8") as handle:
        return json.load(handle, parse_float=Decimal)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rebooking_copilot",
        description="Recommend rebooking actions for ticketed PNRs against a fare snapshot.",
    )
    parser.add_argument("--pnrs", type=Path, default=PACKAGE_ROOT / "fixtures" / "pnrs.json")
    parser.add_argument("--fares", type=Path, default=PACKAGE_ROOT / "fixtures" / "fares_feed.json")
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument(
        "--output",
        type=Path,
        default=PACKAGE_ROOT / "output" / "recommendations.json",
        help="where to write the structured result",
    )
    parser.add_argument("--quiet", action="store_true", help="suppress the human-readable summary")
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    args = build_parser().parse_args(argv)

    try:
        policy = Policy.load(args.policy)
        pnr_document = load_json(args.pnrs)
        fares_document = load_json(args.fares)
    except (OSError, json.JSONDecodeError, ValidationError) as error:
        print(f"error: could not read inputs: {error}", file=sys.stderr)
        return 2

    try:
        result = run_batch(
            pnr_document,
            fares_document,
            policy,
            clock=lambda: datetime.now(timezone.utc),
            explainer=LiteLLMExplanationGenerator.from_environment(),
        )
    except (EnvelopeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(result.model_dump_json(by_alias=True, indent=2) + "\n", encoding="utf-8")

    if not args.quiet:
        print(
            f"Evaluation mode: {result.evaluation_mode.value}. Fare snapshot captured "
            f"{result.fare_snapshot_captured_at}. REBOOK means the recommendation as of "
            "that snapshot; current validation and repricing are required before any action.\n",
            file=sys.stderr,
        )
        for item in result.recommendations:
            saving = (
                f"{item.estimated_net_saving.amount} {item.estimated_net_saving.currency}"
                if item.estimated_net_saving
                else "-"
            )
            print(
                f"{item.pnr}: {item.decision.value:<12} "
                f"offer={item.selected_offer_id or '-':<8} "
                f"net={saving:<14} confidence={item.confidence.value}"
            )
            print(f"    {item.explanation.explanation}")
            if item.warning_codes:
                warnings = ", ".join(code.value for code in item.warning_codes)
                print(f"    Warnings: {warnings}")
            if item.explanation.review_question:
                print(f"    Review: {item.explanation.review_question}")
        print(f"\nStructured output: {args.output}")

    return 0
