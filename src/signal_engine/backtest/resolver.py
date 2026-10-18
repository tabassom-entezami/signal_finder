"""The one outcome resolver: exit order, exit reasons, episode PnL, R and the label.

Every caller (backtest loop, labeller, later the paper simulator and the public record) gets
its exit reason, exit price and R from here. Order inside one bar: gap through the stop, stop
touch (wins even when the same bar also touched the TP), TP, time stop. Signal exits happen at
the next open and are booked by the caller with `SIGNAL_EXIT`. Time-outs and open episodes are
scored like every other episode and are never dropped from a denominator.
"""

from collections.abc import Sequence
from dataclasses import dataclass

TP = "TP"
SL = "SL"
SL_ROOM = "SL_ROOM"
TIME_STOP = "TIME_STOP"
SIGNAL_EXIT = "SIGNAL_EXIT"
FREEZE = "FREEZE"
STOPPED = "STOPPED"
OPEN_MARKED = "OPEN_MARKED"
NOT_FILLED = "NOT_FILLED"
EXIT_REASONS = (SIGNAL_EXIT, SL, SL_ROOM, FREEZE, STOPPED, OPEN_MARKED, TP, TIME_STOP, NOT_FILLED)


@dataclass(frozen=True)
class Bar:
    open: float
    high: float
    low: float
    close: float

    def scaled(self, factor: float) -> "Bar":
        return Bar(self.open * factor, self.high * factor, self.low * factor, self.close * factor)


@dataclass(frozen=True)
class ExitRule:
    stop: float
    tp: float | None = None
    horizon_end: int | None = None  # ms UTC; None = no time stop


@dataclass(frozen=True)
class BarFillModel:
    gap_g: float  # overshoot below the stop price on every stop fill
    cost_stop: float
    cost_side: float

    def stop_fill(self, stop: float) -> float:
        return stop * (1.0 - self.gap_g)

    def gap_fill(self, open_px: float) -> float:
        return open_px * (1.0 - self.gap_g)


@dataclass(frozen=True)
class Exit:
    reason: str
    price: float
    cost_rate: float


def check_bar(bar: Bar, rule: ExitRule, fills: BarFillModel, ts: int) -> Exit | None:
    """The exit, if any, for a position held through `bar`. SL is checked before TP."""
    if bar.open <= rule.stop:
        return Exit(SL, fills.gap_fill(bar.open), fills.cost_stop)
    if bar.low <= rule.stop:
        return Exit(SL, fills.stop_fill(rule.stop), fills.cost_stop)
    if rule.tp is not None and bar.high >= rule.tp:
        return Exit(TP, rule.tp, fills.cost_side)
    if rule.horizon_end is not None and ts >= rule.horizon_end:
        return Exit(TIME_STOP, bar.close, fills.cost_side)
    return None


@dataclass
class Episode:
    """One position from the fill that takes q from 0 to > 0 until the fill that takes it back to 0."""

    coin: int
    entry_ts: int
    pnl: float = 0.0  # net EUR cash flow of the episode, every cost included
    costs: float = 0.0
    risk: float | None = None  # EUR at risk at the first stop placement, for R
    exit_ts: int | None = None
    reason: str | None = None

    def flow(self, cash: float, cost: float) -> None:
        self.pnl += cash
        self.costs += cost

    def close(self, ts: int, reason: str) -> None:
        self.exit_ts = ts
        self.reason = reason

    @property
    def r(self) -> float | None:
        return None if not self.risk else self.pnl / self.risk


def label(ep: Episode) -> bool:
    """The success label, identical everywhere: episode net PnL > 0 at the rule's own exit."""
    return ep.pnl > 0.0


def mark_open(ep: Episode, value: float, cost_side: float, ts: int) -> None:
    """An episode still open at a window end is marked at that close minus COST_SIDE and counted."""
    ep.flow(value * (1.0 - cost_side), value * cost_side)
    ep.close(ts, OPEN_MARKED)


def resolve(
    entry_ts: int,
    entry_px: float,
    qty: float,
    cost_entry: float,
    path: Sequence[tuple[int, Bar, ExitRule]],
    fills: BarFillModel,
) -> Episode:
    """Batch form for labels: buy `qty` at `entry_px`, then fold `check_bar` over the path."""
    if not path:
        raise ValueError("empty price path")
    if path[0][0] < entry_ts:
        raise ValueError("price path starts before the entry was committed")
    ep = Episode(coin=0, entry_ts=entry_ts)
    ep.flow(-qty * entry_px * (1.0 + cost_entry), qty * entry_px * cost_entry)
    ep.risk = qty * (entry_px - path[0][2].stop)
    for ts, bar, rule in path:
        ex = check_bar(bar, rule, fills, ts)
        if ex is not None:
            value = qty * ex.price
            ep.flow(value * (1.0 - ex.cost_rate), value * ex.cost_rate)
            ep.close(ts, ex.reason)
            return ep
    last_ts, last_bar, _ = path[-1]
    mark_open(ep, qty * last_bar.close, fills.cost_side, last_ts)
    return ep


def exit_table(episodes: Sequence[Episode]) -> dict[str, float | int | None]:
    """One column per exit reason, every episode in the denominator, plus break-even win rate."""
    n = len(episodes)
    out: dict[str, float | int | None] = {reason: 0 for reason in EXIT_REASONS}
    for ep in episodes:
        out[str(ep.reason)] = int(out.get(str(ep.reason)) or 0) + 1
    wins = [ep.pnl for ep in episodes if label(ep)]
    losses = [-ep.pnl for ep in episodes if not label(ep)]
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    out["episodes"] = n
    out["wins"] = len(wins)
    out["win_rate"] = len(wins) / n if n else None
    out["breakeven_win_rate"] = avg_loss / (avg_win + avg_loss) if (avg_win + avg_loss) > 0 else None
    out["expectancy"] = sum(ep.pnl for ep in episodes) / n if n else None
    return out
