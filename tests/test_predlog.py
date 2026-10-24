import hashlib
import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from signal_engine.backtest.engine import Bars
from signal_engine.record import predlog
from signal_engine.record.predlog import (
    COMMITMENTS,
    DAY_MS,
    PREDICTIONS,
    ChainError,
    bars_sha256,
    dec,
    predict,
    verify,
)

START = datetime(2018, 1, 1, tzinfo=UTC)
OOS = datetime(2019, 8, 17, tzinfo=UTC)
PREREG = "ab" * 32


def _bars(days: int, seed: int = 7) -> Bars:
    """Synthetic random-walk bars on a daily grid from 2018-01-01 (no real prices)."""
    rng = np.random.default_rng(seed)
    ts = np.array([int((START + timedelta(days=d)).timestamp() * 1000) for d in range(days)], dtype=np.int64)
    u = np.exp(np.cumsum(rng.normal(0.0005, 0.03, size=(days, 2)), axis=0)) * 100.0
    oos = int((OOS - START).days)
    return Bars(ts, u.copy(), u.copy(), u.copy(), u.copy(), u, oos)


def _now(bars: Bars, last: int) -> datetime:
    """Two hours after bar `last` closed: that bar is the last closed one."""
    return datetime.fromtimestamp((int(bars.ts[last]) + DAY_MS) / 1000, UTC) + timedelta(hours=2)


def _cut(bars: Bars, last: int) -> Bars:
    return Bars(bars.ts[: last + 1], bars.open[: last + 1], bars.high[: last + 1], bars.low[: last + 1],
                bars.close[: last + 1], bars.u[: last + 1], bars.oos_start)


@pytest.fixture
def full() -> Bars:
    return _bars(700)


def _run(full: Bars, last: int, rec: Path, stamp: Path | None = None) -> list[dict[str, Any]]:
    return predict(_cut(full, last), rec, _now(full, last), prereg_sha256=PREREG, stamp_path=stamp)


def test_three_days_chain_and_same_day_rerun(full: Bars, tmp_path: Path) -> None:
    rec = tmp_path / "record"
    for last in (600, 601, 602):
        lines = _run(full, last, rec)
        assert [x["kind"] for x in lines] == ["prediction", "prediction"]
    assert verify(rec) == 6
    before = (rec / PREDICTIONS).read_bytes()
    assert _run(full, 602, rec) == []
    assert (rec / PREDICTIONS).read_bytes() == before


def test_prediction_fields(full: Bars, tmp_path: Path) -> None:
    line = _run(full, 600, tmp_path / "r")[0]
    d = line["decision"]
    assert line["symbol"] == "BTC-EUR" and d["state"] in ("long", "flat")
    assert 0.0 < float(d["p"]) < 1.0 and d["horizon_days"] == 7
    assert line["prereg_sha256"] == PREREG and len(line["params_sha256"]) == 64
    # the outcome window opens at the close of the bar in progress at commit, i.e. after the commit
    ref_close = int(line["as_of_ts"]) + 2 * DAY_MS
    assert line["committed_at"] < predlog.iso(ref_close)


def test_public_line_has_no_salt(full: Bars, tmp_path: Path) -> None:
    rec = tmp_path / "r"
    _run(full, 600, rec)
    public = [json.loads(x) for x in (rec / COMMITMENTS).read_text().splitlines()]
    private = [json.loads(x) for x in (rec / PREDICTIONS).read_text().splitlines()]
    assert all("salt" not in p and "decision" not in p for p in public)
    assert [p["commitment"] for p in public] == [p["commitment"] for p in private]


def test_gap_lines_for_missed_days(full: Bars, tmp_path: Path) -> None:
    rec = tmp_path / "r"
    _run(full, 600, rec)
    lines = _run(full, 603, rec)
    gaps = [x for x in lines if x["kind"] == "gap"]
    assert len(gaps) == 4  # 2 missed days x 2 coins, never a back-filled prediction
    assert {g["day"] for g in gaps} == {predlog.iso(int(full.ts[d]))[:10] for d in (601, 602)}
    assert sum(x["kind"] == "prediction" for x in lines) == 2


def test_outcome_after_horizon(full: Bars, tmp_path: Path) -> None:
    rec = tmp_path / "r"
    first = _run(full, 600, rec)
    assert not [x for x in _run(full, 607, rec) if x["kind"] == "outcome"]  # out bar 608 not closed yet
    outs = [x for x in _run(full, 609, rec) if x["kind"] == "outcome"]
    assert len(outs) == 2
    btc = next(o for o in outs if o["symbol"] == "BTC-EUR")
    assert btc["prediction_seq"] == first[0]["seq"]
    assert btc["hit"] == (full.u[608, 0] > full.u[601, 0])


def test_missing_outcome_bar_stays_pending(full: Bars, tmp_path: Path) -> None:
    rec = tmp_path / "r"
    _run(full, 600, rec)
    full.u[608, :] = np.nan
    lines = _run(full, 610, rec)
    assert not [x for x in lines if x["kind"] == "outcome"]


def test_stale_bars_give_no_decision(full: Bars, tmp_path: Path) -> None:
    lines = predict(_cut(full, 600), tmp_path / "r", _now(full, 602), prereg_sha256=PREREG)
    assert [x["kind"] for x in lines] == ["no_decision", "no_decision"]
    assert "fetch-candles" in lines[0]["reason"]


def test_tampered_line_fails(full: Bars, tmp_path: Path) -> None:
    rec = tmp_path / "r"
    _run(full, 600, rec)
    path = rec / PREDICTIONS
    rows = path.read_text().splitlines()
    rows[-1] = rows[-1].replace('"state": "', '"state": "x')  # the last line: only the commitment can tell
    assert rows[-1] != path.read_text().splitlines()[-1]
    path.write_text("\n".join(rows) + "\n")
    with pytest.raises(ChainError, match="commitment"):
        verify(rec)


def test_reordered_or_deleted_middle_line_fails(full: Bars, tmp_path: Path) -> None:
    rec = tmp_path / "r"
    for last in (600, 601):
        _run(full, last, rec)
    path = rec / PREDICTIONS
    rows = path.read_text().splitlines()
    for broken in ([rows[0], rows[2], rows[1], rows[3]], [rows[0], rows[2], rows[3]]):
        path.write_text("\n".join(broken) + "\n")
        with pytest.raises(ChainError) as err:
            verify(rec)
        assert err.value.seq == 2  # the first line whose prev_sha256 no longer matches


def test_cut_tail_fails_against_stamp(full: Bars, tmp_path: Path) -> None:
    rec, stamp = tmp_path / "r", tmp_path / "ops" / predlog.STAMP
    for last in (600, 601):
        _run(full, last, rec, stamp)
    for name in (PREDICTIONS, COMMITMENTS):
        rows = (rec / name).read_text().splitlines()
        (rec / name).write_text("\n".join(rows[:-1]) + "\n")
    assert verify(rec) == 3  # the chain alone cannot see a cut tail
    with pytest.raises(ChainError, match="cut off"):
        verify(rec, stamp)


def test_clock_backwards_refused(full: Bars, tmp_path: Path) -> None:
    rec = tmp_path / "r"
    _run(full, 602, rec)
    with pytest.raises(ChainError, match="backwards"):  # a run dated a day earlier than the last line
        _run(full, 601, rec)


def test_two_writers_same_day_write_once(full: Bars, tmp_path: Path) -> None:
    rec = tmp_path / "r"
    threads = [threading.Thread(target=_run, args=(full, 600, rec)) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert verify(rec) == 2
    assert [json.loads(x)["seq"] for x in (rec / PREDICTIONS).read_text().splitlines()] == [1, 2]


def test_dec_refuses_nan_inf_none() -> None:
    for bad in (float("nan"), float("inf"), None):
        with pytest.raises(ValueError):
            dec(bad)
    assert dec(1.5) == "1.500000"


def test_input_hash_is_truncation_invariant(full: Bars) -> None:
    a = bars_sha256(full.ts[:600], full.u[:600, 0])
    assert a == bars_sha256(_cut(full, 650).ts[:600], _cut(full, 650).u[:600, 0])
    assert a != bars_sha256(full.ts[:601], full.u[:601, 0])


def test_train_table_uses_only_bars_before_oos(full: Bars) -> None:
    table = predlog.train_table(full)
    later = _bars(700)
    later.u[full.oos_start :, :] *= 3.0  # change every OOS bar: the table must not move
    assert predlog.train_table(later) == table
    assert hashlib.sha256(json.dumps(table, sort_keys=True).encode()).hexdigest()


def test_cli_predict_exits_1_without_bars(tmp_path: Path) -> None:
    from signal_engine.__main__ import main

    assert main(["predict", "--data-dir", str(tmp_path), "--now", "2026-10-23T12:00:00Z"]) == 1
    assert not (tmp_path / "record" / PREDICTIONS).exists()  # nothing is written when the inputs fail


def test_commitment_depends_on_the_salt(full: Bars, tmp_path: Path) -> None:
    rec = tmp_path / "r"
    _run(full, 600, rec)
    path = rec / PREDICTIONS
    rows = path.read_text().splitlines()
    last = json.loads(rows[-1])
    rows[-1] = rows[-1].replace(last["salt"], "00" * 32)
    path.write_text("\n".join(rows) + "\n")
    with pytest.raises(ChainError, match="commitment"):
        verify(rec)


def test_rerun_after_an_outcome_writes_it_once(full: Bars, tmp_path: Path) -> None:
    rec = tmp_path / "r"
    _run(full, 600, rec)
    assert sum(x["kind"] == "outcome" for x in _run(full, 609, rec)) == 2
    assert not [x for x in _run(full, 610, rec) if x["kind"] == "outcome"]


def test_unclosed_future_bar_gives_no_outcome_and_no_prediction(full: Bars, tmp_path: Path) -> None:
    rec = tmp_path / "r"
    _run(full, 600, rec)
    # a source that leaks bar 608 before it has closed: the outcome bar of the 600 prediction
    lines = predict(_cut(full, 608), rec, _now(full, 607), prereg_sha256=PREREG)
    assert not [x for x in lines if x["kind"] in ("outcome", "prediction")]
    assert [x["kind"] for x in lines if x["kind"] != "gap"] == ["no_decision", "no_decision"]


def test_missing_as_of_close_gives_no_decision(full: Bars, tmp_path: Path) -> None:
    full.u[600, 0] = np.nan
    lines = _run(full, 600, tmp_path / "r")
    kinds = [(x["symbol"], x["kind"]) for x in lines]
    assert kinds == [("BTC-EUR", "no_decision"), ("ETH-EUR", "prediction")]


def test_duplicate_bar_ts_is_refused(full: Bars, tmp_path: Path) -> None:
    bars = _cut(full, 600)
    bars.ts[599] = bars.ts[598]
    with pytest.raises(ValueError, match="twice"):
        predict(bars, tmp_path / "r", _now(full, 600), prereg_sha256=PREREG)
    assert not (tmp_path / "r" / PREDICTIONS).exists()


def test_git_head_does_not_depend_on_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import signal_engine.__main__ as cli

    if not (Path(cli.__file__).resolve().parents[2] / ".git").exists():
        pytest.skip("not a git checkout (exported copy)")
    monkeypatch.chdir(tmp_path)  # a folder outside the repo, as when predict runs from $TMPDIR
    assert len(cli._git_head()) == 40
