"""Daily loop for the M1 momentum rule with its frozen account layer.

One function, `run_path`, serves path A (account layer, no floor), the strategy layer S
(`account=False`) and every Monte Carlo path (floor on, one halt gap). Decision at close t
(tau[t], sigma[t]) -> trade at open t+1 -> stop placed with the fill at open*(1-d) and checked
on bar t+1 (SL first, via the resolver) -> mark at close t+1. Equity is EUR; idle cash earns 0.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime

import numpy as np
from numpy.typing import NDArray

from signal_engine.backtest.resolver import (
    FREEZE,
    SIGNAL_EXIT,
    SL,
    SL_ROOM,
    STOPPED,
    Bar,
    BarFillModel,
    Episode,
    ExitRule,
    check_bar,
    mark_open,
)
from signal_engine.strategy import b_spot

Arr = NDArray[np.float64]

ACTIVE = "ACTIVE"
REDUCING = "REDUCING"
FROZEN = "FROZEN"
HALTED = "STOPPED"  # the floor state; no restart
ENTRY = "ENTRY"
REBALANCE = "REBALANCE"


@dataclass(frozen=True)
class Params:
    k_stop: float = b_spot.K_STOP
    band: float = b_spot.BAND
    cost_side: float = b_spot.COST_SIDE
    cost_stop: float = b_spot.COST_STOP
    gap_g: tuple[float, ...] = (b_spot.G["BTC"], b_spot.G["ETH"])
    account: bool = True  # False = strategy layer S: no freeze, room stop or weekly limit
    apply_floor: bool = False  # path A runs without the floor; the Monte Carlo and live use it
    halt_gap: float = b_spot.HALT_GAP


@dataclass(frozen=True)
class Bars:
    """Aligned daily bars: EUR OHLC for the fills and the USD-terms close u for the signal."""

    ts: NDArray[np.int64]  # bar start, ms UTC
    open: Arr  # shape (n, coins); NaN = missing bar
    high: Arr
    low: Arr
    close: Arr
    u: Arr
    oos_start: int  # index of the first out-of-sample bar


@dataclass
class RunResult:
    equity: Arr
    episodes: list[Episode]
    fills: list[tuple[int, int, str, float, float, float]]  # (bar, coin, reason, dq, price, cost)
    freezes: int = 0
    stopped: bool = False
    modes: list[str] = field(default_factory=list)
    exposure: list[float] = field(default_factory=list)  # held value / equity at each close

    @property
    def returns(self) -> Arr:
        out: Arr = self.equity[1:] / self.equity[:-1] - 1.0
        return out

    def max_drawdown(self) -> float:
        peak = np.maximum.accumulate(self.equity)
        return float(np.max(1.0 - self.equity / peak))


class _Book:
    def __init__(self, c0: float, coins: int) -> None:
        self.cash = c0
        self.q = [0.0] * coins
        self.w = [0.0] * coins
        self.open_ep: list[Episode | None] = [None] * coins
        self.done: list[Episode] = []
        self.fills: list[tuple[int, int, str, float, float, float]] = []

    def trade(self, j: int, ts: int, i: int, px: float, new_q: float, cost_rate: float, reason: str) -> None:
        dq = new_q - self.q[i]
        value = dq * px
        cost = abs(value) * cost_rate
        self.cash -= value + cost
        ep = self.open_ep[i]
        if ep is None and new_q > 0.0:
            ep = Episode(coin=i, entry_ts=ts)
            self.open_ep[i] = ep
        if ep is not None:
            ep.flow(-value - cost, cost)
        self.q[i] = new_q
        self.fills.append((j, i, reason, dq, px, cost))
        if new_q == 0.0:
            self.w[i] = 0.0
            if ep is not None:
                ep.close(ts, reason)
                self.done.append(ep)
                self.open_ep[i] = None


def _open(bar: Bar | None) -> float:
    return bar.open if bar is not None else 0.0


def _week(ts_ms: int) -> tuple[int, int]:
    iso = datetime.fromtimestamp(ts_ms / 1000, UTC).isocalendar()
    return iso[0], iso[1]


def run_path(
    bars: Bars,
    tau: Arr,
    sigma: Arr,
    params: Params,
    start: int = 0,
    c0: float = 1.0,
    halt_day: int | None = None,
) -> RunResult:
    """Run the rule from bar `start` (decision-only, flat at c0) to the last bar."""
    ts = bars.ts[start:]
    o, h, lo, c = bars.open[start:], bars.high[start:], bars.low[start:], bars.close[start:]
    tau, sigma = tau[start:], sigma[start:]
    n, coins = c.shape
    book = _Book(c0, coins)
    fills = [BarFillModel(params.gap_g[i], params.cost_stop, params.cost_side) for i in range(coins)]
    last_close = [float(c[0, i]) for i in range(coins)]
    sig = [np.nan] * coins
    d_cur = [np.nan] * coins
    room_bound = [False] * coins
    equity = np.empty(n)
    equity[0] = c0
    mode, frozen_until, peak, week_start, week_key = ACTIVE, -1, c0, c0, _week(int(ts[0]))
    freezes, modes = 0, [mode]
    exposure: list[float] = [0.0]

    def limit() -> tuple[float, bool]:
        level = (1.0 - b_spot.FREEZE_DD) * peak
        if params.apply_floor and b_spot.FLOOR * c0 >= level:
            return b_spot.FLOOR * c0, True
        return level, False

    def px_now(i: int, bar: Bar | None) -> float:
        return bar.open if bar is not None else last_close[i]

    def freeze(j: int, floor_binding: bool) -> None:
        nonlocal mode, frozen_until, freezes
        freezes += 1
        mode = HALTED if floor_binding else FROZEN
        frozen_until = j + b_spot.REVIEW_DAYS

    for j in range(1, n):
        t, tsj = j - 1, int(ts[j])
        e_t = float(equity[t])
        if _week(tsj) != week_key:  # first decision of a new ISO week (Monday 00:00 UTC)
            week_key, week_start = _week(tsj), e_t
            if mode == REDUCING:
                mode = ACTIVE
        if mode == FROZEN and t >= frozen_until:
            mode, peak = ACTIVE, e_t
        # ---- decision at close t ----
        targets = list(book.w)
        for i in range(coins):
            if np.isfinite(sigma[t, i]):
                sig[i] = float(sigma[t, i])
            if mode in (FROZEN, HALTED):
                targets[i] = 0.0
            elif np.isfinite(tau[t, i]):  # NaN = missing bar: no decision, keep q and the stop rule
                nw = b_spot.next_weight(float(tau[t, i]), book.w[i], params.band)
                targets[i] = min(nw, book.w[i]) if mode == REDUCING else nw
        day: list[Bar | None] = [
            Bar(float(o[j, i]), float(h[j, i]), float(lo[j, i]), float(c[j, i]))
            if np.isfinite(o[j, i])
            else None
            for i in range(coins)
        ]
        trigger, floor_hit = False, False
        gapped_out: set[int] = set()  # stopped by the halt gap: re-entry only from the next decision
        # ---- unprotected halt gap at the open (Monte Carlo stress) ----
        if halt_day == j:
            for i in range(coins):
                bar = day[i]
                if bar is None or book.q[i] <= 0.0:
                    continue
                gapped = bar.scaled(1.0 - params.halt_gap)
                day[i] = gapped
                rule = ExitRule(stop=b_spot.stop_price(bar.open, d_cur[i]))
                ex = check_bar(gapped, rule, BarFillModel(0.0, params.cost_stop, params.cost_side), tsj)
                if ex is not None:  # gap >= d: fill at the gapped price, COST_STOP, no extra G
                    book.trade(j, tsj, i, ex.price, 0.0, ex.cost_rate, SL_ROOM if room_bound[i] else SL)
                    gapped_out.add(i)
                    if room_bound[i] and params.account:
                        trigger = True
        # ---- execution at the open of bar j ----
        # Sells first, so a buy can use the cash they free (frozen rules: no leverage, total exposure
        # at most 100 % at trade time): a buy is capped at the cash at hand, costs included.
        order = sorted(range(coins), key=lambda k: targets[k] * b_spot.HALF * e_t - book.q[k] * _open(day[k]))
        for i in order:
            bar = day[i]
            if bar is None or targets[i] == book.w[i] or trigger or i in gapped_out:
                continue
            new_q = targets[i] * b_spot.HALF * e_t / bar.open
            spend = (new_q - book.q[i]) * bar.open * (1.0 + params.cost_side)
            if spend > book.cash:
                new_q = book.q[i] + max(book.cash, 0.0) / (bar.open * (1.0 + params.cost_side))
            reason = SIGNAL_EXIT if new_q == 0.0 else (ENTRY if book.q[i] == 0.0 else REBALANCE)
            # Live splits orders > 25 % of equity into child orders; a linear cost gives the same fill.
            book.trade(j, tsj, i, bar.open, new_q, params.cost_side, reason)
            book.w[i] = targets[i]
        # ---- stops, placed with the fill and checked on this bar (SL first) ----
        held = [i for i in range(coins) if book.q[i] > 0.0 and day[i] is not None]
        room = np.inf
        if params.account and held and not trigger:
            level, floor_hit = limit()
            e_plus = book.cash + sum(book.q[i] * px_now(i, day[i]) for i in range(coins))
            value = sum(book.q[i] * px_now(i, day[i]) for i in held)
            over = sum(book.q[i] * px_now(i, day[i]) * (params.gap_g[i] + params.cost_stop) for i in held)
            room = (e_plus - level - over) / value
            if room <= 0.0:  # no room for any stop: flatten at this open
                for i in held:
                    book.trade(
                        j, tsj, i, px_now(i, day[i]), 0.0, params.cost_side, STOPPED if floor_hit else FREEZE
                    )
                freeze(j, floor_hit)
                held = []
        for i in held:
            bar = day[i]
            assert bar is not None
            d = params.k_stop * sig[i]
            room_bound[i] = room < d
            d_cur[i] = min(d, room)
            ep = book.open_ep[i]
            if ep is not None and ep.risk is None:
                ep.risk = book.q[i] * bar.open * d_cur[i]
            ex = check_bar(bar, ExitRule(stop=b_spot.stop_price(bar.open, d_cur[i])), fills[i], tsj)
            if ex is not None:
                book.trade(j, tsj, i, ex.price, 0.0, ex.cost_rate, SL_ROOM if room_bound[i] else SL)
                trigger = trigger or room_bound[i]
                e_fill = book.cash + sum(book.q[k] * px_now(k, day[k]) for k in range(coins))
                if params.account and mode == ACTIVE and e_fill / week_start - 1.0 <= -b_spot.WEEKLY_LIMIT:
                    mode = REDUCING
        if trigger and mode not in (FROZEN, HALTED):  # a room-bound stop filled: sell the rest at the close
            level, floor_hit = limit()
            for i in range(coins):
                if book.q[i] > 0.0:
                    bar = day[i]
                    px = bar.close if bar is not None else last_close[i]
                    book.trade(j, tsj, i, px, 0.0, params.cost_side, STOPPED if floor_hit else FREEZE)
            freeze(j, floor_hit)
        # ---- mark at close j ----
        for i in range(coins):
            bar = day[i]
            if bar is not None and np.isfinite(bar.close):  # the gapped bar on a halt day
                last_close[i] = bar.close
        e_j = book.cash + sum(book.q[i] * last_close[i] for i in range(coins) if book.q[i] > 0.0)
        if params.account and mode in (ACTIVE, REDUCING):
            peak = max(peak, e_j)
            level, floor_hit = limit()
            if e_j <= level:  # freeze (or floor) at a close: sell the rest at this close
                for i in range(coins):
                    if book.q[i] > 0.0:
                        book.trade(
                            j, tsj, i, last_close[i], 0.0, params.cost_side, STOPPED if floor_hit else FREEZE
                        )
                e_j = book.cash
                freeze(j, floor_hit)
            elif mode == ACTIVE and e_j / week_start - 1.0 <= -b_spot.WEEKLY_LIMIT:
                mode = REDUCING  # reduce-only until the first decision of the next ISO week
        # The -2 %/day limit expires at UTC day end, which is the only decision time, so it never binds.
        equity[j] = e_j
        exposure.append(sum(book.q[i] * last_close[i] for i in range(coins) if book.q[i] > 0.0) / e_j)
        modes.append(mode)
    episodes = list(book.done)
    for i, ep in enumerate(book.open_ep):
        if ep is not None:
            mark_open(ep, book.q[i] * last_close[i], params.cost_side, int(ts[-1]))
            episodes.append(ep)
    return RunResult(equity, episodes, book.fills, freezes, mode == HALTED, modes, exposure)


def strategy_inputs(
    bars: Bars, variant: str = b_spot.TSMOM, vol_target: float = b_spot.VOL_TARGET
) -> tuple[Arr, Arr]:
    """tau and sigma_d for every coin column of `bars.u`."""
    pairs = [b_spot.signals(bars.u[:, i], variant, vol_target) for i in range(bars.u.shape[1])]
    return np.column_stack([p[0] for p in pairs]), np.column_stack([p[1] for p in pairs])
