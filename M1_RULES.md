<!-- Redacted copy. Research record, not advice. -->

> **Redacted copy of the M1 rules file frozen on 2026-10-16.** Research record, not investment advice.
> Original file sha256 `9352c2954a200c40413096dcacd4bda07707879a131e682dc1b50c21d5679aa0` (the hash in RESULTS.md). This copy's own
> sha256 is in RESULTS.md, since a file cannot hold its own hash. Money amounts became % of the starting
> capital C0 (the rule's own unit), internal references became `[ref]`, and private notes, the warm-up
> source, measured venue cost detail and the stake size were removed. Every frozen constant and formula of
> the rule (sections 2 to 4 and 8) is unchanged except for those units. Below the line, the text keeps the
> original's 307 lines in order; the table lists every changed line by its original number.

| Original line | Redaction |
|---|---|
| 1 | internal reference |
| 3 | internal role name, internal reference |
| 4 | private path or note |
| 5 | internal reference |
| 10 | internal stage tag |
| 12 | internal reference |
| 14 | internal stage tag |
| 19 | money -> % of C0, internal reference |
| 21 | internal stage tag |
| 22 | internal stage tag, internal role name |
| 23 | internal stage tag |
| 24 | private path or note |
| 31 | internal stage tag, internal reference, private path or note |
| 32 | internal stage tag, internal reference |
| 33 | internal stage tag, internal reference |
| 34 | internal stage tag, internal reference |
| 35 | internal stage tag, internal reference |
| 36 | internal stage tag, internal reference |
| 40 | internal reference |
| 58 | private warm-up source |
| 81 | internal reference |
| 102 | internal reference |
| 104 | internal role name |
| 109 | money -> % of C0, internal reference |
| 111 | internal reference |
| 112 | money -> % of C0, internal reference |
| 115 | internal reference |
| 119 | internal reference, internal role name |
| 122 | private path or note |
| 124 | internal reference |
| 128 | private path or note, internal reference |
| 129 | money -> % of C0 |
| 133 | order size (would reveal the stake) |
| 135 | measured venue cost detail, internal reference |
| 136 | measured venue cost detail, internal reference |
| 137 | measured venue cost detail, internal reference, order size (would reveal the stake) |
| 138 | measured venue cost detail, internal reference |
| 139 | measured venue cost detail, internal reference |
| 140 | private path or note |
| 143 | order size (would reveal the stake), money -> % of C0 |
| 146 | internal reference |
| 157 | private warm-up source |
| 167 | private path or note |
| 168 | private path or note, internal reference |
| 169 | internal reference |
| 177 | internal reference |
| 178 | private path or note |
| 179 | internal reference |
| 181 | money -> % of C0, internal reference |
| 182 | money -> % of C0, internal reference |
| 196 | internal reference |
| 198 | internal reference |
| 203 | internal reference |
| 204 | internal reference |
| 205 | internal reference |
| 206 | internal stage tag, internal reference |
| 211 | private path or note |
| 214 | internal reference |
| 216 | internal reference |
| 219 | private path or note |
| 228 | internal reference |
| 229 | private warm-up source, internal reference |
| 234 | private path or note |
| 236 | private path or note |
| 239 | internal reference |
| 241 | private path or note |
| 245 | internal reference, private warm-up source |
| 256 | money -> % of C0 |
| 258 | internal reference, internal role name, internal stage tag |
| 259 | internal role name, internal stage tag |
| 260 | money -> % of C0, internal role name, internal stage tag |
| 261 | money -> % of C0, internal reference, internal role name, internal stage tag |
| 262 | internal role name, internal stage tag |
| 263 | internal role name, internal stage tag |
| 264 | internal reference, internal role name, internal stage tag |
| 265 | money -> % of C0, internal role name, internal stage tag |
| 269 | internal merge or coverage note |
| 270 | internal merge or coverage note |
| 271 | internal merge or coverage note |
| 272 | internal merge or coverage note |
| 273 | internal merge or coverage note |
| 274 | internal merge or coverage note |
| 275 | internal merge or coverage note |
| 281 | order size (would reveal the stake), money -> % of C0 |
| 282 | internal reference, private warm-up source |
| 283 | money -> % of C0, internal stage tag |
| 287 | private path or note, internal reference |
| 288 | private path or note |
| 289 | private path or note |
| 291 | private path or note |
| 295 | private path or note |
| 298 | money -> % of C0 |
| 300 | money -> % of C0 |
| 306 | internal merge or coverage note, private path or note |
| 307 | internal merge or coverage note, internal reference, internal stage tag |

---

# M1 rules file (redacted copy): rule B1.1.0-spot, frozen before any out-of-sample run

- **Owner:** [role]. A risk review is applied to the stop, the account layer and the loss gate.
- **Written:** 2026-10-16 (UTC). **Status:** frozen when the SHA-256 of this file was recorded [private paths].
- **Scope:** this file pre-registers the first out-of-sample gate and the loss gate for this rule on Bitvavo. It is not the full product gate [ref], which covers five perps, ≥ 100 trades and Claude.
- **Rule of change:** nothing in this file changes after the hash. A later edit is a new pre-registration with a new hash, and every trial already run stays counted.

## §0 Summary (≤ 250 words)

- **One rule, one shot.**
  - B1.1.0-spot is the frozen T1 blend: 21/63/252-day trend votes, long/flat, vol target 0.20, rebalance band 0.10, BTC and ETH as 50/50 half-sleeves.
  - It adds three things: next-bar-open execution on Bitvavo EUR books; signals computed in USD terms (EURconv); and a resting EUR-book catastrophe stop at 4 × the daily σ.
  - Costs are 2× measured: 40 bps per side on the USDC path and 52 bps on stop exits, plus the worst measured overshoot.
- **Gates on the OOS window**, from 2019-08-17 to the last closed bar:
  - PSR ≥ 0.98;
  - DSR > 0.95 with N = max(9, the trial-log count);
  - PBO ≤ 0.20;
  - ≥ 30 episodes;
  - 12-month P98 loss ≤ 10.2 % of C0 with gaps, the −6 % freeze and a −9 % floor;
  - median 12-month PnL ≥ the 3.10 % savings rate.
- **Why N = 9 and not 7 + 1.** The frozen rule is not T1 any more: it has the stop, open fills, quantity holding and the EURconv series. So it is a new counted specification (T5), next to the Donchian sensitivity trial (T6).
- **Stop design.** Vol targeting combined with a σ-scaled stop caps the stop loss per coin at 2.09 % of the sleeve plus overshoot. Room-bound stops keep equity above max(94 % of peak, 91 % of C0) even after the worst measured overshoot. One unprotected −13.28 % halt gap per year is stress-tested.
- **Honest prior.**
  - Training Sharpe is 0.43 at 2× costs. Passing needs an OOS Sharpe of about 0.8 or more: 37–81 % odds before deflation [private note].
  - On failure no money moves, B is never re-tuned, and the savings account is the default.

## 1. Decisions

| # | Decision | Stage | Basis |
|---|---|---|---|
| P1 | Universe: BTC and ETH on Bitvavo. Returns, fills and stops use the EUR books (BTC-EUR, ETH-EUR, first bars 2019-03-08). Signals use USD-terms closes (EURconv). Live entries go through USDC, costed below. | [ref] | [ref] |
| P2 | Frozen rule = §2. No search space: there are no free parameters. | [ref] | [ref] |
| P3 | The catastrophe stop is a resting stop-market on the coin's EUR book, reset every day at 4 σ_d. For this rule it **replaces** the earlier 2 × ATR stop. | [ref] | [ref] |
| P4 | The G1 series is the **account path A**, which models every account-layer rule that changes positions. The strategy layer S is reported, not gated. | [ref] | [ref] (backtest = live) |
| P5 | N = max(9, counted lines in the trial log). The run aborts if T5 and T6 are missing from the log. | [ref] | [ref] |
| P6 | The earlier in-sample bar (best-of-grid ≥ 1.5 after 2× costs) is **not met** (T1: 0.43). The rule proceeds anyway under the loss-gate decision [ref], because nothing is spent and the 477-day in-sample Sharpe has a standard error of ≈ 0.87. The M1 product verdict stays separate. Flagged once [ref]. | [ref] | [ref] |

## 2. The rule, exactly as code implements it

All times are UTC. Bar t is the Bitvavo 1d candle covering [t 00:00, t+1 00:00). Its close is known at t+1 00:00. The still-open bar is dropped, as the fetcher [ref] already does.

```
# ---- constants (frozen) ----
COINS        = ("BTC", "ETH");  HALF = 0.5
LOOKBACKS    = (21, 63, 252)          # bars
VOL_WIN      = 30;  ANN = 365         # 30 daily log returns, ddof = 1, x sqrt(365)
VOL_TARGET   = 0.20;  BAND = 0.10;  K_STOP = 4.0
COST_SIDE    = 0.0040                 # 2x, per side, USDC path: entry, rebalance, signal exit, flatten
COST_STOP    = 0.0052                 # 2x, per side, EUR-book stop exit
G            = {"BTC": 0.0351, "ETH": 0.0647}   # stop overshoot below the stop price (§4)
FREEZE_DD    = 0.06;  FLOOR = 0.91;  REVIEW_DAYS = 7;  WEEKLY_LIMIT = 0.04
HALT_GAP     = 0.1328                 # unprotected gap, MC stress only (§4)

# ---- signal series u[i,t] (USD terms, close only) ----
if t >= 2020-12-10:            u = BitvavoEUR.close[i,t] / BitvavoUSDC_EUR.close[t]      # same bar (EURconv)
elif t >= 2019-03-08:          u = BitvavoEUR.close[i,t] * ECB_EURUSD[last fix date <= t]
                               # fix published ~14:00 UTC on day t, before the close; weekends and holidays forward-filled
else:                          u = WarmUp[i].close[t]                                 # private warm-up source, all before OOS

# ---- decision at close t, per coin i ----
if any input bar of coin i at t is missing:          # gap reported by the fetcher, never filled
    no decision for i at t; keep q_i and re-place the same stop rule at the next open  # L8 analogue
sigma_d = std(diff(log u[i, t-30 .. t]), ddof=1);  sigma_a = sigma_d * sqrt(365)
v       = (sign(u_t - u_{t-21}) + sign(u_t - u_{t-63}) + sign(u_t - u_{t-252})) / 3     # sign(0) = 0
tau     = max(v, 0) * min(1, VOL_TARGET / sigma_a)                                       # 0 <= tau <= 1
w_new   = tau if (abs(tau - w_held) >= BAND or (tau == 0 and w_held > 0)) else w_held
account state: REDUCING -> w_new = min(w_new, w_held);  FROZEN or STOPPED -> w_new = 0 and no entries

# ---- execution at open of bar t+1 (live: placed 00:00:30-00:05:00 UTC; no orders 23:58-00:00) ----
if w_new != w_held:
    target value X_i = w_new * HALF * E_t        # E_t = sleeve equity in EUR at close t
    trade (X_i - q_i * open_{t+1}) at open_{t+1};  cost = COST_SIDE * |traded value|
    orders > 25 % of equity are split into child orders <= 25 % (L4 is an order-size cap)
    w_held = w_new
else: hold quantity q_i (no trade, no cost; weight drifts with price and equity)

# ---- stop, placed with the fill (L2, <= 5 s), re-placed at every daily open ----
for each coin with q_i > 0:
    d_i = K_STOP * sigma_d[i,t]                  # account layer: d_i = min(d_i, d_room)
    S_i = open_{t+1} * (1 - d_i)
    if low_{t+1} <= S_i:                          # checked before the close mark; SL first [ref]
        exit q_i at S_i * (1 - G_i);  cost = COST_STOP * exit value;  q_i = 0;  w_held = 0
        # re-entry is possible from the next decision on; it is a new episode

# ---- mark ----
E_{t+1} = cash + sum_i q_i * close_{t+1};  r_{t+1} = E_{t+1} / E_t - 1    # EUR, idle cash earns 0
```

- **Constraints:**
  - Long/flat only, with no leverage.
  - At trade time, per-coin exposure is at most 50 % of the sleeve (w ≤ 1 of its half) and total exposure at most 100 %. Between trades the held quantity drifts with price; there is no drift-trim rule.
  - Two coins at most (L5 count).
- **Stop bound (approximate, a priori, not fitted):**
  - At the moment of sizing, X_i = ½ E × 0.20/σ_a, so the strategy stop loss per coin is ≈ ½ × 0.20/√365 × K = 0.523 % × K of equity, plus X_i (G_i + COST_STOP).
  - K = 4 gives ≈ 2.09 % per coin, or ≈ 4.19 % for both coins on one day plus overshoot (≈ 1.5 % at a typical ⅓ exposure). That is inside the 6 % freeze room from a fresh peak in the typical case.
  - It is not a hard bound: the held weight can sit up to BAND above τ, prices and σ drift after sizing, so the worst two-coin day can reach ≈ 7.7 %. **The hard guarantee is the §3 room-bound stop**, which caps equity at L_t after the worst measured overshoot.
  - K was chosen from this approximation alone, not from any price history.
- **Donchian sensitivity trial T6** (one counted trial, diagnostic only):
  - Same series, sizing, band, stop, costs, execution and account layer as above.
  - The signal state s_i is set to 1 when u_t > max(u_{t−20..t−1}). It is reset to 0 when u_t < min(u_{t−10..t−1}). Otherwise s_i is unchanged.
  - tau = s_i × min(1, 0.20/σ_a).
  - **Use of the result:** T6 cannot be promoted from this run. If T6's OOS Sharpe is < ½ of T5's, the report flags the result as "lookback-dependent" for the next decision [ref]. The verdict does not change.

## 3. Account layer (path A, the Monte Carlo, and live)

| Element | Exact rule | A-priori basis |
|---|---|---|
| Peak | max of E at daily closes since the last (re)start | live L7 uses the same close-based peak, so backtest = live |
| Limit level | L_t = max((1 − FREEZE_DD) × Peak, FLOOR × C0). The floor applies in the MC and live; it is not applied in path A over 7 years (see below) | −6 % freeze [ref]; the floor at −9 % of C0 leaves 1.2 % of C0 under the 10.2 % cap for unmodelled gaps and rounding |
| Room-bound stop | after the open fills, d_room = (E⁺ − L_t − Σ V_i (G_i + COST_STOP)) / Σ V_i, with V_i = q_i × open and E⁺ = equity after fills; each stop uses min(K σ_d, d_room); if d_room ≤ 0, flatten at that open | equity stays ≥ L_t even with the worst measured overshoot on every fill |
| Freeze | trigger: a room-bound stop fills, or E_t ≤ 0.94 × Peak at a close → remaining positions are sold at that bar's close (COST_SIDE) → FROZEN, no trades for 7 days → restart at the first decision after day 7 with Peak := E | 7 days = one weekly scorecard cycle [ref], where the founder reviews |
| Floor | if the binding level was FLOOR × C0 → STOPPED: no restart. Live: the live stage ends and a new decision is needed | a lifetime cap for the C0 sleeve |
| L6 weekly | from the Monday 00:00 UTC decision, W = E_now / E_week_start − 1, checked at closes and stop fills; W ≤ −4 % → REDUCING (reduce-only, stops active) until the first decision of the next ISO week | spec L6; re-enable = founder's weekly review |
| L6 daily | −2 % → REDUCING to UTC day end. Decisions only happen at 00:00 UTC, so it never binds; the code asserts this | spec L6 |
| Not modelled | L8–L12 (data staleness, reconciliation, dead-man, kill, deploy): operational [ref] | [ref] |

**Path A over the OOS window.** Path A runs C0 from 2019-08-17 with every element above except the floor. The floor would end the series, which makes a Sharpe ratio undefined. The floor is evaluated by the Monte Carlo instead.

## 4. Gap treatment

1. **Overshoot on every stop fill.** Fill = S × (1 − G_i), applied to every fill (worst case, not sampled).
   - G is the largest trade-to-trade drop on the coin's Bitvavo book in the 2025-10-10 cascade: BTC-EUR −3.51 % ([private note], grade A).
   - ETH-EUR has not been measured, so the thinner ETH-USDC −6.47 % is used as a pessimistic proxy.
   - This is the bar gap price [ref].
2. **Unprotected halt gap** (a maintenance or auction reopen, or an outage where no resting stop can act). It is applied once per 12-month MC path, on a uniformly random day.
   - Every held coin loses 13.28 % at the open: the largest measured EUR-book drop (XRP-EUR, 2025-10-10). No Bitvavo halt-reopen gap has been measured; this is a grade C proxy and is open item O2.
   - If 13.28 % ≥ d_i, the stop fills at the gapped price with COST_STOP and no extra G. If 13.28 % < d_i, the position stays and the rest of the bootstrapped bar (high, low, close) is scaled from the gapped open.
3. **Operational events** (venue failure, key theft) are not in this market gate. The run reports a total-loss P98 next to it, using [private note] event rates.
4. **Unit.** Loss is measured in % of C0. The cap is 10.2 % of C0, so EUR/USD drift does not move the gate.

## 5. Costs (2× measured Bitvavo, frozen)

| Leg | 1× measured | Source | 2× (frozen) |
|---|---|---|---|
| EUR↔USDC conversion, each way | [redacted] | [ref] | — |
| Coin on USDC, taker | [redacted] | [ref] | — |
| Impact incl. half-spread | [redacted] | [ref] | — |
| **USDC path per side** (entry, rebalance, signal exit, flatten) | [redacted] | [ref] | **40 bps** |
| **Stop exit on the EUR book** | [redacted] | [ref] | **52 bps** + G (§4) |
| Idle cash | EUR, 0 % (MiCA: no stablecoin interest) | [ref] | 0 |

- **Sensitivity:** 1× and 3× are reported for path A. They are information only.
- **Order size:** [redacted]; the measured impact is an upper bound for it.
- **Disclosure:** these costs and the G values are execution parameters measured in 2025-10 and 2026-10. They were chosen pessimistically and were not fitted to any strategy return.

## 6. Success label (identical in the backtest, demo, live and public record; [ref])

- **Unit:** one episode per coin. It starts at the fill that takes q_i from 0 to > 0 and ends at the fill that takes q_i back to 0, whether by signal exit, stop, room stop, freeze flatten or STOPPED. Rebalances stay inside the episode. A stop followed by re-entry starts a new episode.
- **Episode PnL:** EUR mark-to-market PnL of that coin's position over the episode, minus every cost of the episode (entry, rebalances, exit or stop including overshoot).
- **Success:** episode net PnL > 0 at the rule's own exit.
- **Open at window end:** an episode still open at the end of any evaluation window (the OOS end, a demo month, a scorecard week) is marked at that close minus COST_SIDE × value, and it is counted.
- **Time-outs:** the rule has no time stop. If one is ever added, a time-out resolves at the mark and is never dropped from a denominator.
- **Publication:** the win rate is published next to the break-even win rate (avg loss / (avg win + avg loss)) and the expectancy. There is one column per exit reason: SIGNAL_EXIT, SL, SL_ROOM, FREEZE, STOPPED, OPEN_MARKED.

## 7. Windows

- **Training and warm-up:** bars up to and including 2019-08-16, which closes at 2019-08-17 00:00 UTC. Data before 2019-03-08 is used for signal warm-up only (a private warm-up source).
- **OOS:** bars from 2019-08-17 to the last closed bar at run start.
  - Path A starts flat at C0 and executes the first decision (made at the close of bar 2019-08-16) at the open of 2019-08-17. Entry costs are charged.
  - The expected length at a run in 2026-10 is ≈ 7.2 years (≈ 2,620 days).
- **One shot.** One re-run is allowed only for a code defect, and only if:
  - a failing test on synthetic data proves the code violated this text;
  - no constant changes;
  - both results are published;
  - the fix is logged as `T5-fix` (not counted, as with T1-fix).
- **Contamination statement (checked 2026-10-16 10:24 UTC):**
  - The reports folder did not exist before this file, and the data folder held only `.gitkeep`.
  - There is no result for this rule in the private task notes.
  - The fetcher's live smoke test [ref] fetched BTC-EUR candles for 2026-09-01 to 2026-10-15 but ran no rule.
  - The author loaded no BTC or ETH price after 2019-08-16.
  
## 8. Gates (all of them; account path A unless stated; 2× costs)

| # | Gate | Exact computation |
|---|---|---|
| G-a | **PSR ≥ 0.98** | PSR = Φ(SR̂ √(T−1) / √(1 − γ₃ SR̂ + (γ₄ − 1)/4 SR̂²)) against SR* = 0, with daily SR̂, non-excess γ₄ and T = number of OOS days. Computed by purgedcv and by our own function; they must agree to 1e-6, and the gate uses the lower value |
| G-b | **DSR > 0.95** | SR₀ = √V × ((1 − γ) Φ⁻¹(1 − 1/N) + γ Φ⁻¹(1 − 1/(N e))), with γ = 0.5772 and DSR = PSR at SR* = SR₀. N = max(9, counted trials in the trial log). V = max(cross-trial variance of the daily Sharpes logged per trial, (1 − γ₃ SR̂ + (γ₄ − 1)/4 SR̂²)/(T − 1)). Information only: DSR at N = 35 (9 + 26 grid members) |
| G-c | **PBO ≤ 0.20** | CSCV on the path-A OOS daily returns of a 27-member grid: VOL_TARGET {0.15, 0.20, 0.25} × BAND {0.05, 0.10, 0.15} × K_STOP {3, 4, 5}. S = 16 equal contiguous blocks (remainder dropped from the first), all 12,870 splits, metric = Sharpe. The grid serves PBO and the sensitivity map only; no member can replace the centre, so it is not counted in N [ref] |
| G-d | **≥ 30 OOS episodes** | §6 unit, BTC and ETH pooled, open episodes counted |
| G-e | **P98 loss ≤ 10.2 % of C0** | Moving-block bootstrap of the OOS daily records. Each record holds tau_i, σ_d,i and open/high/low/close relative to the previous close, for both coins on the same day index. Blocks of 20 days, 365-day paths, 10,000 paths, seeds 1 and 2. Each path starts flat at C0 and runs §2 with the full §3 account layer, the floor included, and the §4 gaps (overshoot on every stop, one halt gap). Loss = −(E₃₆₅ − C0)/C0. Pass if both seeds ≤ 10.2 % |
| G-f | **Savings hurdle** | the median 12-month PnL of the same MC is ≥ 3.10 % of C0 in both seeds |

**Reported, not gated:**
- the strategy layer S (no account layer);
- results per coin and pooled;
- sub-windows 2019-08-17 to 2021-12-31, 2022, and 2024-01-01 to the end;
- 1× and 3× costs;
- training-window metrics of T5 from the same run, on 2019-03-08 to 2019-08-16 only (Bitvavo EUR returns start 2019-03-08; ≈ 160 days, information only);
- T6 metrics;
- max drawdown, profit factor, time in market, exit-reason columns;
- MC P98 of max drawdown, P(freeze), P(STOPPED);
- the 27-member sensitivity map;
- the total-loss P98 including operational events.

## 9. Verdict, kill rule and what happens next

- **PASS:** every gate G-a to G-f is met. The first gate is then passed, and the paper stage [ref] starts: ≥ 6 months of paper trading on Bitvavo's live books, inside the backtest's CPCV interval. No money moves before every live condition [ref] is met and the founder says go.
- **MARGINAL:** OOS Sharpe > 0 and PSR > 0.90, but any gate fails.
- **FAIL:** OOS Sharpe ≤ 0, PSR ≤ 0.90, or the edge disappears at 2× costs.
- **Kill rule** (MARGINAL or FAIL):
  - nothing is spent, and the founder's money stays in the 3.10 % savings account;
  - the rule is closed: no re-tuning, no changed window, no dropped coin, no removed cost, and no promotion of T6 or of a grid member [ref];
  - iteration is allowed only as a new pre-registered family with a published effect. Its N starts at ≥ 10, and the 10-week time box runs from this report [ref];
  - the report goes to the founder and to the public log, with its hash.
- **After a live start:** the kill switch [ref] applies, and so do the −6 % freeze and the §3 floor. A STOPPED state ends the live stage.

## 10. Exact command and data for the OOS run

**Preconditions.** The run code must enforce all of these and refuse to run otherwise:
1. `shasum -a 256 -c <rules file>.sha256` passes. The same hash is compiled into the run module as `PREREG_SHA256`.
2. **Loader lock:** any bar at or after 2019-08-17 is refused unless the call comes from `m1-oos` with a matching hash. Development uses synthetic data only.
3. These tests are green:
   - the reference tests [ref]: PSR 0.913 / 0.953, PBO on noise 0.5 ± 0.1, and DSR at N = 1 equal to PSR;
   - vectorised parity to 1e-9;
   - the 15-bar fixture [ref] through three adapters;
   - a look-ahead test proved red;
   - a test proving that an EURconv bar uses only same-bar or earlier inputs.
4. The trial log holds T1–T4, O1–O3, T5 and T6, each line tagged with this hash (T1–O3 are not re-run). The run reads N from that file.
5. A clean working tree, with the commit hash written into the output. The founder commits.

**Data** (all public, no key; fetched after the hash, never inspected before the run):

| Input | Range | Source and licence | Command |
|---|---|---|---|
| Bitvavo BTC-EUR, ETH-EUR 1d | 2019-03-08 → last closed | Bitvavo public API | `uv run python -m signal_engine fetch-candles --symbol BTC/EUR --since 2019-03-08` (same for ETH/EUR) |
| Bitvavo USDC-EUR 1d | 2020-12-10 → last closed | Bitvavo public API | `... fetch-candles --symbol USDC/EUR --since 2020-12-10` |
| ECB EUR/USD reference rate (EXR.D.USD.EUR.SP00.A) | 2019-03-01 → 2020-12-10 | ECB Data Portal, free reuse | `fetch-ecb` [ref] |
| Warm-up source, BTC and ETH 1d | before 2019-03-08 (warm-up only) | [redacted] | [ref] |

**Run:**
```
uv run python -m signal_engine m1-oos \
  --prereg <rules file> \
  --data-dir data --seeds 1 2 \
  --out <result.json> --report <report.md>
```

The `m1-oos` command did not exist yet when this file was frozen. This interface is binding.

## 11. Trial count (imported from [private note])

| ID | Spec | Counted |
|---|---|---|
| T1 | Trend blend, close-to-close, constant weight, no stop, warm-up source (training) | 1 |
| T2 | Regime-DCA | 1 |
| T3 | Spot grid | 1 |
| T4 | Cash-and-carry | 1 |
| O1, O2, O3 | Risk overlays (O2 changes sizing) | 3 |
| **T5** | **B1.1.0-spot = this file §2–§3** (new stop, open fills, quantity holding, EURconv: a new spec, not T1) | 1 |
| **T6** | Donchian 20/10 sensitivity | 1 |
| | **N = 9.** The caller's "7 + 1" missed that T5 ≠ T1. Bug-fix re-runs and the 27 grid members are not counted (§8 G-c) | |

## 12. Risk table

| Risk | Likelihood (data) | Impact | Early warning | Mitigation | Automated check | Owner | Stage |
|---|---|---|---|---|---|---|---|
| OOS Sharpe < ≈ 0.8 → FAIL | 19–63 % [ref] | no capital; weeks of work | the one run | kill rule §9 | gate script | [role] | [ref] |
| The stop and open fills lower the result vs T1 | unknown; the stop fires only on 4σ days | false FAIL | T5 vs T1 on the training overlap 2019-03-08 to 2019-08-16, same run | reported, no change allowed | report row | [role] | [ref] |
| P98 > 10.2 % of C0 despite the design | low: floor 9 % of C0 + 1.2 % margin | ≈ 0.9–2.0 % of C0 over the cap | G-e | §3 floor and room stops | MC, two seeds | [role] | [ref] |
| Halt gap > 13.28 % | no data (grade C) | ≈ 4.4–13.3 % of C0 at ⅓–1 exposure | Bitvavo maintenance notices | O2: measure halt reopens | monthly probe [ref] | [role] | [ref] |
| A pre-look at OOS data | 0 so far (checked) | test void | data or results found before the run | loader lock + hash | precondition 2 | [role] | [ref] |
| Grid not counted inflates DSR | moderate | false PASS | DSR at N = 35 < 0.95 | founder sees both values | report row | [role] | [ref] |
| A freeze leads to a founder override | 47 % odds of a −6 % DD in year 1 [ref] | up to the floor | freeze count | 7-day review, floor is STOPPED | state log | [role] | [ref] |
| The EUR-book stop does not reserve coins bought on USDC | untested | unprotected position | small live test | fallback USDC stop on `bestBid` | L9 reconciliation | [role] | [ref] |

## 13. Merge items (for the manager; not applied)

1. [redacted]
2. [redacted]
3. [redacted]
4. [redacted]
5. [redacted]
6. [redacted]
7. [redacted]

## 14. Open items

- **O1:** ETH-EUR worst trade-to-trade drop. The 6.47 % proxy stays frozen here; it is used later for live sizing only.
- **O2:** Bitvavo halt or auction-reopen gap statistics.
- **O3:** Bitvavo minimum order size. [redacted]
- **O4:** builds needed before the run: the ECB fetcher and the warm-up downloader [ref], and the `m1-oos` command with the purgedcv pin.
- **O5:** a small live cross-market stop test.

## 15. Sources (compact)

- **Repo files:** [private notes].
- **Risk research:** [private notes].
- **Literature:** [private notes].
- **Languages:** English only. This is a frozen rule document; nothing was researched.
- **Contradiction:** a private note describes EURconv both as same-bar (the decision) and previous-bar (an older series). This file uses same-bar, which is point-in-time safe because both closes are known at the same close time.

## 16. Self-check

- **Every trial counted?** Yes. N = 9, and the floor is enforced from the trial log. The grid is disclosed with an N = 35 DSR.
- **OOS truly unseen?** As far as checked: no B result exists, and the loader lock plus the hash are preconditions. Literature contamination is disclosed.
- **Survives 2× costs?** The gates are computed only at 2×, with the worst measured overshoot on every stop.
- **Worst realistic loss?** Design bound ≈ 9 % of C0 plus the halt-gap stress. The gate is 10.2 % of C0.
- **Does any state remove a protective stop?** No. REDUCING, FROZEN and missing-bar days keep or re-place stops.
- **Would I trade my own money on it?** Only after G1 and 6 months of paper trading. At this stake it buys a track record, not income.

## 17. Coverage rows (for the manager)

| Topic | File | Status |
|---|---|---|
| [redacted] | [redacted] | [redacted] |
| [redacted] | [redacted] | [redacted] |
