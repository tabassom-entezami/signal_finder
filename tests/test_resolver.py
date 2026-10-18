import pytest

from signal_engine.backtest import resolver as rs

FILLS = rs.BarFillModel(gap_g=0.0351, cost_stop=0.0052, cost_side=0.004)


def test_stop_wins_when_one_bar_touches_stop_and_tp() -> None:
    ex = rs.check_bar(rs.Bar(100, 120, 90, 110), rs.ExitRule(stop=95, tp=115), FILLS, 0)
    assert ex is not None and ex.reason == rs.SL
    assert ex.price == pytest.approx(95 * (1 - 0.0351))
    assert ex.cost_rate == 0.0052


def test_gap_through_stop_fills_at_the_open_minus_overshoot() -> None:
    ex = rs.check_bar(rs.Bar(90, 92, 85, 91), rs.ExitRule(stop=95), FILLS, 0)
    assert ex is not None and ex.reason == rs.SL
    assert ex.price == pytest.approx(90 * (1 - 0.0351))


def test_tp_and_time_stop_and_no_exit() -> None:
    tp = rs.check_bar(rs.Bar(100, 116, 99, 110), rs.ExitRule(stop=95, tp=115), FILLS, 0)
    assert tp is not None and (tp.reason, tp.price) == (rs.TP, 115)
    ts = rs.check_bar(rs.Bar(100, 101, 99, 100.5), rs.ExitRule(stop=95, horizon_end=10), FILLS, 10)
    assert ts is not None and (ts.reason, ts.price) == (rs.TIME_STOP, 100.5)
    assert rs.check_bar(rs.Bar(100, 101, 99, 100.5), rs.ExitRule(stop=95, horizon_end=10), FILLS, 9) is None


def test_resolve_time_out_and_open_episodes_are_counted() -> None:
    path = [(t, rs.Bar(100, 101, 99, 99.5), rs.ExitRule(stop=90, horizon_end=3)) for t in range(1, 5)]
    timed = rs.resolve(1, 100.0, 1.0, 0.004, path, FILLS)
    assert (timed.reason, timed.exit_ts) == (rs.TIME_STOP, 3)
    assert not rs.label(timed)  # -0.5 move minus costs
    still_open = rs.resolve(1, 100.0, 1.0, 0.004, [(t, b, rs.ExitRule(stop=90)) for t, b, _ in path], FILLS)
    assert still_open.reason == rs.OPEN_MARKED
    assert still_open.pnl == pytest.approx(-100 * 1.004 + 99.5 * (1 - 0.004))
    table = rs.exit_table([timed, still_open])
    assert table["episodes"] == 2 and table[rs.TIME_STOP] == 1 and table[rs.OPEN_MARKED] == 1
    assert table["win_rate"] == 0.0  # time-outs stay in the denominator


def test_r_and_label_on_a_stop() -> None:
    path = [(1, rs.Bar(100, 101, 94, 95), rs.ExitRule(stop=96))]
    ep = rs.resolve(1, 100.0, 2.0, 0.004, path, FILLS)
    exit_value = 2 * 96 * (1 - 0.0351)
    assert ep.pnl == pytest.approx(-200 * 1.004 + exit_value * (1 - 0.0052))
    assert ep.r == pytest.approx(ep.pnl / (2 * (100 - 96)))
    assert not rs.label(ep)


def test_commit_guard_refuses_a_path_before_the_entry() -> None:
    with pytest.raises(ValueError, match="before the entry"):
        rs.resolve(5, 100.0, 1.0, 0.0, [(4, rs.Bar(100, 101, 99, 100), rs.ExitRule(stop=90))], FILLS)
