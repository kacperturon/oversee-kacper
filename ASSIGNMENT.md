# Senior Software Engineer — Take-Home Assignment

## About this exercise

We build products for the air & hotel travel industry: reshopping/price assurance
(continuously re-pricing existing bookings to capture savings) and automation of
agency/TMC workflows. This exercise is drawn straight from that world.

A few things up front, because they change how you should approach this:

- **We work with AI daily and expect you to.** Use whatever AI coding tools you
  normally use. We are not testing whether you can type code from memory — we're
  interested in the decisions you make and the system you end up with.
- **We care more about your thinking than about lines of code.** The design
  document is the most important deliverable. A working prototype backs it up; it
  does not replace it.
- **We deliberately left some things undefined.** Deciding what to pin down,
  what to assume, and what to ask us about is part of the exercise. If something
  is unclear, you are welcome — encouraged — to email us with questions. How you
  handle ambiguity is something we look at.
- **Timebox it to ~2–3 hours.** You will not be able to build everything in that
  time, and we don't want you to. Choose what to build, what to stub, and what to
  only describe — and make that choice visible and defensible.

## The problem: a "Rebooking Copilot"

For a booked flight, the price of an equivalent seat often drops after ticketing.
If we detect that and rebook, the traveler (or the agency's client) saves money —
minus any change fee, and only if the new fare is actually acceptable.

We want an **AI agent that reviews existing bookings against currently-available
fares and, for each booking, produces a structured recommendation**: rebook or not,
to which fare, why, and how confident it is. A human agent may later act on that
recommendation — or, at higher volumes, we may act on some of them automatically.

You are given a small set of bookings and a snapshot of available fares as static
JSON. Build the agent that turns the first into recommendations using the second.

### What we provide (`./fixtures/`)

- `pnrs.json` — a handful of existing ticketed bookings (route, cabin, fare basis,
  price paid, passenger count, and inline fare-rule attributes: refundability,
  change fee, included baggage).
- `fares_feed.json` — a snapshot of currently-available fares for the same
  routes and dates. This is your reshopping-opportunity source. **Not every
  cheaper number is a good rebooking.**

A short primer if travel isn't your background: a fare has attributes beyond price
— which *cabin* (e.g. economy vs. a stripped-down "basic economy"), whether it's
*refundable*, what it costs to *change* an existing ticket (the change fee), and
what's *included* (e.g. checked baggage). Changing a booking may incur that change
fee. We are **not** testing airline trivia — everything you need is in the fixtures.

### What we intentionally did NOT define

This is not an oversight. Part of what we're evaluating is how you handle it. For
example (non-exhaustive): what makes two fares "equivalent" or a rebooking
"worth it"; how much traveler inconvenience (schedule change, extra stop, lost
baggage, lost refundability) is acceptable; whether the agent should recommend for
human approval or act autonomously; the real-world volume, latency, and cost
budget. Make your own call, ask us, or present options — but don't pretend these
questions don't exist.

## What to build

### 1. Design document (`DESIGN.md`) — primary deliverable

Cover what a senior engineer would want a teammate to know before trusting this in
production. At minimum:

- **Approach & architecture** — how the agent is structured; how a booking flows
  from input to recommendation.
- **Where the LLM is and isn't** — be explicit about which decisions are made by a
  model and which by deterministic code, and why. (There are good reasons to keep
  the model out of parts of this.)
- **Correctness & money safety** — how you decide a rebooking is genuinely
  beneficial and safe, and how you avoid acting on a wrong or hallucinated saving.
- **Scale, cost & observability** — this runs against a fixture of a few bookings
  today; assume it may run against a very large, growing book of bookings. What
  breaks, what it costs, how you'd know it's working (and know when it isn't).
- **Assumptions & open questions** — what you assumed, what you'd ask us, and where
  you'd offer the business a choice rather than decide unilaterally.
- **What you deliberately did not do**, and what you'd build next.

### 2. Prototype

A runnable program that reads the fixtures and emits a structured recommendation
per booking (rebook / don't, chosen fare, reasoning, confidence, estimated net
saving). Only the part you consider the risky core needs to actually work — stub
or fake the rest, and say so.

- **Model-optional:** the agent's "reasoning" step may call a real LLM *or* a
  stubbed/deterministic function — your choice. If you call a real model, don't
  commit any API keys; make it run (or degrade cleanly) without one. We do not
  score whether a live model was used; we score the structure and the prompt(s).
- No external/paid APIs and no network access should be required to run it.
- Include a short `README` with how to run it and an example of the output.

### 3. AI-usage note (a few sentences, in `DESIGN.md` or the README)

Which AI tool(s) you used, roughly how you drove them, and one example of
something the AI suggested that you **rejected or corrected**, and why.

## Ground rules

- **Time:** ~2–3 hours. Stop when you hit it; tell us what you'd do with more.
- **Language/stack:** your choice. Pick what lets you move fastest.
- **AI:** encouraged. This is not a closed-book exam.
- **Questions:** email `przemyslaw@oversee.biz` — we keep track of what people ask.

## How to submit

A git repo (or zip) containing `DESIGN.md`, your prototype code + `README`, and the
`fixtures/` you ran against. A short recording or note on how you spent your ~2–3
hours is welcome but optional.

## How we evaluate (so there are no surprises)

We score with a written rubric across: **problem framing & requirement-sensing**,
**architecture & the AI/deterministic boundary**, **correctness & money safety**,
**scale/cost/observability**, **prototype quality & pragmatism**, and
**communication & decision-making**. Depth and judgment on a few things beat shallow
coverage of everything. Surfacing a good question, or presenting a considered
variant, counts in your favor.
