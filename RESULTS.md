# Results

The first strategy failed its out-of-sample test. This page reports the failure in full. The strategy showed
no edge after costs, so it is not traded.

## First out-of-sample test (October 2026): FAIL

### What was tested

One rule, written down and frozen before the test, with every constant fixed:

- a daily time-series momentum blend: trend votes over 21, 63 and 252 days; long or flat, never short;
- volatility-targeted to 20 % a year, with a rebalance band of 10 %; BTC and ETH, half of the capital each;
- the decision is made at the daily close and executed at the next day's open on a large EU spot exchange;
- a resting stop at 4 × the daily volatility, plus account-level loss limits;
- costs at **2 × the measured exchange costs**: 40 bps per side, 52 bps for stop exits, plus the worst measured
  slippage through a stop.

### Test window and rules

- **Window:** 2019-08-17 to the last closed daily bar at run time: 2,617 days, about 7.2 years. The rule was
  designed only on data before that window. Published momentum research that covers part of the window was
  known in advance. That bias favours a pass, so it does not weaken a FAIL.
- **One shot:** the test runs once. A FAIL closes the rule: no re-tuning, no changed window, no dropped coin and
  no removed cost.
- **Pass bars were fixed in advance.** A PASS needs every gate below.

### Gates

| Gate | Bar | Value | Result |
|---|---|---|---|
| Probabilistic Sharpe ratio (PSR) | ≥ 0.98 | 0.359 | **FAIL** |
| Deflated Sharpe ratio (DSR), 9 trials | > 0.95 | 0.030 | **FAIL** |
| Probability of backtest overfitting (PBO), 27-member grid, 12,870 splits | ≤ 0.20 | 0.072 | pass |
| Trades (episodes; 188 closed, 2 open at the end and marked to market) | ≥ 30 | 190 | pass |
| 12-month loss at the 98th percentile (Monte Carlo, 10,000 paths, 2 seeds) | ≤ 10.2 % of capital | 9.0 % | pass |
| Median 12-month return vs a 3.10 % savings account | ≥ 3.10 % | −7.8 % and −7.7 % (2 seeds) | **FAIL** |

### Other numbers from the same run

| Measure | Value |
|---|---|
| Sharpe ratio, annualised, at 2 × costs | −0.13 |
| at 1 × costs | +0.03 |
| at 3 × costs | −0.38 |
| Maximum drawdown | 34.8 % |
| Profit factor | 0.86 |
| Win rate vs the break-even win rate | 26.3 % vs 29.2 % |
| Time in the market | 53 % |
| Sharpe by period, annualised: 2019-08 to 2021 / 2022 / 2024 onward | +0.52 / −1.16 / −0.76 |
| Sensitivity grid (27 variants of the volatility target, band and stop width) | 3 of 27 above zero, the best at +0.14 annualised |
| The same rule on the 162 days just before the window | Sharpe +1.05 annualised |

The last row is the lesson. A short in-sample window looked good. Seven years of unseen data did not.

### Why it failed: the signal, not the plumbing

- **Too little edge, even without the account layer.** A probabilistic Sharpe ratio of 0.98 over this window
  needs an annual Sharpe ratio of about 0.8 (the fat tails of these returns raise the bar). The trend signal
  alone, before the account-level loss limits, reached about 0.23. The best of the 27 grid variants reached
  0.14, and the rule at 1 × costs reached 0.03.
- **The account layer made it worse, but it is not the cause.** The loss limits and stops took the result from
  about +0.23 to −0.13. Removing them still leaves the signal far below the bar.
- **The rule's own exits lost too.** Trades closed by the trend signal, not by a stop, lost money in total.
- **The trend idea failed in a second form.** A breakout variant tested alongside, as a counted sensitivity
  trial, scored −0.27 a year.
- **So execution work cannot rescue it.** Better fills, tighter cost modelling or a different stop would turn
  a losing rule into a cleaner losing rule. They help measure an edge; they do not create one.

### What happened next

- The rule is closed, and nothing is traded.
- Further rule families were searched on a wider coin set, with every variant counted as a trial. None has an
  edge after costs yet. The best deflated Sharpe ratio is 0.16 across 75 counted trials (0.12 at the upper count
  of 117), far below the 0.95 bar.
- **The test itself was tested (20 October 2026).** On synthetic markets with no edge, the search-and-deflate gate
  rarely passed noise. On synthetic markets with a planted edge of the size it needs, it found the edge only about
  two times in three, and rarely when the edge sits in a single signal. That gap is under review before the next
  go/no-go on 31 October 2026.
- **Stop rule.** The rules fixed before this test allow one new strategy family, pre-registered within ten
  weeks of this result, so by late December 2026. Any search after that needs a new, dated decision, and every
  search for a directional trading rule ends on 31 March 2027 at the latest, or earlier if the Sharpe ratio a
  new rule would need to clear our deflated test on the available history rises above 2. Every variant tried
  counts as a trial, so each new attempt raises that bar.
- **In either case** the next piece of work is a forward prediction log, not built yet. Each prediction will
  be written down and hashed before its outcome is known, then scored, so the record can be checked and cannot
  be edited afterwards. These will be paper predictions only: no money is traded.

## Why this result can be trusted

- **Frozen before the test.** The rules file was hashed (SHA-256) before the run. The hash is below, so the file
  can be verified if it is published.
- **Two implementations.** PSR, DSR and PBO were computed by our own code and by the open-source `purgedcv`
  library. PSR and DSR agree to 1e-6, and the gate used the lower value. For PBO, the two agree on the strict
  definition (0.052). The gate used our more conservative count (0.072), which also counts ties as losses.
- **Deflated for the number of trials.** The DSR deflates the Sharpe ratio for the 9 counted trials.
- **Checked by a second reviewer.** Reviewers who did not write the test recomputed the PSR, DSR, episode count,
  the 2 × cost Sharpe ratio, the period Sharpe ratios and the drawdown from the saved equity curve and exit table. The PBO and the two Monte Carlo gates
  need per-variant returns or a re-run, so they were checked for internal consistency only.
- **Costs measured, not assumed.** The costs come from measured fees and order-book depth, doubled. They were
  not fitted to any strategy return.

| Artefact | SHA-256 |
|---|---|
| Rules file, frozen before the run | `9352c2954a200c40413096dcacd4bda07707879a131e682dc1b50c21d5679aa0` |
| Input data | `458a1cde7fb4608ac75e3b6b48cfd8e895d2b0f85e1457e79ca59ec44d993404` |
| Result file, as saved | `e219bcdfca2ccf9b9f88ca84c564e8575baf69307304703a655a47293be25f21` |
| Result content (the result JSON without its own `result_sha256` field) | `ecfac932a2547872249dead42dfa1e568aa9c7a5f13be601a6e216936c79a928` |

## Limits of this result

It is one rule on two coins, on one venue, on daily bars, from 2019 to 2026. It does not show that momentum fails
everywhere. It shows that this rule, at realistic costs, did not beat a savings account on unseen data.
