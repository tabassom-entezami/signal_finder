from datetime import UTC, datetime

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from signal_engine.backtest import engine as en
from signal_engine.backtest import resolver as rs
from signal_engine.backtest.synthetic import DAY_MS, START_MS
from signal_engine.sim import books
from signal_engine.sim import papersim as ps
from signal_engine.strategy import b_spot

NO_FEES = ps.Fees(0.0, 0.0, 0.0)
T0 = int(datetime(2026, 11, 2, 12, tzinfo=UTC).timestamp() * 1000)
CASH = 1000.0  # a neutral starting cash; the real one is the owner's `PAPER_CASH_EUR`


@pytest.fixture(autouse=True)
def _paper_cash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPER_CASH_EUR", str(CASH))


def _book(best: float, step: float, qty: float, n: int = 10) -> list[list[float]]:
    return [[best + step * k, qty] for k in range(n)]


def test_sell_walk_matches_a_hand_computed_book() -> None:
    fill = books.sell([[100.0, 1.0], [99.0, 2.0], [98.0, 5.0]], 2.5)
    assert fill.filled == 2.5 and fill.residual == 0.0
    assert fill.vwap == pytest.approx((100 * 1 + 99 * 1.5) / 2.5)


def test_buy_walk_matches_a_hand_computed_book() -> None:
    fill = books.buy([[100.0, 1.0], [101.0, 1.0]], 150.0)
    assert fill.filled == pytest.approx(1 + 50 / 101)
    assert fill.vwap == pytest.approx(150 / (1 + 50 / 101))


def test_walk_stops_at_the_4_percent_protection_and_leaves_a_residual() -> None:
    bids = [[100.0, 1.0], [96.5, 1.0], [95.9, 10.0]]  # 95.9 is beyond 4 % below 100
    fill = books.sell(bids, 5.0)
    assert fill.filled == 2.0 and fill.residual == 3.0
    asks = [[100.0, 1.0], [104.0, 1.0], [104.1, 10.0]]
    assert books.buy(asks, 1000.0).filled == 2.0


def test_empty_books_fill_nothing() -> None:
    assert books.sell([], 1.0) == books.Fill(None, 0.0, 1.0)
    assert books.buy([], 10.0) == books.Fill(None, 0.0, 10.0)


@settings(max_examples=50, deadline=None, database=None)
@given(
    best=st.floats(10, 1e5),
    step=st.floats(1e-4, 0.01),
    qty=st.floats(1e-3, 5),
    want=st.floats(1e-3, 20),
)
def test_no_fill_is_better_than_the_book(best: float, step: float, qty: float, want: float) -> None:
    bids = [[best * (1 - step * k), qty] for k in range(20)]
    asks = [[best * (1 + step * k), qty] for k in range(20)]
    s, b = books.sell(bids, want), books.buy(asks, want * best)
    assert s.vwap is not None and s.vwap <= best * (1 + 1e-12)
    assert b.vwap is not None and b.vwap >= best * (1 - 1e-12)


def test_entry_arms_the_stop_in_the_same_step() -> None:
    acct = ps.Account()
    ev = ps.enter(acct, 0, T0, _book(100, 0.1, 10), 200.0, 96.0, ps.BITVAVO_FEES)
    pos = acct.positions[0]
    assert ev.kind == ps.ENTRY and pos.mode == ps.PROTECTED and pos.stop == 96.0
    assert acct.cash == pytest.approx(CASH - 200.0 * (1 + ps.BITVAVO_FEES.entry))


def test_stop_trigger_walks_the_bids_and_retries_the_residual_every_5_s() -> None:
    acct = ps.Account()
    ps.enter(acct, 0, T0, [[100.0, 3.0]], 300.0, 96.0, NO_FEES)
    assert ps.on_book(acct, 0, T0 + 5_000, [[96.5, 9.0]], NO_FEES) == []  # above the stop
    ev = ps.on_book(acct, 0, T0 + 10_000, [[95.0, 1.0], [80.0, 9.0]], NO_FEES)
    assert ev[0].kind == rs.SL and ev[0].qty == 1.0
    pos = acct.positions[0]
    assert pos.mode == ps.RESIDUAL and pos.due_ms == T0 + 15_000 and pos.qty == pytest.approx(2.0)
    assert ps.on_book(acct, 0, T0 + 14_000, [[94.0, 9.0]], NO_FEES) == []  # not due yet
    ps.on_book(acct, 0, T0 + 15_000, [[94.0, 9.0]], NO_FEES)
    assert 0 not in acct.positions
    ep = acct.closed[0]
    assert ep.reason == rs.SL and ep.pnl == pytest.approx(95.0 + 2 * 94.0 - 300.0)


def test_an_overdue_residual_breaks_the_invariant() -> None:
    acct = ps.Account()
    ps.enter(acct, 0, T0, [[100.0, 2.0]], 200.0, 96.0, NO_FEES)
    ps.on_book(acct, 0, T0 + 1_000, [[95.0, 1.0]], NO_FEES)
    with pytest.raises(ps.InvariantError):
        ps.check_invariant(acct, T0 + 1_000 + ps.RESIDUAL_DELAY_MS * 2 + 1)


def test_a_position_without_a_stop_breaks_the_invariant() -> None:
    acct = ps.Account()
    ps.enter(acct, 0, T0, [[100.0, 2.0]], 200.0, 96.0, NO_FEES)
    with pytest.raises(ps.InvariantError):
        ps.rearm(acct, 0, 0.0, T0)


def test_observation_gap_fill_is_the_worse_of_the_candle_and_the_first_book() -> None:
    fills = rs.BarFillModel(0.0, 0.0, 0.0)
    for bids, want in (([[94.0, 9.0]], 94.0), ([[97.0, 9.0]], 95.5)):  # the candle's low is 95.5
        acct = ps.Account()
        ps.enter(acct, 0, T0, [[100.0, 1.0]], 100.0, 96.0, NO_FEES)
        ev = ps.on_candle(acct, 0, T0 + 60_000, rs.Bar(99.0, 99.5, 95.5, 98.0), fills, bids)
        assert ev[0].price == pytest.approx(want) and ev[0].observed is False


def test_entry_guards() -> None:
    bids, asks = _book(99.95, -0.01, 50), _book(100.0, 0.01, 50)
    ok = int(datetime(2026, 11, 2, 12, tzinfo=UTC).timestamp() * 1000)
    assert ps.entry_refusal(ok, 100.0, bids, asks) is None
    assert ps.entry_refusal(ok, 100.0, bids, asks, status="major") == "GUARD_STATUS"
    assert ps.entry_refusal(ok, 100.0, bids, asks, maintenance_ms=ok + 3_600_000) == "GUARD_MAINTENANCE"
    pre_close = int(datetime(2026, 11, 2, 23, 58, 30, tzinfo=UTC).timestamp() * 1000)
    assert ps.entry_refusal(pre_close, 100.0, bids, asks) == "GUARD_PRE_CLOSE"
    assert ps.entry_refusal(ok, 100.0, [[99.0, 50.0]], asks) == "GUARD_SPREAD"
    assert ps.entry_refusal(ok, 10_000.0, bids, asks) == "GUARD_DEPTH"


def test_signal_exit_sells_by_walk() -> None:
    acct = ps.Account()
    ps.enter(acct, 0, T0, [[100.0, 1.0]], 100.0, 96.0, NO_FEES)
    ev = ps.exit_signal(acct, 0, T0 + DAY_MS, [[110.0, 5.0]], NO_FEES)
    assert ev[0].kind == rs.SIGNAL_EXIT and acct.closed[0].pnl == pytest.approx(10.0)


def test_window_end_mark_counts_the_open_episode_and_keeps_the_position() -> None:
    acct = ps.Account()
    ps.enter(acct, 0, T0, [[100.0, 1.0]], 100.0, 96.0, NO_FEES)
    ep = ps.mark_window_end(acct, 0, 103.0, T0 + DAY_MS, 0.0)
    assert ep is not None and ep.reason == rs.OPEN_MARKED and ep.pnl == pytest.approx(3.0)
    assert acct.positions[0].episode.reason is None


def test_fixture_15_bars_papersim_matches_backtest_and_labeller() -> None:
    """The shared 15-bar fixture, third adapter: the papersim state machine on daily candles."""
    c = [100, 101, 102, 101, 103, 104, 105, 104, 106, 107, 108, 107, 109, 110, 111]
    o = [100] + c[:-1]
    lo = [x * 0.97 for x in o]
    lo[9] = o[9] * 0.95  # touches the 4 % stop
    h = [max(a, z) * 1.01 for a, z in zip(o, c, strict=True)]
    ts = [START_MS + DAY_MS * j for j in range(15)]
    fills = rs.BarFillModel(b_spot.G["BTC"], b_spot.COST_STOP, b_spot.COST_SIDE)
    qty = 0.5 / o[1]
    path = [(ts[j], rs.Bar(o[j], h[j], lo[j], c[j]), rs.ExitRule(stop=o[j] * 0.96)) for j in range(1, 15)]
    lab = rs.resolve(ts[1], o[1], qty, b_spot.COST_SIDE, path, fills)

    acct = ps.Account(cash=1.0)
    fees = ps.Fees(b_spot.COST_SIDE, b_spot.COST_STOP, b_spot.COST_SIDE)
    ps.enter(acct, 0, ts[1], [[o[1], 1.0]], qty * o[1], o[1] * 0.96, fees)
    for j in range(1, 15):
        if 0 not in acct.positions:
            break
        ps.rearm(acct, 0, o[j] * 0.96, ts[j])  # the daily stop reset at the open
        ps.on_candle(acct, 0, ts[j], rs.Bar(o[j], h[j], lo[j], c[j]), fills)
    sim = acct.closed[0]

    pad = np.full(15, np.nan)
    o_, h_, lo_, c_ = (np.column_stack([np.asarray(x, float), pad]) for x in (o, h, lo, c))
    bt_bars = en.Bars(np.asarray(ts, dtype=np.int64), o_, h_, lo_, c_, c_, 0)
    tau, sigma = np.tile([1.0, np.nan], (15, 1)), np.tile([0.01, np.nan], (15, 1))
    bt = en.run_path(bt_bars, tau, sigma, en.Params(account=False)).episodes[0]
    assert (sim.reason, sim.exit_ts) == (lab.reason, lab.exit_ts) == (bt.reason, bt.exit_ts) == (rs.SL, ts[9])
    assert sim.pnl == pytest.approx(lab.pnl, rel=1e-12) and sim.pnl == pytest.approx(bt.pnl, rel=1e-12)
    assert sim.r == pytest.approx(lab.r, rel=1e-12)


def test_observation_gap_partial_walk_leaves_a_residual() -> None:
    """Holding 3.0, the first book after the gap has 0.1 @ 94, the minute's low is 90."""
    acct = ps.Account()
    ps.enter(acct, 0, T0, [[100.0, 3.0]], 300.0, 96.0, NO_FEES)
    fills = rs.BarFillModel(0.0, 0.0, 0.0)
    ev = ps.on_candle(acct, 0, T0 + 60_000, rs.Bar(99.0, 99.0, 90.0, 92.0), fills, [[94.0, 0.1]])
    assert ev[0].qty == pytest.approx(0.1) and ev[0].price == 90.0 and ev[0].observed is False
    pos = acct.positions[0]
    assert pos.mode == ps.RESIDUAL and pos.market == "EUR" and pos.qty == pytest.approx(2.9)


def test_signal_exit_obeys_the_guards_and_keeps_the_stop() -> None:
    acct = ps.Account()
    ps.enter(acct, 0, T0, [[100.0, 1.0]], 100.0, 96.0, NO_FEES)
    pre_close = int(datetime(2026, 11, 2, 23, 59, tzinfo=UTC).timestamp() * 1000)
    assert ps.exit_signal(acct, 0, pre_close, [[110.0, 5.0]], NO_FEES)[0].kind == "GUARD_PRE_CLOSE"
    assert ps.exit_signal(acct, 0, T0, [[110.0, 5.0]], NO_FEES, status="major")[0].kind == "GUARD_STATUS"
    assert acct.positions[0].mode == ps.PROTECTED and acct.positions[0].qty == 1.0


def test_a_signal_exit_residual_is_sold_on_usdc_only() -> None:
    acct = ps.Account()
    ps.enter(acct, 0, T0, [[100.0, 2.0]], 200.0, 96.0, NO_FEES)
    ps.exit_signal(acct, 0, T0, [[105.0, 0.5]], NO_FEES)
    pos = acct.positions[0]
    assert pos.mode == ps.RESIDUAL and pos.market == "USDC"
    assert ps.on_book(acct, 0, T0 + 6_000, [[104.0, 9.0]], NO_FEES, market="EUR") == []
    ps.on_book(acct, 0, T0 + 6_000, [[104.0, 9.0]], NO_FEES, market="USDC")
    assert 0 not in acct.positions and acct.closed[0].reason == rs.SIGNAL_EXIT
