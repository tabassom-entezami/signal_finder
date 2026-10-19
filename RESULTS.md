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

### What happened next

- The rule is closed, and nothing is traded.
- Further rule families were searched on a wider coin set, with every variant counted as a trial. None has an
  edge after costs yet. The best deflated Sharpe ratio is 0.16 across 75 counted trials (0.12 at the upper count
  of 117), far below the 0.95 bar.
- The next step is a forward paper-trading loop. It logs each prediction before its outcome is known, so any
  future claim can be checked against a record made in advance.

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
