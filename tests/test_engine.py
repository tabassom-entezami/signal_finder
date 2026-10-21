import hashlib
from collections.abc import Sequence

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from signal_engine.backtest import engine as en
from signal_engine.backtest import resolver as rs
from signal_engine.backtest.synthetic import DAY_MS, START_MS, synthetic_bars
from signal_engine.strategy import b_spot

NAN = np.nan


def _bars(
    o: Sequence[float], h: Sequence[float], lo: Sequence[float], c: Sequence[float], coins: int = 1
) -> en.Bars:
    """Coin 0 from the lists; other coins have no bars (NaN). Bar 0 is Monday 2001-01-01."""
    n = len(c)
    pad = np.full((n, coins - 1), NAN)
    arr = [np.column_stack([np.asarray(x, float), pad]) for x in (o, h, lo, c)]
    ts = (START_MS + DAY_MS * np.arange(n)).astype(np.int64)
    return en.Bars(ts, arr[0], arr[1], arr[2], arr[3], arr[3], 0)


def _const(n: int, values: list[float]) -> np.ndarray:
    return np.tile(np.asarray(values, float), (n, 1))


def test_vectorised_parity_no_stop_no_band_no_cost() -> None:
    """Independent re-implementation: equity = cumprod(1 + sum 0.5 tau (c/o - 1))."""
    b = synthetic_bars(3, 400)
    tau = np.random.default_rng(1).uniform(0.05, 1.0, size=b.close.shape)
    params = en.Params(k_stop=1e9, band=0.0, cost_side=0.0, cost_stop=0.0, account=False)
    res = en.run_path(b, tau, np.full(b.close.shape, 0.02), params)
    growth = 1.0 + (b_spot.HALF * tau[:-1] * (b.close[1:] / b.open[1:] - 1.0)).sum(axis=1)
    expected = np.r_[1.0, np.cumprod(growth)]
    np.testing.assert_allclose(res.equity, expected, rtol=0, atol=1e-9)


def test_entry_cost_and_mark_by_hand() -> None:
    b = _bars([100, 100], [100, 112], [100, 100], [100, 110])
    res = en.run_path(b, _const(2, [1.0]), _const(2, [0.001]), en.Params(account=False))
    assert res.fills[0][2] == en.ENTRY
    assert res.equity[1] == pytest.approx(1 - 0.5 - 0.5 * 0.004 + 0.005 * 110)


def test_catastrophe_stop_is_4_sigma_and_reset_at_each_open() -> None:
    """Day 1 low 93 stays above 100*(1-0.08); day 2 the stop is re-placed at 110*(1-0.08) and fills."""
    b = _bars([100, 100, 110], [100, 110, 111], [100, 93, 100], [100, 110, 105])
    res = en.run_path(b, _const(3, [1.0]), _const(3, [0.02]), en.Params(account=False))
    stop = 110 * (1 - 4 * 0.02)
    kind = [(f[0], f[2]) for f in res.fills]
    assert kind == [(1, en.ENTRY), (2, rs.SL)]
    j, _, _, dq, px, cost = res.fills[1]
    assert px == pytest.approx(stop * (1 - 0.0351))
    assert cost == pytest.approx(-dq * px * 0.0052)
    assert res.episodes[0].reason == rs.SL


def test_room_bound_stops_keep_equity_above_94_pct_of_peak_then_freeze_7_days() -> None:
    n = 12
    lo = [100.0, 99.0] + [100.0] * (n - 2)
    b = en.Bars(
        (START_MS + DAY_MS * np.arange(n)).astype(np.int64),
        _const(n, [100, 100]),
        _const(n, [100, 100]),
        np.column_stack([lo, lo]),
        _const(n, [100, 100]),
        _const(n, [100, 100]),
        0,
    )
    res = en.run_path(b, _const(n, [1.0, 1.0]), _const(n, [0.03, 0.03]), en.Params())
    reasons = [(f[0], f[2]) for f in res.fills]
    assert reasons[2:4] == [(1, rs.SL_ROOM), (1, rs.SL_ROOM)]
    assert res.equity[1] >= (1 - b_spot.FREEZE_DD) * 1.0 - 1e-12  # worst overshoot on both coins
    assert res.modes[1:9] == [en.FROZEN] * 8
    assert res.modes[9] == en.ACTIVE
    assert [f[0] for f in res.fills[4:6]] == [9, 9]  # first trades after 7 days without trades
    assert res.freezes == 1


def test_floor_binding_after_restart_stops_for_good() -> None:
    n = 14
    lo = np.full(n, 100.0)
    lo[1] = lo[10] = 99.0
    b = en.Bars(
        (START_MS + DAY_MS * np.arange(n)).astype(np.int64),
        _const(n, [100, 100]),
        _const(n, [100, 100]),
        np.column_stack([lo, lo]),
        _const(n, [100, 100]),
        _const(n, [100, 100]),
        0,
    )
    res = en.run_path(b, _const(n, [1.0, 1.0]), _const(n, [0.03, 0.03]), en.Params(apply_floor=True))
    assert res.stopped  # restart peak ~0.94 -> the 91 % floor binds, no room for a stop: flatten
    assert res.equity[-1] >= b_spot.FLOOR - 1e-12
    assert all(f[0] <= 9 for f in res.fills)
    assert res.episodes[-1].reason == rs.STOPPED


def test_weekly_loss_limit_reduces_until_next_iso_week() -> None:
    n = 10
    lo = np.full(n, 100.0)
    lo[2] = 90.0
    b = en.Bars(
        (START_MS + DAY_MS * np.arange(n)).astype(np.int64),
        _const(n, [100, 100]),
        _const(n, [100, 100]),
        np.column_stack([lo, lo]),
        _const(n, [100, 100]),
        _const(n, [100, 100]),
        0,
    )
    res = en.run_path(b, _const(n, [0.5, 0.5]), _const(n, [0.01, 0.01]), en.Params())
    assert [f[2] for f in res.fills if f[0] == 2] == [rs.SL, rs.SL]
    assert res.modes[2:7] == [en.REDUCING] * 5  # reduce-only Wednesday to Sunday
    assert sorted({f[0] for f in res.fills if f[2] == en.ENTRY}) == [1, 7]  # re-entry on Monday
    # Equity is 0.952 against a 0.94 limit: no room for the overshoot, so the re-entry flattens and freezes.
    assert [f[2] for f in res.fills if f[0] == 7][-2:] == [rs.FREEZE, rs.FREEZE] and res.modes[7] == en.FROZEN


def test_halt_gap_beyond_the_stop_fills_at_the_gapped_open_without_overshoot() -> None:
    b = _bars([100, 100, 100], [100, 101, 101], [100, 99, 99], [100, 100, 100])
    res = en.run_path(b, _const(3, [1.0]), _const(3, [0.02]), en.Params(account=False), halt_day=2)
    _, _, reason, _, px, _ = res.fills[1]
    assert reason == rs.SL
    assert px == pytest.approx(100 * (1 - b_spot.HALT_GAP))
    assert [f[2] for f in res.fills].count(en.ENTRY) == 1  # no re-entry on the gap bar


def test_halt_gap_inside_the_stop_keeps_the_position_on_the_scaled_bar() -> None:
    b = _bars([100, 100, 100], [100, 101, 101], [100, 99, 99], [100, 100, 100])
    res = en.run_path(b, _const(3, [0.3]), _const(3, [0.05]), en.Params(), halt_day=2)
    assert [f[2] for f in res.fills] == [en.ENTRY]
    q = res.fills[0][3]
    assert res.equity[2] == pytest.approx(res.equity[1] - q * 100 * b_spot.HALT_GAP)


def test_no_room_for_a_stop_flattens_at_the_open_and_freezes() -> None:
    n = 3
    one = _const(n, [100, 100])
    b = en.Bars((START_MS + DAY_MS * np.arange(n)).astype(np.int64), one, one, one, one, one, 0)
    res = en.run_path(b, _const(n, [1.0, 1.0]), _const(n, [0.03, 0.03]), en.Params(cost_side=0.01))
    assert [f[2] for f in res.fills] == [en.ENTRY, en.ENTRY, rs.FREEZE, rs.FREEZE]
    assert res.freezes == 1 and res.modes[1] == en.FROZEN


def test_fixture_15_bars_backtest_and_labeller_agree() -> None:
    """The shared 15-bar fixture through two adapters (the paper-simulator one comes with the simulator)."""
    c = [100, 101, 102, 101, 103, 104, 105, 104, 106, 107, 108, 107, 109, 110, 111]
    o = [100] + c[:-1]
    lo = [x * 0.97 for x in o]
    lo[9] = o[9] * 0.95  # touches the 4 % stop
    h = [max(a, z) * 1.01 for a, z in zip(o, c, strict=True)]
    b = _bars(o, h, lo, c, coins=2)
    res = en.run_path(b, _const(15, [1.0, NAN]), _const(15, [0.01, NAN]), en.Params(account=False))
    bt = res.episodes[0]
    qty = 0.5 / o[1]
    fills = rs.BarFillModel(b_spot.G["BTC"], b_spot.COST_STOP, b_spot.COST_SIDE)
    path = [
        (int(b.ts[j]), rs.Bar(o[j], h[j], lo[j], c[j]), rs.ExitRule(stop=o[j] * 0.96)) for j in range(1, 15)
    ]
    lab = rs.resolve(int(b.ts[1]), o[1], qty, b_spot.COST_SIDE, path, fills)
    assert (bt.reason, bt.exit_ts) == (lab.reason, lab.exit_ts) == (rs.SL, int(b.ts[9]))
    assert bt.pnl == pytest.approx(lab.pnl, rel=1e-12)
    assert bt.r == pytest.approx(lab.r, rel=1e-12)


def test_open_episode_is_marked_at_the_end() -> None:
    b = _bars([100, 100], [100, 101], [100, 99.9], [100, 100])
    res = en.run_path(b, _const(2, [1.0]), _const(2, [0.01]), en.Params(account=False))
    assert res.episodes[0].reason == rs.OPEN_MARKED


def _digest(res: en.RunResult) -> str:
    return hashlib.sha256(res.equity.tobytes() + repr(res.fills).encode()).hexdigest()


def test_same_input_same_hash() -> None:
    b = synthetic_bars(5, 500)
    tau, sigma = en.strategy_inputs(b)
    runs = [_digest(en.run_path(b, tau, sigma, en.Params(), start=299)) for _ in range(2)]
    other = _digest(
        en.run_path(synthetic_bars(6, 500), *en.strategy_inputs(synthetic_bars(6, 500)), en.Params(), 299)
    )
    assert runs[0] == runs[1] != other


@settings(max_examples=15, deadline=None, database=None)
@given(seed=st.integers(0, 2**32 - 1), cut=st.integers(320, 449))
def test_backtest_is_truncation_invariant(seed: int, cut: int) -> None:
    """Equity and fills up to bar t are identical with or without later bars."""
    full = synthetic_bars(seed, 450)
    part = en.Bars(
        full.ts[: cut + 1],
        full.open[: cut + 1],
        full.high[: cut + 1],
        full.low[: cut + 1],
        full.close[: cut + 1],
        full.u[: cut + 1],
        full.oos_start,
    )
    a = en.run_path(full, *en.strategy_inputs(full), en.Params(), start=299)
    p = en.run_path(part, *en.strategy_inputs(part), en.Params(), start=299)
    np.testing.assert_array_equal(p.equity, a.equity[: len(p.equity)])
    assert p.fills == [f for f in a.fills if f[0] < len(p.equity)]


def test_weekly_limit_is_also_checked_at_stop_fills() -> None:
    """BTC's stop takes the week to -4.2 %; ETH rallies by the close (-2.2 %): reduce-only all the same."""
    n = 9
    btc_lo = np.full(n, 100.0)
    btc_lo[2] = 90.0
    eth = np.r_[100.0, 100.0, np.full(n - 2, 120.0)]
    eth_open = np.r_[100.0, 100.0, 100.0, np.full(n - 3, 120.0)]
    flat = np.full(n, 100.0)
    b = en.Bars(
        (START_MS + DAY_MS * np.arange(n)).astype(np.int64),
        np.column_stack([flat, eth_open]),
        np.column_stack([flat, eth]),
        np.column_stack([btc_lo, np.minimum(eth_open, eth)]),
        np.column_stack([flat, eth]),
        np.column_stack([flat, eth]),
        0,
    )
    res = en.run_path(b, _const(n, [1.0, 0.2]), _const(n, [0.01, 0.01]), en.Params())
    assert [f[2] for f in res.fills if f[0] == 2] == [rs.SL]
    assert res.equity[2] / res.equity[0] - 1 > -b_spot.WEEKLY_LIMIT  # the close alone would not reduce
    assert res.modes[2] == en.REDUCING
    assert [f[0] for f in res.fills if f[1] == 0 and f[2] == en.ENTRY] == [1, 7]


def test_a_buy_never_borrows_cash() -> None:
    """Coin 0 doubles while held, then coin 1 enters at w = 1: the buy is capped at the cash at hand."""
    n = 5
    ts = (START_MS + DAY_MS * np.arange(n)).astype(np.int64)
    p0 = np.array([100.0, 100.0, 200.0, 200.0, 200.0])
    px = np.column_stack([p0, np.full(n, 100.0)])
    b = en.Bars(ts, px, px * 1.001, px * 0.999, px, px, 0)
    tau = np.column_stack([np.ones(n), np.array([0.0, 0.0, 1.0, 1.0, 1.0])])
    params = en.Params(account=False)
    res = en.run_path(b, tau, _const(n, [0.01, 0.01]), params)
    cash, q = 1.0, [0.0, 0.0]
    for _, i, _, dq, price, cost in res.fills:
        cash -= dq * price + cost
        q[i] += dq
        assert cash >= -1e-12
    entry_1 = [f for f in res.fills if f[1] == 1 and f[2] == en.ENTRY]
    cash_left = 1.0 - 0.5 * (1 + params.cost_side)  # after coin 0's entry; coin 0 is never sold
    assert entry_1 and entry_1[0][3] * 100.0 * (1 + params.cost_side) == pytest.approx(cash_left, rel=1e-9)
