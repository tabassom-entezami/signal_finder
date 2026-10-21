"""Synthetic daily bars for development and tests (random walks, never real prices).

Dated before the out-of-sample start, so the loader lock holds for every development run.
"""

import math
from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from signal_engine.backtest.engine import Bars
from signal_engine.data.pit import OOS_START

DAY_MS = 86_400_000
START_MS = int(datetime(2001, 1, 1, tzinfo=UTC).timestamp() * 1000)
WARMUP = 300  # bars before the synthetic OOS start (252 lookback + 30-day vol)

# Market-panel shape, measured on Binance Vision BTCUSDT / ETHUSDT daily bars 2017-08-17..2019-03-07
# (567 returns each, all before the held-out cut): sd 0.046 / 0.058, BTC-ETH log-return correlation 0.714,
# simple-return excess kurtosis 2.88 / 1.88. The GARCH(1,1)-t maximum likelihood fit sat on the stationarity
# bound (alpha + beta = 0.999, BTC omega ~ 0, t df 3.6 / 2.9), so persistence is pinned at 0.96 and the t df
# at 8.
# Each coin's shock mixes two independent t8 draws (sqrt(rho) common + sqrt(1 - rho) own), so the shock
# kurtosis is 4.5 (rho^2 + (1 - rho)^2) + 6 rho (1 - rho) ~ 3.88 and the GARCH unconditional kurtosis ~5.07
# (excess ~2.1), inside the measured 1.9-2.9.
MARKET_VOL = 0.05
MARKET_RHO = 0.71
GARCH_ALPHA, GARCH_BETA, T_DF = 0.08, 0.88, 8.0
# Same sample: open-to-previous-close gaps > 1 % on 0 / 4 of 567 days (max 1.52 %); a low more than 10 %
# below the bar body on 15 / 19 of 567 days (2.6 / 3.4 %), close-to-close moves to -22 %.
GAP_MAX = 0.015
WICK_DEPTH = (0.10, 0.22)
MIN_RETURN = -0.95  # simple-return floor: a price never reaches zero
MU_LOOKBACK = 21  # the shortest TSMOM lookback (lab tsmom-L21): the mu control's signal


def synthetic_market(seed: int, n_coins: int = 12, n_days: int = 1105, *, drift: float = 0.0,
                     phi: float = 0.0, mu: float = 0.0, planted: int | None = None, garch: bool = True,
                     jump_p: float = 0.03, gap_p: float = 0.004, vol: float = MARKET_VOL,
                     rho: float = MARKET_RHO) -> dict[str, pd.DataFrame]:
    """A coin panel in the research-lab shape: open/high/low/close/volume frames, daily naive-UTC index.

    Simple returns R_t = drift + GARCH(1,1) Student-t shocks with one common factor (pairwise correlation
    `rho`), clipped at >= MIN_RETURN; close_t = close_{t-1} (1 + R_t). The null is drift 0: the price is a
    martingale (zero-mean simple return, so a long-only cell earns no beta). The `planted` coin gets AR(1)
    returns with coefficient `phi` and/or a signal-conditional drift mu s_t, s_t = +1 when its close at t-1 is
    above the close MU_LOOKBACK days before that, else -1 (the shortest TSMOM cell's signal: oracle control).
    Opens gap from the previous close with probability `gap_p` (zero-mean, |gap| <= GAP_MAX); with probability
    `jump_p` a bar's low wicks WICK_DEPTH below its body (stop risk without changing close-to-close returns).
    """
    if n_coins < 1 or n_days < 2:
        raise ValueError("need >= 1 coin and >= 2 days")
    index = pd.date_range(datetime(2001, 1, 1), periods=n_days, freq="D")
    if index[-1] >= OOS_START.replace(tzinfo=None):
        raise ValueError("synthetic bars must end before the out-of-sample start")
    rng = np.random.default_rng(seed)
    t_scale = math.sqrt(T_DF / (T_DF - 2.0))  # unit-variance Student-t shocks
    common = rng.standard_t(T_DF, size=(n_days, 1)) / t_scale
    own = rng.standard_t(T_DF, size=(n_days, n_coins)) / t_scale
    z = math.sqrt(rho) * common + math.sqrt(1.0 - rho) * own
    if garch:
        eps = np.empty_like(z)
        omega = vol**2 * (1.0 - GARCH_ALPHA - GARCH_BETA)
        s2 = np.full(n_coins, vol**2)
        for t in range(n_days):
            eps[t] = np.sqrt(s2) * z[t]
            s2 = omega + GARCH_ALPHA * eps[t] ** 2 + GARCH_BETA * s2
    else:
        eps = vol * z
    r = np.maximum(eps + drift, MIN_RETURN)
    if planted is not None and (phi != 0.0 or mu != 0.0):
        x = r[:, planted].copy()
        c = np.empty(n_days)
        level = 100.0
        for t in range(n_days):
            if t > 0:
                x[t] += phi * x[t - 1]
            if mu != 0.0 and t > MU_LOOKBACK:  # the signal reads closes up to t-1 only
                x[t] += mu if c[t - 1] > c[t - 1 - MU_LOOKBACK] else -mu
            x[t] = max(x[t], MIN_RETURN)
            level *= 1.0 + x[t]
            c[t] = level
        r[:, planted] = x
    close = 100.0 * np.cumprod(1.0 + r, axis=0)
    prev = np.vstack([np.full((1, n_coins), 100.0), close[:-1]])
    shape = (n_days, n_coins)
    gap = np.where(rng.random(shape) < gap_p, rng.uniform(-GAP_MAX, GAP_MAX, shape), 0.0)
    open_ = prev * (1.0 + gap)
    high = np.maximum(open_, close) * (1.0 + np.abs(rng.normal(0.0, 0.3 * vol, (n_days, n_coins))))
    low = np.minimum(open_, close) * (1.0 - np.abs(rng.normal(0.0, 0.3 * vol, (n_days, n_coins))))
    wick = rng.random((n_days, n_coins)) < jump_p
    depth = rng.uniform(WICK_DEPTH[0], WICK_DEPTH[1], (n_days, n_coins))
    low = np.where(wick, np.minimum(low, np.minimum(open_, close) * (1.0 - depth)), low)
    volume = np.exp(rng.normal(13.0, 1.0, (1, n_coins)) + rng.normal(0.0, 0.5, (n_days, n_coins)))
    cols = [f"SYN{i:02d}USDT" for i in range(n_coins)]
    return {k: pd.DataFrame(v, index=index, columns=cols) for k, v in
            (("open", open_), ("high", high), ("low", low), ("close", close), ("volume", volume))}


def synthetic_bars(seed: int, n_days: int = 900, drift: float = 0.0006, vol: float = 0.03) -> Bars:
    """Two coins: GBM closes, open = previous close, high/low around the bar, u = close * 1.1."""
    rng = np.random.default_rng(seed)
    r = rng.normal(drift, vol, size=(n_days, 2))
    close = 100.0 * np.exp(np.cumsum(r, axis=0))
    open_ = np.vstack([close[:1] / np.exp(r[:1]), close[:-1]])
    spread = np.abs(rng.normal(0.0, vol, size=(n_days, 2)))
    high = np.maximum(open_, close) * (1.0 + spread)
    low = np.minimum(open_, close) * (1.0 - spread)
    ts = (START_MS + DAY_MS * np.arange(n_days)).astype(np.int64)
    return Bars(ts, open_, high, low, close, close * 1.1, WARMUP)


@dataclass(frozen=True)
class SyntheticSource:
    """A `BarSource` over synthetic bars; `real` is False, so the loader lock stays closed."""

    seed: int
    n_days: int = 900
    real: bool = False

    def load(self) -> Bars:
        return synthetic_bars(self.seed, self.n_days)
