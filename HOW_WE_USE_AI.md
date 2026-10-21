# How we use AI in signal_finder

This page explains the role a large language model (Claude, by Anthropic) plays in the
product. It describes principles only. It does not describe a trading strategy, its
parameters, its test results or when anything goes live.

## The short version

Deterministic code does the maths. In the design below, Claude reviews a finished candidate and gives a
second opinion, in a fixed format, with its sources named. Claude never computes,
changes or invents a number, and it never places an order. This layer is designed but not built yet: it is
built only once a base strategy passes its own test, and it is kept only if it beats the code alone (section 6).

```
 market data ──► deterministic engine ──► candidate (all numbers fixed)
                                               │
                                               ▼
                               structured, evidence-tagged summary
                                               │
                                               ▼
                                   Claude (meta-labeler, planned)
                                               │
                                               ▼
                                 validator chain (code) ──► rejected
                                               │
                                               ▼
                                     published label + reasons
```

## 1. Code owns every number

Prices, indicators, regime, score, entry, stop, target and position size come from
plain, tested, versioned code. The same inputs always give the same outputs, so every
number can be reproduced and audited. The model is not asked to forecast prices or to
size positions, because a language model is not a reliable calculator and its output
can vary between calls.

## 2. Claude is a meta-labeler

Planned design, not running yet: sections 2 to 6 and 8 describe the layer as it will be built.

"Meta-labeling" is an established idea in quantitative finance: a primary model
proposes a trade, and a secondary model decides whether that proposal is worth acting
on. In signal_finder the primary model is the deterministic engine; Claude is one
possible secondary model.

For each candidate Claude returns a small label set such as **TAKE**, **SKIP** or
**REDUCE**, plus:

- a bounded confidence adjustment (it can nudge, never override);
- the key drivers behind its view;
- the signals that contradict it;
- the conditions that would invalidate the setup;
- the evidence IDs behind every claim it makes.

A SKIP is not discarded. Skipped candidates are tracked the same way as taken ones, so
the value of the label can be measured honestly.

## 3. Structured in, structured out

Claude does not read raw web pages or free-form market chatter as instructions. It
receives a structured summary built by code, in which every fact carries an evidence
ID that points back to its source record and timestamp. It answers in a fixed JSON
schema. Anything that does not parse is rejected; there is no "best effort" reading of
free text.

## 4. Every output is validated before use

A chain of code checks runs on every answer before anything is published:

1. **Schema:** the answer has exactly the expected fields and types.
2. **Evidence:** every claim cites evidence IDs, and every cited ID exists in the input.
3. **No new numbers:** every number in the output must already appear in the input.
   Claude may quote numbers; it may not produce new ones.
4. **Risk limits:** the label cannot loosen any limit set by the deterministic risk
   layer. It can only keep or reduce exposure.

An answer that fails any check is dropped and logged. The candidate then follows a
fixed fallback path defined in code, never the model's text.

## 5. Prompts are versioned like code

Each prompt has a version and a content hash. Every stored output records the prompt
version and the model that produced it, so a label can always be traced to the exact
instructions that created it. A prompt change is a new version, reviewed and measured
like any other code change, never an in-place edit.

## 6. Measured before it is trusted

Claude's label is treated as an unproven feature until the data says otherwise:

- Every label is recorded before the outcome it predicts is known.
- Labels are scored against real outcomes, including the ones it said to skip.
- Confidence is checked for calibration: when it says "likely", is it right that often?
- Tests on historical data are treated as a smoke test only. A model may have seen
  that history during training, so hiding dates does not stop it from "remembering".
  Only periods after the model's training cutoff count as real evidence, and they are
  reported separately.
- Schema failures, rejected claims, latency and cost per signal are tracked.

If the label does not add measurable value, the engine runs without it.

**Status: designed, not built.** A meta-labeler needs a base strategy whose decisions it can improve, and the
first strategy failed its out-of-sample test. The model layer is built only after a base strategy passes its own
test and its decisions have been logged with their outcomes for a while. It is then tested once, under rules
written down before the test, against the code-only engine on the same decisions. If it does not beat the code
alone, it is deleted, not tuned until it looks better. A losing strategy with a well-written explanation of why
it lost is still a losing strategy.

## 7. What Claude does not do

- It does not place, change or cancel orders, and it has no access to exchange
  accounts or keys.
- It does not change any number produced by the engine.
- It does not follow instructions found inside the data it reads. Fetched text is
  treated as data only.
- It does not give personal financial advice. The product is informational.

## 8. Failure and cost

If the API times out or errors, the engine falls back to a deterministic path defined
in code; nothing waits on the model to stay safe. Calls are budgeted: the right model
size is chosen per task, repeated context is cached, and non-urgent work can be sent
in batches. Cost per signal will be measured, not estimated.

## Why this design

Language models are good at weighing many pieces of evidence and explaining a view in
plain words. They are weak at arithmetic, consistency and knowing the limits of their
own knowledge. This design uses the first strength and fences off the weaknesses with
code: the model can only add a checked, sourced opinion on top of numbers it cannot
touch.
