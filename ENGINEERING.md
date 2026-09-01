# Engineering Guidelines

These conventions guide the focused Python MVP rewrite. They optimize for correctness, reviewer readability, and delivery within the assignment timebox—not for building an internal platform prematurely.

## Principles

- Prefer the smallest implementation that makes the risky core correct and auditable.
- Be defensive at external boundaries and simple inside validated domain code.
- Fail visibly at the batch boundary; isolate failures at the PNR and offer boundaries.
- Never guess missing financial or operational data.
- Keep policy explicit, versioned, and separate from mechanics.
- Keep the LLM optional and outside financially authoritative code paths.
- Preserve evidence for decisions instead of relying on prose or logs alone.

## Python style

- Target a modern supported Python version and use full type hints.
- Use small, cohesive modules and mostly pure functions.
- Use Pydantic models at JSON, configuration, and LLM-output boundaries.
- Use `Decimal` from parsing through serialization for money and FX; use integers for passengers, stops, baggage, and seats.
- Represent decisions, confidence, and reason codes with enums or constrained literals.
- Prefer descriptive names and straightforward control flow over clever abstractions.
- Comments explain **why**, business policy, or a safety invariant. Do not narrate self-explanatory code.
- Inject variable dependencies such as the clock, FX provider, policy repository, and explanation generator.
- Do not catch broad exceptions inside pure domain functions. At orchestration boundaries, convert known failures into explicit outcomes and isolate unexpected record-level failures with diagnostic context.
- Avoid mutable global state and hidden environment reads in domain code.

## Defensive boundaries

- Reject a malformed top-level envelope or duplicate PNR/offer identifiers with a non-zero exit and useful error.
- Convert a malformed PNR into `REVIEW`; never silently omit it.
- Quarantine a malformed offer and continue evaluating valid offers. If all relevant offers are unusable, return `REVIEW`.
- Validate currencies, timestamps, non-negative amounts, positive passenger counts, and internal totals.
- Require timezone-aware snapshot timestamps. Fixture replay uses `capturedAt`; production freshness enforcement and execution-time repricing remain mandatory future controls.
- Keep selected-decision `reasonCodes` separate from warnings about malformed or unpriceable alternatives.
- Never reuse a recommendation as execution authority; reprice and revalidate first.
- Do not log unnecessary traveler or booking data. Prefer identifiers, hashes, versions, and structured reason codes.

## TDD workflow

For each behavior:

1. Write or update a failing test describing the business outcome.
2. Implement the smallest change that passes it.
3. Refactor while the tests remain green.
4. Update documentation when behavior or policy changes.

Test names should state the scenario and expected result, for example:

```python
def test_two_passenger_exchange_applies_fee_twice() -> None: ...
def test_insufficient_seats_rejects_offer_without_splitting_pnr() -> None: ...
def test_unsupported_currency_returns_review_without_estimated_saving() -> None: ...
```

## Test layers

- **Unit tests:** Decimal calculations, FX, thresholds, time windows, ranking, and individual policy rules.
- **Pipeline tests:** malformed-record isolation, offer quarantine, deterministic fallback, and all five supplied PNR outcomes.
- **Golden contract test:** stable structured output after removing volatile fields or injecting a fixed clock.
- **LLM adapter tests:** provider success, timeout/error, invalid schema, and deterministic fallback. Mock the provider; tests require no network or API key.
- **Semantic explanation tests:** reject numbers absent from locked facts and prose that contradicts the deterministic decision; do not mistake this for full semantic verification.

Additional MVP fixtures should cover exact threshold equality, a malformed PNR and malformed offer, two passengers with one available seat, an unsupported currency, and two otherwise equal offers whose future change fees differ.

Property-based tests, load tests, multi-segment cases, concurrency tests, and calibrated-model evaluation are valuable future work unless time remains.

## Tooling and dependencies

Keep the dependency set narrow:

- Runtime: Pydantic and LiteLLM; Python standard-library `decimal`, `datetime`, `zoneinfo`, `argparse`, `json`, and `logging`.
- Development: pytest and Ruff.
- Optional later: Hypothesis for financial invariants and mypy if stricter static analysis proves useful.

Do not add LangChain/LangGraph, a guardrails framework, Redis, a database, a queue, a retry library, or a CLI framework for this fixture-scale prototype. Those may be justified by production requirements, but they would obscure the core decisions here.

## Definition of done

- The CLI runs offline against the supplied fixtures and produces one result per PNR.
- Tests pass without network access or model credentials.
- Formatting and lint checks pass.
- The output contains calculation and policy provenance.
- Code, README, example output, prompt, and `DESIGN.md` describe the same behavior.
- Known limitations and deliberate scope cuts remain explicit.

