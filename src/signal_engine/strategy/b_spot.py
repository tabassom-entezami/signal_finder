"""The M1 momentum rule (B1.1.0-spot), exactly as frozen before the out-of-sample test.

Pure functions on NumPy arrays. Index t is a closed daily bar; every value at t uses only
`u[0..t]` (truncation invariant). NaN means "no decision at t": a missing input bar or not
enough history. The Donchian 20/10 rule is the one counted sensitivity trial (T6).
"""

import numpy as np
from numpy.typing import NDArray

Arr = NDArray[np.float64]

STRATEGY_VERSION = "B1.1.0-spot"
DONCHIAN_VERSION = "B1.1.0-spot-donchian-20-10"

# ---- constants (frozen; names and values as in the frozen rules file) ----
COINS = ("BTC", "ETH")
HALF = 0.5
LOOKBACKS = (21, 63, 252)  # bars
VOL_WIN = 30  # daily log returns, ddof = 1
ANN = 365
VOL_TARGET = 0.20
BAND = 0.10
K_STOP = 4.0
COST_SIDE = 0.0040  # 2x, per side, USDC path: entry, rebalance, signal exit, flatten
COST_STOP = 0.0052  # 2x, per side, EUR-book stop exit
G = {"BTC": 0.0351, "ETH": 0.0647}  # stop overshoot below the stop price
FREEZE_DD = 0.06
FLOOR = 0.91
REVIEW_DAYS = 7
WEEKLY_LIMIT = 0.04
HALT_GAP = 0.1328  # unprotected gap, Monte Carlo stress only
DONCHIAN_ENTRY = 20
DONCHIAN_EXIT = 10

TSMOM = "tsmom"
DONCHIAN = "donchian"


def sigma_daily(u: Arr) -> Arr:
    """sigma_d[t] = std(diff(log u[t-30 .. t]), ddof=1): 31 points, 30 returns; NaN if any is missing."""
    n = len(u)
    out = np.full(n, np.nan)
    if n <= VOL_WIN:
        return out
    with np.errstate(invalid="ignore", divide="ignore"):
        r = np.diff(np.log(u))
    windows = np.lib.stride_tricks.sliding_window_view(r, VOL_WIN)  # row k = r[k .. k+29] -> t = k + 30
    ok = np.isfinite(windows).all(axis=1)
    std = np.full(len(windows), np.nan)
    if ok.any():
        std[ok] = np.std(windows[ok], axis=1, ddof=1)
    out[VOL_WIN:] = std
    return out


def _size(state: Arr, sigma_d: Arr, vol_target: float) -> Arr:
    """tau = max(state, 0) * min(1, vol_target / sigma_a); NaN propagates (no decision)."""
    sigma_a = sigma_d * np.sqrt(ANN)
    with np.errstate(divide="ignore", invalid="ignore"):
        scale = np.minimum(1.0, vol_target / sigma_a)
    out: Arr = np.maximum(state, 0.0) * scale
    return out


def tsmom_vote(u: Arr) -> Arr:
    """v[t] = (sign(u_t - u_{t-21}) + sign(u_t - u_{t-63}) + sign(u_t - u_{t-252})) / 3, sign(0) = 0."""
    n = len(u)
    v = np.full(n, np.nan)
    longest = max(LOOKBACKS)
    if n > longest:
        cur = u[longest:]
        total = np.zeros(n - longest)
        for lb in LOOKBACKS:
            total = total + np.sign(cur - u[longest - lb : n - lb])
        v[longest:] = total / len(LOOKBACKS)
    return v


def donchian_state(u: Arr) -> Arr:
    """s = 1 when u_t > max(u_{t-20..t-1}); 0 when u_t < min(u_{t-10..t-1}); else unchanged (starts 0)."""
    n = len(u)
    s = np.full(n, np.nan)
    state = 0.0
    for t in range(DONCHIAN_ENTRY, n):
        window = u[t - DONCHIAN_ENTRY : t + 1]
        if not np.isfinite(window).all():
            continue  # missing bar: no decision, the state is kept
        if u[t] > window[:-1].max():
            state = 1.0
        elif u[t] < u[t - DONCHIAN_EXIT : t].min():
            state = 0.0
        s[t] = state
    return s


def signals(u: Arr, variant: str = TSMOM, vol_target: float = VOL_TARGET) -> tuple[Arr, Arr]:
    """(tau, sigma_d) for one coin's USD-terms close series u; 0 <= tau <= 1 where defined."""
    sigma_d = sigma_daily(u)
    if variant == TSMOM:
        state = tsmom_vote(u)
    elif variant == DONCHIAN:
        state = donchian_state(u)
    else:
        raise ValueError(f"unknown variant {variant!r}")
    return _size(state, sigma_d, vol_target), sigma_d


def next_weight(tau: float, w_held: float, band: float = BAND) -> float:
    """Rebalance band: move to tau only if it moved by >= band, or to exit when tau is 0."""
    if abs(tau - w_held) >= band or (tau == 0.0 and w_held > 0.0):
        return tau
    return w_held


def stop_price(open_px: float, distance: float) -> float:
    """Resting catastrophe stop for one day: S = open * (1 - d), d = min(K * sigma_d, d_room)."""
    return open_px * (1.0 - distance)
