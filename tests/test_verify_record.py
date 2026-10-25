import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from signal_engine.backtest.engine import Bars
from signal_engine.data import pit
from signal_engine.record import predlog
from signal_engine.record.predlog import COMMITMENTS, DAY_MS, PREDICTIONS, ChainError
from signal_engine.validation.trials import read_trials

START = datetime(2018, 1, 1, tzinfo=UTC)
OOS = datetime(2019, 8, 17, tzinfo=UTC)
PREREG = "ab" * 32
# sha256 of the line bodies the scenario below wrote before `decide_body`/`outcome_body` were factored out
GOLDEN = "85c0125c92d4be99d3112a5ff23ec3665ee2a11b9af390909a432c25b49865c1"


def _bars(days: int, seed: int = 7) -> Bars:
    """Synthetic random-walk bars on a daily grid from 2018-01-01 (no real prices); as in test_predlog."""
    rng = np.random.default_rng(seed)
    ts = np.array([int((START + timedelta(days=d)).timestamp() * 1000) for d in range(days)], dtype=np.int64)
    u = np.exp(np.cumsum(rng.normal(0.0005, 0.03, size=(days, 2)), axis=0)) * 100.0
    return Bars(ts, u.copy(), u.copy(), u.copy(), u.copy(), u, int((OOS - START).days))


def _now(bars: Bars, last: int) -> datetime:
    """Two hours after bar `last` closed: that bar is the last closed one."""
    return datetime.fromtimestamp((int(bars.ts[last]) + DAY_MS) / 1000, UTC) + timedelta(hours=2)


class Store:
    """Bars with an ingest time per bar, like the candle store's first_available_time; `u` may be revised."""

    def __init__(self, full: Bars) -> None:
        self.full = full
        self.u = full.u.copy()
        self.ingest: dict[int, int] = {}

    def cut(self, last: int) -> Bars:
        b, n = self.full, last + 1
        return Bars(b.ts[:n], b.open[:n], b.high[:n], b.low[:n], b.close[:n], self.u[:n].copy(), b.oos_start)

    def run(self, rec: Path, last: int, now: datetime) -> list[dict[str, Any]]:
        """`fetch` the bars up to `last` one hour before `now`, then `predict` at `now`."""
        for n in range(last + 1):
            self.ingest.setdefault(n, int(now.timestamp() * 1000) - 3_600_000)
        return predlog.predict(self.cut(last), rec, now, prereg_sha256=PREREG, code="c0")

    def load(self, at: datetime) -> Bars:
        """The bars known strictly before `at`, with today's (possibly revised) values."""
        at_ms = int(at.timestamp() * 1000)
        last = -1
        while last + 1 in self.ingest and self.ingest[last + 1] < at_ms:
            last += 1
        return self.cut(last)


@pytest.fixture
def full() -> Bars:
    return _bars(700)


def _record(full: Bars, rec: Path) -> Store:
    """Predictions on 600, 601, 603 (one gap day), 612 (outcomes due) and a stale run a day later."""
    store = Store(full)
    for last in (600, 601, 603, 612):
        store.run(rec, last, _now(full, last))
    store.run(rec, 612, _now(full, 613))  # bar 613 not fetched yet: no_decision for both coins
    return store


def _seqs(rec: Path, kind: str, symbol: str | None = None) -> list[int]:
    return [int(x["seq"]) for x in read_trials(rec / PREDICTIONS)
            if x["kind"] == kind and (symbol is None or x["symbol"] == symbol)]


def test_predict_bodies_unchanged_by_the_refactor(full: Bars, tmp_path: Path) -> None:
    rec = tmp_path / "r"
    _record(full, rec)
    bodies = [predlog._body_keys(x) for x in read_trials(rec / PREDICTIONS)]
    assert {b["kind"] for b in bodies} == {"gap", "no_decision", "outcome", "prediction"}
    assert hashlib.sha256(predlog.canon(bodies).encode()).hexdigest() == GOLDEN


def test_every_line_rebuilds_including_the_stale_no_decision(full: Bars, tmp_path: Path) -> None:
    rec = tmp_path / "r"
    store = _record(full, rec)
    assert len(_seqs(rec, "no_decision")) == 2 and len(_seqs(rec, "outcome")) > 0
    assert predlog.replay(rec, store.load, PREREG) == []


def test_a_bar_known_at_commit_time_would_flip_the_stale_line(full: Bars, tmp_path: Path) -> None:
    rec = tmp_path / "r"
    store = _record(full, rec)
    store.ingest[613] = int(_now(full, 613).timestamp() * 1000) - 1  # as if fetched before that run
    out = predlog.replay(rec, store.load, PREREG)
    assert {seq for seq, _ in out} == set(_seqs(rec, "no_decision"))
    assert {reason for _, reason in out} == {"DECISION_DRIFT"}


def test_revised_input_bar_is_input_and_decision_drift(full: Bars, tmp_path: Path) -> None:
    rec = tmp_path / "r"
    store = _record(full, rec)
    first_btc = _seqs(rec, "prediction", "BTC-EUR")[0]
    store.u[600, 0] *= 3.0  # the as_of bar of the first BTC prediction, revised after the commit
    reasons = {r for seq, r in predlog.replay(rec, store.load, PREREG) if seq == first_btc}
    assert reasons == {"INPUT_DRIFT", "DECISION_DRIFT"}


def test_revised_pre_oos_bar_is_params_drift(full: Bars, tmp_path: Path) -> None:
    rec = tmp_path / "r"
    store = _record(full, rec)
    store.u[400, 1] *= 1.5  # an ETH training bar after the 252-day lookback, before the OOS start (593)
    out = predlog.replay(rec, store.load, PREREG)
    eth = set(_seqs(rec, "prediction", "ETH-EUR"))
    assert eth and all((seq, "PARAMS_DRIFT") in out for seq in eth)


def test_revised_outcome_bar_is_outcome_drift(full: Bars, tmp_path: Path) -> None:
    rec = tmp_path / "r"
    store = _record(full, rec)
    lines = read_trials(rec / PREDICTIONS)
    first = next(x for x in lines if x["kind"] == "prediction" and x["symbol"] == "BTC-EUR")
    outcome = next(x for x in lines if x["kind"] == "outcome" and x["prediction_seq"] == first["seq"])
    store.u[600 + predlog.OUT_DAYS, 0] *= 1.2  # the outcome bar of that prediction
    assert (int(outcome["seq"]), "OUTCOME_DRIFT") in predlog.replay(rec, store.load, PREREG)


def test_a_revised_bar_after_as_of_never_touches_earlier_lines(full: Bars, tmp_path: Path) -> None:
    rec = tmp_path / "r"
    store = _record(full, rec)
    store.u[605, :] *= 1.3  # after the 600, 601 and 603 decisions and not an outcome bar of theirs
    lines = read_trials(rec / PREDICTIONS)
    late = {int(x["seq"]) for x in lines if x.get("as_of_ts", 0) >= int(full.ts[605])}
    out = predlog.replay(rec, store.load, PREREG)
    assert out and {seq for seq, _ in out} <= late


def test_changed_preregistration_is_refused_per_prediction(full: Bars, tmp_path: Path) -> None:
    rec = tmp_path / "r"
    store = _record(full, rec)
    out = predlog.replay(rec, store.load, "cd" * 32)
    assert {seq for seq, r in out if r == "PREREG_MISMATCH"} == set(_seqs(rec, "prediction"))


def test_sample_replays_only_the_given_lines(full: Bars, tmp_path: Path) -> None:
    rec = tmp_path / "r"
    store = _record(full, rec)
    calls: list[datetime] = []

    def load(at: datetime) -> Bars:
        calls.append(at)
        return store.load(at)

    first = _seqs(rec, "prediction")[0]
    assert predlog.replay(rec, load, "cd" * 32, seqs={first}) == [(first, "PREREG_MISMATCH")]
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("change", "reasons"),
    [
        ({"decision": {"state": "x"}}, ["LOGIC_DRIFT"]),
        ({"kind": "no_decision"}, ["DECISION_DRIFT"]),
        ({"input_sha256": "0"}, ["INPUT_DRIFT"]),
        ({"input_sha256": "0", "decision": {"state": "x"}}, ["INPUT_DRIFT", "DECISION_DRIFT"]),
        ({"params_sha256": "0", "decision": {"state": "x"}}, ["PARAMS_DRIFT", "DECISION_DRIFT"]),
        ({"versions": {}}, ["LOGIC_DRIFT"]),
        ({}, []),
    ],
)
def test_drift_reasons(change: dict[str, Any], reasons: list[str]) -> None:
    want = {"kind": "prediction", "input_sha256": "a", "params_sha256": "b", "decision": {"state": "long"},
            "versions": {"predlog": "1"}}
    assert predlog._drift(want, want | change) == reasons


def test_public_file_alone_checks_chain_seq_and_time(full: Bars, tmp_path: Path) -> None:
    rec = tmp_path / "r"
    _record(full, rec)
    public = rec / COMMITMENTS
    assert predlog.verify_public(public) == len(read_trials(rec / PREDICTIONS))
    rows = public.read_text().splitlines()
    public.write_text("\n".join([rows[0], rows[2], rows[1], *rows[3:]]) + "\n")
    with pytest.raises(ChainError):
        predlog.verify_public(public)


def test_public_check_refuses_a_salt(tmp_path: Path) -> None:
    from signal_engine.validation.trials import log_trial

    path = tmp_path / COMMITMENTS
    log_trial(path, {"id": "x", "seq": 1, "committed_at": "2026-10-24T00:00:00.000Z", "salt": "00"})
    with pytest.raises(ChainError, match="salt"):
        predlog.verify_public(path)


def test_public_check_of_an_empty_or_missing_file(tmp_path: Path) -> None:
    assert predlog.verify_public(tmp_path / "none.jsonl") == 0


def test_cli_exit_1_on_a_broken_record_and_3_without_bars(full: Bars, tmp_path: Path) -> None:
    from signal_engine.__main__ import main

    rec = tmp_path / "record"
    _record(full, rec)
    if not pit.PREDLOG_SHA256_FILE.exists():
        pytest.skip("the predlog pre-registration hash file is not in this checkout")
    assert main(["verify-record", "--data-dir", str(tmp_path)]) == 3  # a valid record, no raw bars
    rows = (rec / PREDICTIONS).read_text().splitlines()
    rows[0] = rows[0].replace('"kind": "prediction"', '"kind": "predictiom"')
    (rec / PREDICTIONS).write_text("\n".join(rows) + "\n")
    assert main(["verify-record", "--data-dir", str(tmp_path)]) == 1
    assert main(["verify-record", "--public", str(rec / COMMITMENTS)]) == 0


@pytest.mark.parametrize("bad", ["0", "-1"])
def test_cli_refuses_a_sample_below_one(bad: str, tmp_path: Path) -> None:
    from signal_engine.__main__ import main

    with pytest.raises(SystemExit) as err:
        main(["verify-record", "--data-dir", str(tmp_path), "--sample", bad])
    assert err.value.code == 2


def _public(path: Path, rows: list[dict[str, Any]]) -> None:
    from signal_engine.validation.trials import log_trial

    for row in rows:
        log_trial(path, row)


@pytest.mark.parametrize(
    ("rows", "why"),
    [
        ([{"id": "a", "seq": 1, "committed_at": "2026-10-24T00:00:00.000Z"},
          {"id": "b", "seq": 3, "committed_at": "2026-10-25T00:00:00.000Z"}], "contiguous"),
        ([{"id": "a", "seq": 1, "committed_at": "2026-10-25T00:00:00.000Z"},
          {"id": "b", "seq": 2, "committed_at": "2026-10-24T00:00:00.000Z"}], "backwards"),
    ],
)
def test_public_check_on_an_intact_chain_still_refuses_a_seq_jump_or_time_going_back(
    rows: list[dict[str, Any]], why: str, tmp_path: Path
) -> None:
    path = tmp_path / COMMITMENTS
    _public(path, rows)
    with pytest.raises(ChainError, match=why):
        predlog.verify_public(path)


def test_outcome_is_due_exactly_one_day_after_its_outcome_bar(full: Bars) -> None:
    index = predlog.bar_index(full)
    a = int(full.ts[600])
    pred = {"id": "prediction:BTC-EUR:x", "symbol": "BTC-EUR", "seq": 1, "as_of_ts": a}
    due = a + (predlog.OUT_DAYS + 1) * DAY_MS
    assert predlog.outcome_body(full, index, 0, pred, due - 1) is None
    assert predlog.outcome_body(full, index, 0, pred, due) is not None


def test_a_due_outcome_without_its_line_is_outcome_missing(full: Bars, tmp_path: Path) -> None:
    rec = tmp_path / "r"
    store = _record(full, rec)
    lines = read_trials(rec / PREDICTIONS)
    gone = next(x for x in lines if x["kind"] == "outcome")
    kept = [x for x in lines if x is not gone]
    (rec / PREDICTIONS).write_text("".join(json.dumps(x, sort_keys=True) + "\n" for x in kept))
    out = predlog.replay(rec, store.load, PREREG)
    assert (int(gone["prediction_seq"]), "OUTCOME_MISSING") in out


class _FakeSource:
    """Stands in for RealBarSource in the CLI: records the keywords and serves the test store."""

    seen: list[dict[str, Any]] = []
    store: Store

    def __init__(self, root: Path, prereg: str, now: datetime, **kw: Any) -> None:
        _FakeSource.seen.append({"now": now, **kw})
        self.at = kw["known_at"]

    def load(self) -> Bars:
        return _FakeSource.store.load(self.at)


def test_cli_rebuilds_with_known_at_and_reports_a_flipped_line(
    full: Bars, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from signal_engine import __main__ as cli

    _FakeSource.store, _FakeSource.seen = _record(full, tmp_path / "record"), []
    monkeypatch.setattr(cli, "RealBarSource", _FakeSource)
    monkeypatch.setattr(pit, "read_prereg_hash", lambda _path: PREREG)
    assert cli.main(["verify-record", "--data-dir", str(tmp_path)]) == 0
    assert _FakeSource.seen and all(s["known_at"] == s["now"] for s in _FakeSource.seen)
    _FakeSource.store.ingest[613] = int(_now(full, 613).timestamp() * 1000) - 1  # known before the stale run
    assert cli.main(["verify-record", "--data-dir", str(tmp_path)]) == 2


def test_cli_sample_rebuilds_exactly_that_many_lines(
    full: Bars, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from signal_engine import __main__ as cli

    _FakeSource.store = _record(full, tmp_path / "record")
    monkeypatch.setattr(cli, "RealBarSource", _FakeSource)
    monkeypatch.setattr(pit, "read_prereg_hash", lambda _path: PREREG)
    asked: list[set[int] | None] = []
    real = predlog.replay
    def spy(*a: Any) -> list[tuple[int, str]]:
        asked.append(a[3])
        return real(*a)

    monkeypatch.setattr(predlog, "replay", spy)
    assert cli.main(["verify-record", "--data-dir", str(tmp_path), "--sample", "1", "--seed", "0"]) == 0
    assert asked and asked[0] is not None and len(asked[0]) == 1
