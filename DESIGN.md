# Rebooking Copilot — Design

## Summary

The prototype is a **recommendation agent**, not an autonomous booking system. It evaluates each PNR against a fare snapshot and returns one of three outcomes: `REBOOK`, `DONT_REBOOK`, or `REVIEW`, together with the selected offer, estimated net saving, confidence, reason codes, and an explanation.

The risky core is deterministic. Code validates inputs, matches fares, calculates money, applies client policy, ranks candidates, and assigns the decision. LLM improves the explanation presented to a human agent, it cannot change the decision or any financial value. If there is no model set or model validation fails, a deterministic template is used instead.

The initial production rollout would require human approval for each recommendation. The system would initially have a shadow run, recored what it would have done and compare its decisions with the human selected outputs. The main benefit would be more pronounced once there is more ambigious data i.e. unstructured fare rules, differences between airline-branded fares, or relevant client approval history. Automation would later be used for low-risk areas.

## 1. Scope and assumptions

### Prototype scope

The fixtures model an **exchange of an existing ticket**. For this timebox the prototype:

- treats a PNR as one indivisible unit;
- supports the single-segment itineraries present in the fixtures;
- retrieves offers for the same route on the departure date and its adjacent dates, then applies schedule policy;
- calculates exchange economics using the existing ticket's change fee;
- applies a small, explicit policy;
- produces structured, auditable recommendations;
- optionally uses an LLM only to explain an already-computed result.

### Data assumptions

- Prices and change fees are per passenger unless the field is explicitly a total.
- `ticket.totalPaid` is the authoritative original booking value, but it is checked against `pricePerPassenger × passengers`; inconsistency causes `REVIEW`.
- The existing ticket's `changeFeePerPassenger` is the fee charged for the current exchange. It is multiplied by passengers, not itinerary segments.
- The candidate offer's `changeFeePerPassenger` describes the new ticket's future flexibility. It is not charged in the current exchange. If that offer is booked, it becomes the existing-ticket fee considered by later reshopping runs.
- A higher candidate future fee makes an otherwise worthwhile offer `REVIEW`; an equal or lower fee remains eligible. Equal-saving candidates with the same policy status prefer the lower future fee. An unsupported fee currency is retained as warning/review evidence without.
- The payer of the change fee is a commercial concern outside this engine. The fee is always a transaction cost in the reported net saving.
- Timestamps without offsets are interpreted as airport-local wall-clock times. Matching uses the departure airport's local calendar date. Production data should include an IANA timezone and normalized UTC instant; ambiguity is routed to `REVIEW`.
- `fares_feed.capturedAt` is a required timezone-aware business evaluation time. The static prototype evaluates recommendations as of that snapshot so fixture replay is reproducible; `generatedAt` is the actual UTC program run time. Production must enforce a business-approved freshness limit and always reprice before execution.
- For proof of concept, EUR/USD uses the fixed configured rate `1 EUR = 1.08 USD`. This is illustrative rather than executable market data, so cross-currency opportunities require `REVIEW`. An unsupported-currency alternative is retained as warning evidence; if every relevant candidate is unpriceable, the result is `REVIEW` with no invented saving.

### Business policy chosen for the prototype

Policies vary by client; these defaults are explicit examples and should be versioned configuration in production.

| Concern | Prototype decision |
|---|---|
| Minimum worthwhile saving | At least `max(25 in booking currency, 5% of totalPaid)`; equality passes |
| Schedule | Departure and arrival change must each be within ±2 hours, inclusive |
| Stops | A direct journey may not become connecting; candidate is rejected |
| Baggage | Included baggage must not decrease |
| Seats | Must cover every passenger on the PNR; never split the PNR |
| Carrier | Automatically acceptable only when the carrier is on the client's approved list; otherwise `REVIEW` |
| Refundability | Refundable → non-refundable requires `REVIEW` and client confirmation |
| Cabin/product | A downgrade may be considered only at ≥10% net saving and still requires `REVIEW`; otherwise reject |

`REVIEW` is intentional: deterministic data does not remove business judgment. It is used when an opportunity appears financially beneficial but acceptance depends on client policy, uncertain data, or operational risk.
Production policy may additionally compare journey duration or use client-specific departure and arrival windows.

### Clarifications received

- The fixtures model exchange only; cancel-and-rebook is outside the prototype.
- The PNR remains one indivisible unit.
- The existing ticket's exchange fee belongs in net saving regardless of who settles it.
- Airline-credit valuation is outside the prototype.

### Open questions for the business

The prototype makes the choices above so it can run, but these remain decisions to confirm before production:

- Does the client define equivalence by cabin family, branded fare, fare basis, operating carrier, or some combination?
- Should a carrier change, refundability loss, or sufficiently valuable cabin downgrade ever be automatically accepted, and does that vary by traveler or account?
- How should taxes, residual value, agency fees, and the production semantics of fare-rule fields affect authoritative exchange economics?
- What snapshot age and departure cutoff are acceptable for recommendations, and what authoritative source supplies execution-time availability and repricing?
- What currencies and settlement rules are supported, and which live FX source and timestamp should be authoritative?
- What volume, latency, model-data retention, human-review SLA, and realized-saving target should drive the production architecture?
- How should multi-segment itineraries, schedule changes, and scarce inventory shared by multiple PNRs be prioritized?

These should become versioned client policy or explicit product requirements.

## 2. Architecture and booking flow

The implementation separates the safety-critical functions:

| Module | Responsibility |
|---|---|
| `models.py` | Pydantic input, policy, money, evidence, and output models |
| `validation.py` | Envelope and record validation; quarantine malformed offers |
| `matching.py` | Journey indexing and route/date-bucket retrieval |
| `economics.py` | Decimal currency conversion, fees, thresholds, and net saving |
| `policy.py` | Schedule and product policy, review rules, ranking, reason codes, confidence |
| `reasoning.py` | Deterministic template and optional LiteLLM explanation adapter |
| `pipeline.py` | Per-PNR orchestration and record-level fault isolation |
| `cli.py` | Fixture loading, configuration, output, and exit status |

Flow per booking:

1. Validate the PNR and feed. A malformed top-level document or duplicate PNR/offer identifier fails the run visibly; a malformed individual PNR becomes `REVIEW` and does not stop other PNRs.
2. Retrieve the route's booking-date, previous-date, and next-date buckets from an index keyed by `(origin, destination, local departure date)`, then apply the inclusive departure and arrival ±2-hour checks and product policy.
3. Quarantine malformed offers and continue evaluating valid offers. Relevant malformed alternatives are exposed through non-authoritative warning evidence; only an entirely unusable relevant bucket becomes `REVIEW`.
4. Convert comparable amounts using an explicit FX quote and `Decimal` arithmetic. Evidence records the configured quote pair/rate separately from the applied pair/rate, including a high-precision inverse. Unpriceable alternatives receive warning evidence but do not erase a valid priceable decision.
5. Calculate and retain evidence for every candidate.
6. Apply hard constraints and review rules.
7. Rank candidates: select the highest-saving safe `REBOOK`; also expose materially relevant or higher-saving `REVIEW` alternatives so risk is not hidden.
8. Assign deterministic confidence and reason codes.
9. Generate an optional LLM explanation from immutable facts. Validate it and fall back to a template on any failure.
10. Emit one recommendation per PNR plus feed and policy.

### Decisions and monitoring

There are exactly three recommendation decisions:

- `REBOOK`: satisfies the active policy and is worth recommending. It still means “recommend”, not “execute”.
- `DONT_REBOOK`: the input is valid but no acceptable opportunity exists.
- `REVIEW`: a potentially useful result needs human judgment or data cannot safely support a decision.

Monitoring is scheduling metadata, not a fourth decision. A `DONT_REBOOK` PNR remains eligible for later checks. Near-threshold opportunities—within one percentage point of the configured percentage receive higher recheck priority and may include a deterministic trigger price. This avoids sending economically invalid changes to a human while preserving reshopping value.

## 3. Where the LLM is and is not

### Deterministic authority

Code owns:

- schema validation and normalization;
- matching and equivalence checks;
- every money and FX calculation;
- seat, schedule, baggage, stop, carrier, refundability, and cabin policy;
- threshold comparison, offer selection, reason codes, decision, and confidence;
- authorization to surface or later execute a recommendation.

These operations are exact, testable, cheap, fast, repeatable, and financially consequential. Using a model for them would reduce reliability without adding useful judgment because the fixture data is already structured.

### LLM role

When enabled, the model turns the locked recommendation facts into a concise explanation for a travel agent: why the option was selected or rejected, what changes for the traveler, and exactly what a reviewer must confirm. In future, deterministic aggregates of a client's historical approvals could be supplied as evidence—for example sample size, recency, and similarity—and the model could summarize that evidence. Historical behavior would support review, never silently become policy; explicit client policy always wins.

The MVP uses a small `ExplanationGenerator` interface with:

- `LiteLLMExplanationGenerator`, allowing hosted providers or local Ollama through one SDK;
- `TemplateExplanationGenerator`, the offline and failure fallback.

The LiteLLM Python SDK is sufficient for the prototype. A LiteLLM proxy or managed LLM gateway could later centralize routing, spend limits, retries, logging, and provider policy.
LiteLLM is loaded lazily only after `LLM_MODEL` selects the model-backed path, so template-only execution performs no model initialization or model-related network activity.

### LLM guardrails

- The model receives a minimal, structured facts object, not raw instructions embedded in booking data.
- The prompt states that the decision and values are final and must not be changed.
- Output is parsed into a strict Pydantic schema with bounded lengths and no extra fields.
- Numeric claims must come from the locked fact set, and phrases that contradict the deterministic decision are rejected.
- This is a lightweight defense-in-depth check only: it proves that a number appears among locked values, not that prose assigns it to the correct semantic field. The prose is not fully fact-verified.
- The returned explanation is advisory text only. Structured fields used by downstream systems come from deterministic code.
- Timeouts, provider errors, rate limits, invalid JSON, schema failures, or unsupported model capabilities cause a template fallback; they never fail the booking batch.
- The output records template/LLM source, prompt version, and configured model identifier. Production logging would additionally require sensitive-data minimization and an agreed retention policy.

A larger “LLM guardrails” framework is unnecessary for this narrow MVP. The most important guardrail is architectural: the model lacks authority over money and actions.
Future work could ask the model for small structured claims keyed to named evidence fields before rendering prose, but that is deliberately not added to this prototype.

The hosted-model payload contains only decision-relevant, non-traveler facts: decision, offer identifier, calculated totals, current and future fees, passenger count, confidence/reasons, monitoring trigger, and policy version. It excludes the PNR locator, itinerary, fare basis, raw ticket, and traveler data.

## 4. Correctness and money safety

For a fixture exchange:

```text
new fare total = offer price per passenger × passengers
current exchange fee total = existing ticket change fee per passenger × passengers
estimated net saving = original totalPaid − new fare total − current exchange fee total
```

Example `QX7T2A`: `480 − 300 − 100 = 80 USD`.

The candidate's future change fee is not subtracted from this calculation. It is converted separately for fare-quality comparison and retained in candidate evidence. All amounts use `Decimal`; money is rounded to cents, while an applied inverse FX rate keeps full decimal precision. Conversion evidence distinguishes `quotePair`/`quotedRate` from `appliedPair`/`appliedRate`. Production must add currency-specific minor units and a timestamped authoritative FX quote.

The prototype does not model voluntary cancel-and-rebook. A refund followed by a new purchase has different rules and economics, while carrier credit is not equivalent to cash because it may be carrier-locked, traveler-specific, and expiring. That distinction is documented but deliberately outside the timebox.

Before any real action, production must perform a fresh authoritative reprice and revalidate availability, fare rules, seats, fee, currency rate, and policy version. A recommendation is an estimate from a snapshot, not a reservation. If any material value changed, execution is aborted and the PNR is reevaluated.

Different PNRs may compete for the same scarce inventory. The fixture prototype evaluates them independently and makes no reservation. Production needs idempotency per PNR, a duplicate-work lock, fresh availability checks, and safe abort/retry. Optimizing allocation across competing PNRs is future work rather than something this recommendation engine should solve.

### Confidence

The MVP uses `HIGH`, `MEDIUM`, or `LOW` with explicit reasons:

- `HIGH`: complete, same-currency data and no review-sensitive trade-off;
- `MEDIUM`: valid calculation but a client/product decision requires review;
- `LOW`: material data uncertainty such as illustrative FX.

This is more honest than an uncalibrated float such as `0.93`. A numerical probability should be added only after defining a measurable outcome and calibrating it against sufficient historical recommendations and execution results.

## 5. Output and auditability

Each result includes at least:

- `pnr`, `decision`, `selectedOfferId`;
- `estimatedNetSaving` or a reason why it cannot be estimated;
- `confidence` and `confidenceReasons`;
- stable authoritative `reasonCodes`, non-authoritative `warningCodes`/`candidateWarnings`, and a human explanation;
- candidate calculation evidence and rejected/review alternatives;
- optional monitoring priority and trigger price;
- `evaluatedAsOf`, UTC `generatedAt`, and `fareSnapshotCapturedAt`;
- `policyVersion`, FX provenance, and reasoning source/provider/prompt version.
- batch-level `evaluationMode: HISTORICAL_SNAPSHOT`.

Given the same validated inputs, policy, snapshot, and FX table, the deterministic recommendation is replayable. In historical mode, `REBOOK` means “recommended as of this snapshot,” never permission to act today. `generatedAt` is actual UTC execution time and `fareSnapshotCapturedAt` is the feed timestamp. Every action requires current departure validation, availability, fare rules, fees, FX, and authoritative repricing. A production/live mode would reject departed itineraries and stale feeds; it is outside this MVP.

## 6. Scale, cost, and observability

### Planning assumptions

Assume 100,000 active PNRs and growth, with departures up to 365 days ahead. The exact numbers should be validated with the business; they are capacity-planning inputs rather than product truth.

Suggested baseline:

| Time to departure | Baseline recheck |
|---|---|
| 90–365 days | daily |
| 30–90 days | every 6 hours |
| 7–30 days | hourly |
| under 7 days | every 15 minutes, subject to an operational cutoff |

Near-threshold opportunities move one tier higher. In production, provider webhooks or fare-feed deltas should trigger affected journey buckets when available. Incremental polling is the fallback, with periodic full reconciliation to detect missed events. No assumption is made that every airline or aggregator provides a reliable webhook.

Offers are indexed by `(origin, destination, local departure date)`. Each booking reads three bounded buckets—its date and the immediately preceding/following dates—so a ±2-hour window works across midnight; deterministic departure and arrival policy then filters candidates. Normalized buckets can be cached by journey key and snapshot version. Cached results are never execution authority.

The LLM runs only for `REBOOK` and `REVIEW` explanations, not for every fare comparison or periodic check. Explanations can be cached by immutable fact hash. This controls latency and model cost while deterministic templates preserve availability.

For `D` deterministic evaluations/day and fraction `f` reaching `REBOOK` or `REVIEW`, model calls/day are approximately `D × f` before cache hits. A preliminary explanation envelope is about 250–500 input tokens and 80–150 output tokens per call, to be replaced by measurements.

### Observability

Track:

- PNRs evaluated, validation failures, quarantined offers, and batch completion;
- decision distribution and stable reason-code distribution;
- candidates matched and fare-snapshot age;
- estimated versus realized net saving;
- human approval, rejection, and override rates by policy version;
- execution-time reprice, rule-change, and availability failures;
- latency by pipeline stage and recheck backlog age;
- LLM call volume, latency, cost, validation failure, and fallback rate.

Alert on stale feeds, rising validation failures, decision-distribution drift, negative realized savings, and reprice failure spikes. Audit records include input snapshot identifiers, calculation inputs, policy version, reason codes, and model/prompt version where used.

## 7. Rollout toward automation

Start with all results requiring human approval. Run the engine in shadow mode for a few months and compare its proposed decisions with human outcomes, but do not treat raw agreement percentage as the only safety metric: measure false-positive rebooks, realized savings, policy overrides, reprice failures, and performance by client and reason-code groups.

After enough representative evidence, enable automation gradually for a narrow group such as same flight, same carrier, same cabin, same baggage/refundability, same currency, sufficient seats, high confidence, and a meaningful saving. Use versioned policies, small traffic percentages, kill switches, idempotency, fresh repricing, and automatic rollback on guardrail metrics. High-value, cross-currency, downgrade, refundability-loss, unusual, or low-confidence cases remain human-approved even if average model-human agreement is high.

## 8. Deliberate scope cuts and next work

Not built in the 2–3 hour prototype:

- automatic airline execution and rollback;
- cancel-and-rebook or valuation of carrier credits;
- splitting passengers from one PNR;
- multi-segment, round-trip, or open-jaw itinerary matching;
- live FX and settlement integration;
- persistent queues, database, distributed workers, and scheduler;
- provider webhook integrations and full reconciliation jobs;
- client policy administration and reviewer UI;
- extraction of unstructured fare rules;
- cross-PNR inventory allocation;
- calibrated probability confidence and historical preference learning.

The implementation priority after the prototype is: add execution-time revalidation, introduce persistent scheduling/monitoring, support multi-segment journeys, then run shadow mode before considering any autonomous action.

## 9. Expected fixture outcomes

| PNR | Decision | Estimated saving | Main reason |
|---|---|---:|---|
| `QX7T2A` | `REBOOK` | 80.00 USD | Same product; saving remains after current exchange fee |
| `LM9P4C` | `DONT_REBOOK` | -90.00 USD | Two-passenger fee plus product downgrade makes it uneconomic |
| `RT5K8B` | `REVIEW` | about 255.56 EUR | Illustrative fixed FX cannot authorize an executable comparison |
| `ZC3N1D` | `REVIEW` | 140.00 USD | Saving exists but refundability is lost |
| `HB6W9E` | `DONT_REBOOK` | best comparable offer -90.00 USD | Current fee removes the saving; continue monitoring separately |

`OF-5002` is also rejected: the existing $150 exchange fee still applies, its net is `260 − 150 − 150 = -40 USD`, and it adds a stop, changes product, and loses baggage.

## 10. AI usage

I used **Claude Code, ChatGPT, and Codex** for brainstorming, research, implementation, review, and audits, then checked each proposed change against the fixtures and clarifications. One concrete Claude-produced behavior applied a wall-clock freshness gate to the static feed, which turned every fixture into `REVIEW`. I corrected it because the assignment fixture is historical test data: the prototype now evaluates as of `capturedAt`, labels that mode explicitly, and still requires live repricing before action.

