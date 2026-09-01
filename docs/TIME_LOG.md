# Time log

## Pass 1 — original prototype (~2 hours 50 minutes)

- **~20 min — Requirement sensing.** Read the assignment and both fixtures by hand; Find
  edge cases (per-passenger fee multiplication, cross-currency
  comparison, refundability loss, a fee-dominated saving, and a cheap fare that is
  not a comparable product) and separated what to assume from what to ask.
- **~30 min — Written plan and clarifying questions.** Decided which open questions
  were parameters (configuration values) and which could change the design, then
  sent the questions to the interviewer.
- **~10 min - AI planning and audit** Went through a few rounds of questions and more
  edge cases using AI in planning mode as well as describing and creating rules for later code generation in `ENGINEERING.md` - pragmatic approach, defensive programming, expected stack, TDD, self-describing and succint code, modular to make it easier to reason about and with AI.
- **~45 min — Deterministic core.** Matching, `Decimal` money maths, policy gates,
  and a structured recommendation per PNR.
- **~15 min - AI assisted audits**
- **~30 min — Tests.** Per-fixture outcomes plus unit coverage of the gates.
- **~30 min — Documentation.** Design document, README, and the AI-usage note.

## If more time were available

Execution-time repricing and revalidation, persistent monitoring and scheduling,
multi-segment itineraries, live FX with settlement, and shadow-mode measurement
before any automation. These are listed as deliberate scope cuts in `DESIGN.md`.
