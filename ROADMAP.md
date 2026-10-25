# Roadmap

Status on 25 October 2026. Own capital first, BTC and ETH spot, daily bars, one part-time founder.

Learn for free, prove with data, then risk a little. The first strategy failed its one-shot test, so no money is
traded. The search for a new strategy stopped under its own rule; the next step is a forward paper prediction
log. Paid signals come last, and only after a legal review.

## Time spent so far

About **40 hours** of active work over 7 days (13 to 19 October 2026): 25 working sessions and about 170 typed prompts.
Measured from the Claude Code session logs. A pause of more than 30 minutes counts as a break.

## Where it stands

| Step | Status |
|---|---|
| Research round: primary sources in the relevant languages, key numbers measured twice | done |
| Code foundation: Python 3.13, CI, point-in-time daily candle pipeline, order-book recorder | done |
| Engine: data, backtest, one shared outcome resolver, validation statistics | done |
| First out-of-sample test (16 October 2026) | **FAIL**, see [RESULTS.md](RESULTS.md) |
| Wider search for a new rule family, learning only (17 to 19 October 2026) | done: no family with an edge after costs |
| Test of the test on synthetic markets, with and without a planted edge (20 October 2026) | done: it rarely passes pure noise, but misses a real edge about one time in three; the gap is under review |
| Stop rule for the directional search (25 October 2026) | fired: with 14,528 earlier tests counted as trials, a new rule would need a Sharpe ratio above 2; the search stopped and the 31 October decision is no-go |
| Forward paper prediction log, hashed before each outcome | running; first outcomes on 31 October 2026 |

The rule was frozen and hash-committed before any out-of-sample data was touched. It ran once on 2,617 days it had
never seen: Sharpe −0.13 a year after doubled costs, probabilistic Sharpe ratio 0.36 (0.98 needed). The rule is
closed: no re-tuning. The raw gate output, with the hashes of the frozen rule, the input data and the result, is
in [docs/reports/m1-oos-report.md](docs/reports/m1-oos-report.md).

## What comes next

Dates are the earliest possible, not a forecast. Each later step happens only if the gate before it passes.

| When | Step | Gate |
|---|---|---|
| From October 2026 | Forward paper prediction log: each prediction is hashed before its outcome is known, then scored. No money is traded. | First outcomes on 31 October 2026. No claim before 50 non-overlapping outcomes per coin. A new strategy search only after a new, dated decision. |
| Only if a new, dated search finds a family that passes | Paper trading against live public order books for at least 6 months. Every prediction is hash-committed before its outcome; a calibration stop trips if live results leave the predicted band. | Results stay inside the backtest band and every drill passes. |
| At least 6 months later | A small live stake of own money. | Probabilistic Sharpe ratio ≥ 0.98, a capped 98th-percentile loss, legal and security checks done. Trading freezes at −6 % for review. |
| 2028 onwards | Scale the capital, then free signals with every loss posted and a hash-checkable record. | At least 12 months live inside the band. Paid signals only after a legal review. |

## What the data decided

- **A 98 % win rate is not trustworthy.** On 251 replayed public Telegram signals the real expectancy was −0.03 to
  −0.09 R. The certainty that matters is that the edge is real and the loss is capped.
- **The side that verifiably makes money** is market makers, liquidators and carry desks, not directional retail.
- **The best published next idea** (cross-coin trend) did not survive costs in any long-only or small version after
  its publication date in our own tests.
