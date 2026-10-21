"""12-month loss Monte Carlo on the owner's stake (`STAKE_USD` in `.env`), for the loss and return gates.

Moving-block bootstrap of the OOS daily records (both coins on the same day index): tau,
sigma_d and open/high/low/close relative to the previous close. Each 365-day path starts flat
at C0 and runs the same `run_path` as path A with the floor on, the overshoot on every stop and
one unprotected halt gap on a uniformly random day.

Model assumption: tau and sigma_d are resampled with the returns of their own day, not recomputed on the
synthetic path. The block (20 days) is shorter than every lookback (21/63/252 days, 31-point sigma), so
at each block join the position size can jump to a value the synthetic history would not give. Stops are
checked on the synthetic path, but their distance inherits sigma_d. The bias of the P98 loss can go either
way, so the P98 is a model estimate, not a loss ceiling; no block-length sensitivity was run for M1.
For later rules, `simulate(..., inputs=strategy_inputs)` recomputes tau and sigma_d on each synthetic path
after a WARMUP-day prefix, and `sweep` reports the P98 over block lengths; the gate value is the worst one.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from signal_engine.backtest.engine import Bars, Params, run_path
from signal_engine.settings import required

Arr = NDArray[np.float64]
DAY_MS = 86_400_000
MONDAY_MS = 4 * DAY_MS  # 1970-01-05, a Monday: synthetic path calendars start on a week boundary
BLOCK = 20
BLOCKS = (5, 10, 20, 40)  # block-length sensitivity grid
WARMUP = 252 + 30  # longest vote lookback plus the 31-point sigma window
DAYS = 365
PATHS = 10_000
Inputs = Callable[[Bars], tuple[Arr, Arr]]  # bars -> (tau, sigma_d), e.g. engine.strategy_inputs
MC_PARAMS = Params(account=True, apply_floor=True)
# Operational events for the reported total-loss P98 come from `OP_EVENTS` in `.env`: (annual probability,
# loss range as a share of the stake). Market, stop gaps and the halt gap are already in each path.
TOTAL_LOSS_CAP = 1.05  # total loss cap, as a share of the stake


@dataclass(frozen=True)
class Records:
    rel: Arr  # (k, 4, coins): open, high, low, close / previous close
    tau: Arr  # (k, coins), decision at the close of that record
    sigma: Arr


@dataclass(frozen=True)
class MCResult:
    p98_loss: float  # $ on the stake
    median_pnl_pct: float  # of C0
    p98_max_dd: float
    p_freeze: float
    p_stopped: float
    paths: int
    p98_total_loss: float = 0.0  # $, market + operational events (reported, not gated)


def records(bars: Bars, tau: Arr, sigma: Arr, start: int) -> Records:
    """Daily records from bar `start` + 1 on (each needs the previous close)."""
    prev = bars.close[start:-1]
    rel = np.stack([x[start + 1 :] / prev for x in (bars.open, bars.high, bars.low, bars.close)], axis=1)
    keep = np.isfinite(rel).all(axis=(1, 2)) & np.isfinite(tau[start + 1 :]).all(axis=1)
    return Records(rel[keep], tau[start + 1 :][keep], sigma[start + 1 :][keep])


def _indices(rng: np.random.Generator, n: int, length: int, block: int) -> NDArray[np.int64]:
    starts = rng.integers(0, n - block + 1, size=-(-length // block))
    return (starts[:, None] + np.arange(block)[None, :]).ravel()[:length].astype(np.int64)


def simulate(
    rec: Records,
    seed: int,
    n_paths: int = PATHS,
    days: int = DAYS,
    block: int = BLOCK,
    params: Params = MC_PARAMS,
    stake: float | None = None,
    inputs: Inputs | None = None,
) -> MCResult:
    """inputs=None resamples tau and sigma_d with their own day (the M1 gate); a function recomputes them
    on each synthetic path, which first runs WARMUP days so every lookback is filled."""
    if len(rec.tau) < block:
        raise ValueError("fewer records than one block")
    stake = float(required("stake_usd")) if stake is None else stake
    rng = np.random.default_rng(seed)
    coins = rec.tau.shape[1]
    pre = 0 if inputs is None else WARMUP
    # The first decision day (index pre) stays a Monday.
    ts = (MONDAY_MS + DAY_MS * (np.arange(pre + days + 1) + (-pre % 7))).astype(np.int64)
    end, max_dd, froze, stopped = np.empty(n_paths), np.empty(n_paths), np.empty(n_paths), np.empty(n_paths)
    for p in range(n_paths):
        idx = _indices(rng, len(rec.tau), pre + days + 1, block)
        rel = rec.rel[idx]
        close = np.cumprod(np.vstack([np.ones((1, coins)), rel[1:, 3]]), axis=0)
        prev = np.vstack([np.ones((1, coins)), close[:-1]])
        o, h, lo = rel[:, 0] * prev, rel[:, 1] * prev, rel[:, 2] * prev
        bars = Bars(ts, o, h, lo, close, close, 0)
        tau, sigma = (rec.tau[idx], rec.sigma[idx]) if inputs is None else inputs(bars)
        halt = int(rng.integers(1, days + 1))
        res = run_path(bars, tau, sigma, params, start=pre, halt_day=halt)
        end[p], max_dd[p] = res.equity[-1], res.max_drawdown()
        froze[p], stopped[p] = res.freezes > 0, res.stopped
    loss = -(end - 1.0) * stake
    ops = operational_loss(np.random.default_rng([seed, 1]), n_paths) * stake
    total = np.minimum(loss + ops, TOTAL_LOSS_CAP * stake)
    return MCResult(
        p98_loss=float(np.percentile(loss, 98)),
        median_pnl_pct=float(np.median(end - 1.0)),
        p98_max_dd=float(np.percentile(max_dd, 98)),
        p_freeze=float(froze.mean()),
        p_stopped=float(stopped.mean()),
        paths=n_paths,
        p98_total_loss=float(np.percentile(total, 98)),
    )


def sweep(
    rec: Records,
    seeds: Iterable[int],
    blocks: Iterable[int] = BLOCKS,
    n_paths: int = PATHS,
    stake: float | None = None,
    inputs: Inputs | None = None,
) -> dict[tuple[int, int], MCResult]:
    """One run per (block, seed); report the P98 as the range over the grid and gate on its maximum."""
    seeds = list(seeds)
    return {
        (b, s): simulate(rec, s, n_paths, block=b, stake=stake, inputs=inputs) for b in blocks for s in seeds
    }


def operational_loss(rng: np.random.Generator, n_paths: int) -> Arr:
    """Per path, the summed share of the stake lost to operational events in one year (own stream,
    so the gated market loss is identical with or without this line)."""
    out = np.zeros(n_paths)
    for p_year, lo, hi in required("op_events"):
        hit = rng.random(n_paths) < p_year
        out += hit * rng.uniform(lo, hi, n_paths)
    return out
