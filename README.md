# Rebooking Copilot

A Python prototype that reviews ticketed flight bookings against a fare snapshot and produces one structured recommendation per PNR: `REBOOK`, `DONT_REBOOK`, or `REVIEW`.

The primary deliverable is [`DESIGN.md`](DESIGN.md). It explains the architecture, policy assumptions, LLM boundary, financial safety, scale, observability, rollout, and deliberate scope cuts. [`ENGINEERING.md`](ENGINEERING.md) defines the conventions for the focused MVP rewrite.

## Core design

- Deterministic code owns validation, matching, policy, offer selection, `Decimal` money calculations, decision, and confidence.
- An optional LLM writes only the human-readable explanation from locked facts.
- LiteLLM provides a small interchangeable adapter for hosted models and local Ollama and is imported only when a model call is configured.
- Missing credentials, provider failures, or invalid model output fall back to a deterministic template.
- Model prose is rejected when it contradicts the decision or contains a number absent from the locked fact set. This lightweight check cannot prove that a real number is used in the correct semantic role; structured deterministic fields remain authoritative.
- The static fixture is evaluated as of its `capturedAt` timestamp; `generatedAt` records the actual UTC run time.
- The prototype recommends only. Every initial result requires human approval and fresh repricing before action.

## Install and run

Python 3.10 or newer.

```bash
python -m pip install -e ".[dev]"

# Runs against ./fixtures and writes output/recommendations.json by default.
python -m rebooking_copilot \
  --pnrs fixtures/pnrs.json \
  --fares fixtures/fares_feed.json \
  --output output/recommendations.json

pytest -q
ruff check .
ruff format --check .
```

The command runs offline and needs no API key: with no model configured, LiteLLM is
not imported and every explanation comes from the deterministic template. To enable
the optional model, set the environment variables LiteLLM expects—for a local Ollama
server:

```bash
LLM_MODEL=ollama/qwen3:8b LLM_API_BASE=http://localhost:11434 python -m rebooking_copilot
```

`LLM_TIMEOUT_SECONDS` overrides the default 20-second timeout. An invalid value logs
a warning and uses the default. Any missing configuration, timeout, provider error,
or schema violation falls back to the template without failing the PNR or batch.
Provider credentials are supplied only through the environment and are never committed.

Business rules live in [`policy.json`](policy.json) and are versioned; the version
that produced a recommendation is recorded in the output.

The supplied fare feed is a historical fixture captured on 2026-07-08. Output is
labeled `evaluationMode: HISTORICAL_SNAPSHOT` and evaluates as of that timestamp;
`generatedAt` remains the actual UTC execution time and `fareSnapshotCapturedAt`
remains the feed timestamp. `REBOOK` means “recommended as of this snapshot,” not
permission to act today. Every action requires current departure, availability,
fare-rule, fee, FX, and authoritative repricing checks. A production/live mode would
also reject departed itineraries and stale feeds.

## Expected fixture outcomes

| PNR | Decision | Estimated net saving | Confidence | Reason |
|---|---|---:|---|---|
| `QX7T2A` | `REBOOK` | 80.00 USD | `HIGH` | Clean same-product saving after fee |
| `LM9P4C` | `DONT_REBOOK` | -90.00 USD | `HIGH` | Two-passenger fee and downgrade make it unattractive |
| `RT5K8B` | `REVIEW` | 255.56 EUR | `LOW` | Fixed proof-of-concept FX rate |
| `ZC3N1D` | `REVIEW` | 140.00 USD | `MEDIUM` | Loses refundability |
| `HB6W9E` | `DONT_REBOOK` | -90.00 USD best comparable | `HIGH` | No valid saving now; monitoring metadata carries a trigger price of 85.00 USD |

`OF-5002` is rejected separately: the existing 150.00 USD exchange fee still
applies, leaving `260 − 150 − 150 = −40 USD`, and it adds a stop, downgrades the
product, loses baggage, and departs outside the accepted window.

Malformed or unsupported-currency alternatives are retained under `warningCodes`
and `candidateWarnings` without overriding a valid priceable decision. If every
relevant offer is malformed or unpriceable, the booking is routed to `REVIEW` without
an invented saving.

The exchange calculation is:

```text
estimated net saving = totalPaid
                     − offer price per passenger × passengers
                     − existing ticket change fee per passenger × passengers
```

The candidate offer's change fee is a future rule of the resulting ticket; it is not
an additional fee for the current exchange. A higher future fee routes an otherwise
worthwhile offer to `REVIEW`; equal-saving candidates prefer the lower future fee.
Candidate evidence distinguishes `currentExchangeFeeTotal` from
`futureChangeFeePerPassenger`.

Schedule equivalence requires both departure and arrival displacement within ±2
hours, inclusive. Candidate lookup reads the same route's departure-date bucket and
its adjacent dates before applying that policy, so cross-midnight offers are not missed.

FX evidence distinguishes the configured `quotePair`/`quotedRate` from the actual
`appliedPair`/`appliedRate`; inverse applied rates retain high decimal precision while
money remains rounded to cents.

Duplicate PNR or offer identifiers fail the input envelope visibly because an
ambiguous identifier cannot support an auditable recommendation.

## Structure

```text
rebooking_copilot/
  models.py       typed input/output contracts
  validation.py   boundary validation and quarantine
  matching.py     journey indexing and candidate lookup
  economics.py    Decimal and FX calculations
  policy.py       decisions, ranking, confidence, reason codes
  reasoning.py    LiteLLM adapter and template fallback
  prompt.py       the published prompt contract, kept verbatim
  pipeline.py     per-PNR orchestration and isolation
  cli.py          command-line entry point
policy.json       versioned client business rules
tests/            unit, pipeline, adapter, and golden-contract tests
```

## Documentation

| File | Purpose |
|---|---|
| [`DESIGN.md`](DESIGN.md) | Primary interview deliverable |
| [`ENGINEERING.md`](ENGINEERING.md) | Python, TDD, defensive-programming, and dependency conventions |
| [`prompts/reasoning_prompt.md`](prompts/reasoning_prompt.md) | Narrow explanation-layer prompt and contract |
| [`docs/TIME_LOG.md`](docs/TIME_LOG.md) | How the time was spent |
| [`output/recommendations.json`](output/recommendations.json) | Example run against the supplied fixtures |

