"""Book-walk fills, ported from an earlier depth-cost script's `walk()`.

A market order walks the levels from the best price and stops at Bitvavo's 4 % slippage
protection; what is left is a residual, never a fill at a price the book did not show.
"""

from collections.abc import Sequence
from dataclasses import dataclass

PROTECT_PCT = 0.04  # Bitvavo market-order slippage protection

Levels = Sequence[Sequence[float]]  # [[price, base quantity], ...], best first


@dataclass(frozen=True)
class Fill:
    vwap: float | None  # None when nothing filled
    filled: float  # base quantity
    residual: float  # base quantity left (sell) or its quote value left (buy, in quote)


def sell(bids: Levels, qty: float, protect_pct: float = PROTECT_PCT) -> Fill:
    """Sell `qty` base into `bids`, no lower than best bid × (1 − protect_pct)."""
    if not bids:
        return Fill(None, 0.0, qty)
    floor = float(bids[0][0]) * (1.0 - protect_pct)
    left, quote, done = qty, 0.0, 0.0
    for level in bids:
        price, amount = float(level[0]), float(level[1])
        if price < floor or left <= 1e-12:
            break
        take = min(left, amount)
        quote += take * price
        done += take
        left -= take
    return Fill(quote / done if done else None, done, max(left, 0.0))


def buy(asks: Levels, notional: float, protect_pct: float = PROTECT_PCT) -> Fill:
    """Spend `notional` quote on `asks`, no higher than best ask × (1 + protect_pct)."""
    if not asks:
        return Fill(None, 0.0, notional)
    cap = float(asks[0][0]) * (1.0 + protect_pct)
    left, quote, done = notional, 0.0, 0.0
    for level in asks:
        price, amount = float(level[0]), float(level[1])
        if price > cap or left <= 1e-9:
            break
        take_quote = min(left, price * amount)
        quote += take_quote
        done += take_quote / price
        left -= take_quote
    return Fill(quote / done if done else None, done, max(left, 0.0))


def spread_bps(bids: Levels, asks: Levels) -> float | None:
    if not bids or not asks:
        return None
    bid, ask = float(bids[0][0]), float(asks[0][0])
    return (ask - bid) / ((ask + bid) / 2) * 1e4


def depth_within(levels: Levels, pct: float = 0.01) -> float:
    """Quote value of the levels within `pct` of the best price (either side)."""
    if not levels:
        return 0.0
    best = float(levels[0][0])
    return sum(
        float(p) * float(q) for p, q, *_ in levels if abs(float(p) - best) <= best * pct
    )
