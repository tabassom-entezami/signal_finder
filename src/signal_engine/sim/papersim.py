"""Paper account and its pure state machine. No I/O: the caller passes books,
candles and the clock; the polling loop that fetches them is a separate step.

Every exit leg is priced by a book walk (`books.sell`) or, for a candle observation, decided by
the one resolver (`resolver.check_bar`), so labels, backtest and papersim agree.
Invariant after every call: each open position is PROTECTED (armed stop) or RESIDUAL (a sell due
at most RESIDUAL_DELAY_MS ago); anything else raises InvariantError.
"""

import logging
from dataclasses import dataclass, field, replace

from signal_engine.backtest.resolver import (
    NOT_FILLED,
    SIGNAL_EXIT,
    SL,
    Bar,
    BarFillModel,
    Episode,
    ExitRule,
    check_bar,
    mark_open,
)
from signal_engine.sim import books

PROTECTED = "PROTECTED"
RESIDUAL = "RESIDUAL"
ENTRY = "ENTRY"
C0_EUR = 446.0  # $500 C_max at the 2026-10 rate (brief)
RESIDUAL_DELAY_MS = 5_000
OBSERVATION_GAP_MS = 15_000
PRE_CLOSE_MS = 120_000  # no orders in the 2 minutes before a 1D close (AFM marking-the-close case)
MAINTENANCE_BLOCK_MS = 12 * 3_600_000
MAX_SPREAD_BPS = 30.0
DEPTH_MULTIPLE = 20.0  # depth within 1 % must be >= 20 x the order
DAY_MS = 86_400_000

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Fees:
    entry: float  # EUR->USDC conversion + USDC taker
    stop_exit: float  # EUR-book taker
    signal_exit: float  # USDC taker


# Measured Bitvavo fees, not doubled (the backtest doubles them).
BITVAVO_FEES = Fees(entry=0.0011 + 0.0005, stop_exit=0.0025, signal_exit=0.0005)


class InvariantError(RuntimeError):
    """An open position without an armed stop or a due residual sell."""


@dataclass(frozen=True)
class Event:
    ts: int
    coin: int
    kind: str  # ENTRY, SL, SIGNAL_EXIT, NOT_FILLED, OPEN_MARKED or a guard refusal
    qty: float
    price: float | None
    observed: bool = True  # False: priced by the observation-gap rule, excluded from slippage stats


@dataclass
class Position:
    coin: int
    qty: float
    stop: float
    episode: Episode
    mode: str = PROTECTED
    due_ms: int | None = None
    exit_reason: str | None = None
    market: str = "EUR"  # where a residual is sold: EUR after a stop, USDC after a signal exit


@dataclass
class Account:
    cash: float = C0_EUR
    positions: dict[int, Position] = field(default_factory=dict)
    closed: list[Episode] = field(default_factory=list)


def entry_refusal(
    now_ms: int,
    notional: float,
    bids: books.Levels,
    asks: books.Levels,
    *,
    status: str = "none",
    maintenance_ms: int | None = None,
) -> str | None:
    """The first guard an entry fails, or None. Exits never pass through these guards."""
    if status != "none":
        return "GUARD_STATUS"
    if maintenance_ms is not None and 0 <= maintenance_ms - now_ms <= MAINTENANCE_BLOCK_MS:
        return "GUARD_MAINTENANCE"
    if DAY_MS - now_ms % DAY_MS <= PRE_CLOSE_MS:
        return "GUARD_PRE_CLOSE"
    spread = books.spread_bps(bids, asks)
    if spread is None or spread > MAX_SPREAD_BPS:
        return "GUARD_SPREAD"
    if books.depth_within(asks) < DEPTH_MULTIPLE * notional:
        return "GUARD_DEPTH"
    return None


def enter(
    acct: Account, coin: int, ts: int, asks: books.Levels, notional: float, stop: float, fees: Fees
) -> Event:
    """Buy by walking the asks; the stop is armed in the same step, so no unprotected state exists."""
    if coin in acct.positions:
        raise ValueError(f"coin {coin} already has an open position")
    fill = books.buy(asks, notional)
    if fill.vwap is None:
        return Event(ts, coin, NOT_FILLED, 0.0, None)
    value = fill.filled * fill.vwap
    ep = Episode(coin=coin, entry_ts=ts)
    ep.flow(-value * (1.0 + fees.entry), value * fees.entry)
    ep.risk = fill.filled * (fill.vwap - stop)
    acct.cash -= value * (1.0 + fees.entry)
    acct.positions[coin] = Position(coin, fill.filled, stop, ep)
    check_invariant(acct, ts)
    return Event(ts, coin, ENTRY, fill.filled, fill.vwap)


def rearm(acct: Account, coin: int, stop: float, ts: int) -> None:
    """Move an armed stop (the daily σ reset, or after maintenance); a residual keeps its sell."""
    pos = acct.positions[coin]
    pos.stop = stop
    check_invariant(acct, ts)


def _exit_leg(
    acct: Account, pos: Position, ts: int, qty: float, price: float, cost: float, reason: str
) -> None:
    value = qty * price
    pos.episode.flow(value * (1.0 - cost), value * cost)
    acct.cash += value * (1.0 - cost)
    pos.qty -= qty
    pos.exit_reason = pos.exit_reason or reason
    if pos.qty <= 1e-12:
        pos.episode.close(ts, pos.exit_reason)
        acct.closed.append(pos.episode)
        del acct.positions[pos.coin]


def _residual(pos: Position, ts: int, reason: str, market: str) -> None:
    pos.mode, pos.due_ms, pos.market = RESIDUAL, ts + RESIDUAL_DELAY_MS, market
    pos.exit_reason = pos.exit_reason or reason


def _sell(
    acct: Account, pos: Position, ts: int, bids: books.Levels, cost: float, reason: str, market: str
) -> Event:
    fill = books.sell(bids, pos.qty)
    if fill.vwap is not None:
        _exit_leg(acct, pos, ts, fill.filled, fill.vwap, cost, reason)
    if pos.coin in acct.positions:
        _residual(pos, ts, reason, market)
    return Event(ts, pos.coin, reason, fill.filled, fill.vwap)


def on_book(
    acct: Account, coin: int, ts: int, bids: books.Levels, fees: Fees, market: str = "EUR"
) -> list[Event]:
    """One book poll of `market`: on EUR, trigger the stop at bestBid <= stop; on either market,
    retry a due residual that belongs to it."""
    pos = acct.positions.get(coin)
    events: list[Event] = []
    if pos is not None:
        due = pos.mode == RESIDUAL and pos.market == market and pos.due_ms is not None and ts >= pos.due_ms
        triggered = (
            market == "EUR" and pos.mode == PROTECTED and bool(bids) and float(bids[0][0]) <= pos.stop
        )
        if due or triggered:
            cost = fees.signal_exit if market == "USDC" else fees.stop_exit
            events.append(_sell(acct, pos, ts, bids, cost, pos.exit_reason or SL, market))
    check_invariant(acct, ts)
    return events


def on_candle(
    acct: Account,
    coin: int,
    ts: int,
    bar: Bar,
    fills: BarFillModel,
    bids: books.Levels | None = None,
) -> list[Event]:
    """A candle observation: the resolver decides whether and why the position exits.

    Without `bids` (the daily-candle adapter) the resolver's fill model prices the whole quantity.
    With `bids` (the first book after an observation gap) the walked quantity fills at
    the worse of the walk and the candle's low, flagged unobserved; the rest becomes a residual."""
    pos = acct.positions.get(coin)
    if pos is None or pos.mode != PROTECTED:
        return []
    ex = check_bar(bar, ExitRule(stop=pos.stop), fills, ts)
    if ex is None:
        return []
    if bids is None:
        qty = pos.qty
        _exit_leg(acct, pos, ts, qty, ex.price, ex.cost_rate, ex.reason)
        check_invariant(acct, ts)
        return [Event(ts, coin, ex.reason, qty, ex.price)]
    walk = books.sell(bids, pos.qty)
    price = None if walk.vwap is None else min(walk.vwap, bar.low)
    if price is not None:
        _exit_leg(acct, pos, ts, walk.filled, price, ex.cost_rate, ex.reason)
    if coin in acct.positions:
        _residual(pos, ts, ex.reason, "EUR")
    check_invariant(acct, ts)
    return [Event(ts, coin, ex.reason, walk.filled, price, observed=False)]


def exit_refusal(now_ms: int, status: str = "none") -> str | None:
    """Signal exits pass the entry guards except the liquidity skip and the pre-maintenance entry
    block. Stops never pass through guards."""
    if status != "none":
        return "GUARD_STATUS"
    if DAY_MS - now_ms % DAY_MS <= PRE_CLOSE_MS:
        return "GUARD_PRE_CLOSE"
    return None


def exit_signal(
    acct: Account, coin: int, ts: int, bids: books.Levels, fees: Fees, status: str = "none"
) -> list[Event]:
    """Family B turned flat: sell on X-USDC by walk; a remainder becomes a USDC residual.
    A guard refusal sells nothing and is returned as an event; the stop stays armed."""
    pos = acct.positions.get(coin)
    if pos is None:
        return []
    refusal = exit_refusal(ts, status)
    if refusal is not None:
        return [Event(ts, coin, refusal, 0.0, None)]
    events = [_sell(acct, pos, ts, bids, fees.signal_exit, SIGNAL_EXIT, "USDC")]
    check_invariant(acct, ts)
    return events


def mark_window_end(acct: Account, coin: int, close: float, ts: int, cost_side: float) -> Episode | None:
    """A marked copy of the open episode for a window score; the position stays open."""
    pos = acct.positions.get(coin)
    if pos is None:
        return None
    ep = replace(pos.episode)
    mark_open(ep, pos.qty * close, cost_side, ts)
    return ep


def check_invariant(acct: Account, now_ms: int) -> None:
    for pos in acct.positions.values():
        protected = pos.mode == PROTECTED and pos.stop > 0.0
        due = pos.due_ms
        residual = pos.mode == RESIDUAL and due is not None and now_ms - due <= RESIDUAL_DELAY_MS
        if pos.qty > 0.0 and not (protected or residual):
            log.critical("papersim invariant", extra={"coin": pos.coin, "mode": pos.mode, "now": now_ms})
            raise InvariantError(f"coin {pos.coin}: {pos.mode} position without an armed stop or a due sell")
