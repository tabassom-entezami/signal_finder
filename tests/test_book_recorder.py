import json
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import ccxt
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from signal_engine.__main__ import main
from signal_engine.data import book_recorder
from signal_engine.data.book_recorder import record_books
from signal_engine.storage import read_partition

START = datetime(2026, 10, 18, 2, 30, tzinfo=UTC)
START_MS = int(START.timestamp() * 1000)


class FakeClock:
    """Wall clock that only moves when the code sleeps (or a fetch takes time)."""

    def __init__(self, t: datetime, overshoot_s: float = 0.0) -> None:
        self.t = t
        self.overshoot_s = overshoot_s  # the OS wakes a sleeper a little late
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += timedelta(seconds=seconds + self.overshoot_s)


class FakeExchange:
    """Bitvavo-shaped answers; `book_errors[(call_no, symbol)]` raises on that book call."""

    def __init__(
        self,
        clock: FakeClock,
        book_errors: dict[tuple[int, str], Exception] | None = None,
        status_error: Exception | None = None,
        fetch_s: float = 0.0,
    ) -> None:
        self.clock = clock
        self.book_errors = book_errors or {}
        self.status_error = status_error
        self.fetch_s = fetch_s
        self.book_calls: list[tuple[str, int | None]] = []
        self.status_calls = 0

    def fetch_markets(self) -> Any:
        self.status_calls += 1
        if self.status_error:
            raise self.status_error
        return [
            {"symbol": "BTC/EUR", "info": {"market": "BTC-EUR", "status": "halted"}},
            {"symbol": "ETH/USDC", "info": {"market": "ETH-USDC", "status": "trading"}},
        ]

    def fetch_order_book(self, symbol: str, limit: int | None = None) -> Any:
        self.book_calls.append((symbol, limit))
        self.clock.t += timedelta(seconds=self.fetch_s)
        err = self.book_errors.get((len(self.book_calls), symbol))
        if err:
            raise err
        levels = [[60000.0 + i, 0.5] for i in range(30)]
        return {"bids": levels, "asks": [[61000, "0.25"]], "nonce": 42, "timestamp": None}


def _rows(root: Path) -> list[dict[str, Any]]:
    files = sorted((root / "raw" / "bitvavo_books").rglob("*.parquet"))
    return [r for f in files for r in read_partition(f).to_pylist()]


def _run(tmp_path: Path, clock: FakeClock, fake: FakeExchange, seconds: float = 15) -> dict[str, int]:
    return record_books(
        ["BTC/EUR", "ETH/USDC"],
        5,
        START,
        START + timedelta(seconds=seconds),
        tmp_path,
        exchange=fake,
        clock=clock.now,
        sleep=clock.sleep,
    )


def test_waits_until_start_in_chunks(tmp_path: Path) -> None:
    clock = FakeClock(START - timedelta(seconds=150))
    fake = FakeExchange(clock)
    _run(tmp_path, clock, fake)
    assert clock.sleeps[:3] == [60.0, 60.0, 30.0]  # wall clock re-read at least every minute
    assert _rows(tmp_path)[0]["tick"] == START_MS  # no snapshot before start


def test_records_n_snapshots_on_grid_and_stops_at_end(tmp_path: Path) -> None:
    clock = FakeClock(START)
    fake = FakeExchange(clock)
    counts = _run(tmp_path, clock, fake)
    assert counts == {"ticks": 3, "rows": 6, "errors": 0, "skipped": 0}
    assert fake.status_calls == 3 and fake.book_calls[0] == ("BTC/EUR", 25)
    rows = _rows(tmp_path)
    assert [r["tick"] for r in rows] == [START_MS + k * 5000 for k in (0, 0, 1, 1, 2, 2)]
    assert len(rows[0]["bids"]) == 25 and rows[0]["bids"][0] == [60000.0, 0.5]
    assert rows[0]["asks"] == [[61000.0, 0.25]]  # strings and ints converted to float
    assert rows[0]["nonce"] == 42 and rows[0]["error"] is None
    assert [r["status"] for r in rows[:2]] == ["halted", "trading"]
    folder = tmp_path / "raw" / "bitvavo_books" / "2026" / "10" / "18" / "02"
    assert sorted(p.name for p in folder.iterdir()) == [
        f"part-{START_MS + k * 5000}.parquet" for k in range(3)
    ]


def test_failed_fetch_records_error_row_and_continues(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    clock = FakeClock(START)
    fake = FakeExchange(clock, book_errors={(1, "BTC/EUR"): ccxt.ExchangeNotAvailable("maintenance")})
    counts = _run(tmp_path, clock, fake)
    rows = _rows(tmp_path)
    assert counts["ticks"] == 3 and counts["rows"] == 6 and counts["errors"] == 1
    assert rows[0]["error"] == "book: ExchangeNotAvailable('maintenance')"
    assert rows[0]["bids"] is None and rows[0]["status"] == "halted"
    assert all(r["error"] is None for r in rows[1:])
    assert any(r.message == "book snapshot errors" and r.levelno == logging.WARNING for r in caplog.records)


def test_failed_status_and_malformed_book_are_rows_not_crashes(tmp_path: Path) -> None:
    clock = FakeClock(START)
    fake = FakeExchange(clock, status_error=ccxt.NetworkError("down"))
    fake.fetch_order_book = lambda symbol, limit=None: {"bids": None, "asks": []}  # type: ignore[method-assign]
    counts = _run(tmp_path, clock, fake, seconds=5)
    [row, _] = _rows(tmp_path)
    assert counts["errors"] == 2
    assert row["status"] is None and row["error"].startswith("status: NetworkError('down'); book: TypeError(")


def test_slow_round_skips_missed_ticks(tmp_path: Path) -> None:
    clock = FakeClock(START)
    fake = FakeExchange(clock, fetch_s=6.0)  # one round of 2 books takes 12 s > 5 s interval
    counts = _run(tmp_path, clock, fake, seconds=30)
    ticks = sorted({r["tick"] for r in _rows(tmp_path)})
    assert ticks == [START_MS, START_MS + 15000]  # stays on the 5 s grid, no back-to-back catch-up
    assert counts["skipped"] == 4 and counts["ticks"] == 2


def test_sleep_overshoot_keeps_the_slot(tmp_path: Path) -> None:
    clock = FakeClock(START - timedelta(seconds=2), overshoot_s=0.003)
    counts = _run(tmp_path, clock, FakeExchange(clock))
    assert sorted({r["tick"] for r in _rows(tmp_path)}) == [START_MS + k * 5000 for k in range(3)]
    assert counts["skipped"] == 0


def test_started_after_end_records_nothing(tmp_path: Path) -> None:
    clock = FakeClock(START + timedelta(seconds=20))
    counts = _run(tmp_path, clock, FakeExchange(clock))
    assert counts["ticks"] == 0 and _rows(tmp_path) == []


def test_bad_window_and_naive_times_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="UTC"):
        record_books(["BTC/EUR"], 5, datetime(2026, 10, 18), START, tmp_path)
    with pytest.raises(ValueError, match="end > start"):
        record_books(["BTC/EUR"], 5, START, START, tmp_path)


def test_default_exchange_has_short_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    configs: list[dict[str, Any]] = []
    clock = FakeClock(START)

    def factory(config: dict[str, Any]) -> FakeExchange:
        configs.append(config)
        return FakeExchange(clock)

    monkeypatch.setattr(ccxt, "bitvavo", factory)
    record_books(
        ["BTC/EUR"], 5, START, START + timedelta(seconds=5), tmp_path, clock=clock.now, sleep=clock.sleep
    )
    assert configs == [{"enableRateLimit": True, "timeout": 3000}]


def test_schema_types() -> None:
    assert book_recorder.SCHEMA.field("bids").type == pa.list_(pa.list_(pa.float64()))
    assert book_recorder.SCHEMA.field("ts").type == pa.int64()


def test_cli_record_books_parses_args(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr("signal_engine.__main__.record_books", lambda *a: calls.append(a))
    argv = [
        "record-books",
        "--symbols",
        "BTC/EUR,ETH/USDC",
        "--interval",
        "5",
        "--start",
        "2026-10-18T02:30:00Z",
        "--end",
        "2026-10-18T06:00:00Z",
        "--data-dir",
        str(tmp_path),
    ]
    assert main(argv) == 0
    assert calls == [(["BTC/EUR", "ETH/USDC"], 5.0, START, datetime(2026, 10, 18, 6, tzinfo=UTC), tmp_path)]


def test_cli_record_books_without_end_records_until_stopped_and_writes_a_pid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Any, ...]] = []
    seen: list[str] = []

    def fake(*a: Any) -> None:
        calls.append(a)
        seen.append((tmp_path / "ops/record-books.pid").read_text().strip())

    monkeypatch.setattr("signal_engine.__main__.record_books", fake)
    argv = ["record-books", "--symbols", "BTC/EUR", "--start", "2026-10-18T02:30:00Z",
            "--data-dir", str(tmp_path)]
    assert main(argv) == 0
    assert calls[0][3] is None and seen[0].isdigit()


def test_without_end_the_recorder_resumes_after_a_sleep_and_logs_the_gap(tmp_path: Path) -> None:
    clock = FakeClock(START)
    fake = FakeExchange(clock)

    def stop_after_wake() -> datetime:  # 10 min asleep after the first tick, then stop the run
        if len(fake.book_calls) >= 2 and clock.t < START + timedelta(minutes=10):
            clock.t = START + timedelta(minutes=10)
        if clock.t > START + timedelta(minutes=10, seconds=12):
            raise KeyboardInterrupt
        return clock.t

    with pytest.raises(KeyboardInterrupt):
        record_books(["BTC/EUR", "ETH/USDC"], 5, START, None, tmp_path, exchange=fake, clock=stop_after_wake,
                     sleep=clock.sleep)
    gaps = [json.loads(line) for line in (tmp_path / "ops/book-gaps.jsonl").read_text().splitlines()]
    assert len(gaps) == 1 and gaps[0]["missed_slots"] >= 100
    assert any(r["tick"] >= START_MS + 600_000 for r in _rows(tmp_path))  # recording resumed after the wake


HOUR = START.replace(minute=0)  # 02:00, the hour the fixtures record in
HOUR_DIR = ("raw", "bitvavo_books", "2026", "10", "18", "02")


def _record(tmp_path: Path, start: datetime, seconds: float, fake: FakeExchange | None = None) -> Path:
    clock = FakeClock(start)
    record_books(["BTC/EUR", "ETH/USDC"], 5, start, start + timedelta(seconds=seconds), tmp_path,
                 exchange=fake or FakeExchange(clock), clock=clock.now, sleep=clock.sleep)
    return tmp_path.joinpath(*HOUR_DIR)


def test_compact_hour_keeps_every_row_including_error_rows(tmp_path: Path) -> None:
    clock = FakeClock(START)
    fake = FakeExchange(clock, book_errors={(2, "ETH/USDC"): ccxt.NetworkError("timeout")})
    record_books(["BTC/EUR", "ETH/USDC"], 5, START, START + timedelta(seconds=15), tmp_path,
                 exchange=fake, clock=clock.now, sleep=clock.sleep)
    folder = tmp_path.joinpath(*HOUR_DIR)
    before = _rows(tmp_path)
    path = book_recorder.compact_hour(folder)
    assert path == folder / "hour-2026101802.parquet"
    assert sorted(p.name for p in folder.iterdir()) == ["hour-2026101802.parquet"]
    assert _rows(tmp_path) == before  # same rows, same order (tick, symbol)
    assert sum(r["error"] is not None for r in _rows(tmp_path)) == 1
    assert pq.ParquetFile(path).metadata.row_group(0).column(0).compression == "ZSTD"
    assert book_recorder.compact_hour(folder) is None  # nothing left to join


def test_rerun_after_a_stop_between_rename_and_removals_adds_no_row_twice(tmp_path: Path) -> None:
    folder = _record(tmp_path, START, 10)
    kept = {p.name: p.read_bytes() for p in folder.glob("part-*.parquet")}
    book_recorder.compact_hour(folder)
    for name, data in kept.items():  # the removals never happened
        (folder / name).write_bytes(data)
    _record(tmp_path, START + timedelta(seconds=10), 5)  # one more tick lands in the same hour
    book_recorder.compact_hour(folder)
    rows = _rows(tmp_path)
    assert [r["tick"] for r in rows] == [START_MS + k * 5000 for k in (0, 0, 1, 1, 2, 2)]
    assert sorted(p.name for p in folder.iterdir()) == ["hour-2026101802.parquet"]


def test_compact_books_skips_unfinished_hours_and_non_date_folders(tmp_path: Path) -> None:
    folder = _record(tmp_path, START, 10)
    odd = folder.parent / "09x"
    odd.mkdir()
    (odd / "part-1.parquet").write_bytes(next(folder.glob("part-*.parquet")).read_bytes())
    late = HOUR + timedelta(hours=1, seconds=30)  # a running writer may still add a tick
    assert book_recorder.compact_books(tmp_path, late) == []
    assert book_recorder.compact_books(tmp_path, HOUR + timedelta(hours=1, minutes=1)) == [
        folder / "hour-2026101802.parquet"
    ]
    assert (odd / "part-1.parquet").exists()


def test_a_read_back_that_differs_keeps_the_tick_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = _record(tmp_path, START, 10)
    real = pq.read_table

    def lossy(path: Any, **kw: Any) -> pa.Table:
        table = real(path, **kw)
        return table.slice(1) if str(path).endswith(".tmp") else table

    monkeypatch.setattr(pq, "read_table", lossy)
    with pytest.raises(RuntimeError, match="differs"):
        book_recorder.compact_hour(folder)
    assert sorted(p.name for p in folder.iterdir()) == [f"part-{START_MS + k * 5000}.parquet" for k in (0, 1)]


def test_duplicate_tick_and_symbol_rows_are_refused(tmp_path: Path) -> None:
    folder = _record(tmp_path, START, 5)
    (folder / f"part-{START_MS + 1}.parquet").write_bytes((folder / f"part-{START_MS}.parquet").read_bytes())
    with pytest.raises(RuntimeError, match="duplicate"):
        book_recorder.compact_hour(folder)
    assert len(list(folder.glob("part-*.parquet"))) == 2 and not list(folder.glob("hour-*"))


def test_recorder_joins_the_hour_it_leaves_and_keeps_the_current_one(tmp_path: Path) -> None:
    start = HOUR + timedelta(minutes=59, seconds=50)
    _record(tmp_path, start, 20)
    past, current = tmp_path.joinpath(*HOUR_DIR), tmp_path.joinpath(*HOUR_DIR[:-1], "03")
    assert sorted(p.name for p in past.iterdir()) == ["hour-2026101802.parquet"]
    assert len(list(current.glob("part-*.parquet"))) == 2
    assert len(_rows(tmp_path)) == 8  # 4 ticks x 2 symbols, none lost


def test_a_failed_join_never_stops_recording(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(folder: Path) -> Path:
        raise OSError("disk full")

    monkeypatch.setattr(book_recorder, "compact_hour", broken)
    start = HOUR + timedelta(minutes=59, seconds=55)
    _record(tmp_path, start, 15)
    assert len(list(tmp_path.joinpath(*HOUR_DIR).glob("part-*.parquet"))) == 1  # kept, not joined
    assert len(_rows(tmp_path)) == 6


def test_cli_compact_books_joins_finished_hours(tmp_path: Path) -> None:
    folder = _record(tmp_path, START, 10)
    assert main(["compact-books", "--data-dir", str(tmp_path)]) == 0
    assert sorted(p.name for p in folder.iterdir()) == ["hour-2026101802.parquet"]


@pytest.mark.parametrize("change", ["extra column", "missing column"])
def test_a_tick_file_with_another_schema_stops_the_join(tmp_path: Path, change: str) -> None:
    folder = _record(tmp_path, START, 10)
    first = folder / f"part-{START_MS}.parquet"
    table = pq.read_table(first)
    table = table.append_column("extra", pa.array([1] * table.num_rows)) if change == "extra column" \
        else table.drop_columns(["error"])
    pq.write_table(table, first)
    with pytest.raises(RuntimeError, match="another schema"):
        book_recorder.compact_hour(folder)
    assert len(list(folder.glob("part-*.parquet"))) == 2 and not list(folder.glob("hour-*"))


def test_a_corrupt_tick_file_stops_the_join_and_keeps_every_file(tmp_path: Path) -> None:
    folder = _record(tmp_path, START, 10)
    first = folder / f"part-{START_MS}.parquet"
    first.write_bytes(first.read_bytes()[:100])
    with pytest.raises(pa.ArrowInvalid):
        book_recorder.compact_hour(folder)
    assert len(list(folder.glob("part-*.parquet"))) == 2 and not list(folder.glob("hour-*"))


def test_only_old_temporary_files_are_cleared(tmp_path: Path) -> None:
    folder = _record(tmp_path, START, 10)
    old, live = folder / ".hour-2026101802-1.tmp", folder / ".hour-2026101802-2.tmp"
    old.write_bytes(b"x")
    live.write_bytes(b"x")
    os.utime(old, (0, 0))  # left by a run stopped long ago
    book_recorder.compact_hour(folder)
    assert not old.exists() and live.exists()  # another run's file in progress is never touched


def test_joined_rows_are_sorted_by_tick_then_symbol(tmp_path: Path) -> None:
    clock = FakeClock(START)
    record_books(["ETH/USDC", "BTC/EUR"], 5, START, START + timedelta(seconds=10), tmp_path,
                 exchange=FakeExchange(clock), clock=clock.now, sleep=clock.sleep)
    path = book_recorder.compact_hour(tmp_path.joinpath(*HOUR_DIR))
    assert path is not None
    rows = pq.read_table(path).select(["tick", "symbol"]).to_pylist()
    assert [(r["tick"] - START_MS, r["symbol"]) for r in rows] == [
        (0, "BTC/EUR"), (0, "ETH/USDC"), (5000, "BTC/EUR"), (5000, "ETH/USDC")]


def _gaps(root: Path) -> list[dict[str, Any]]:
    path = root / "ops/book-gaps.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def _restart(root: Path, now: datetime, seconds: float, start: datetime = START) -> FakeClock:
    """The launchd job restarts with the same fixed --start while `now` is later."""
    clock = FakeClock(now)
    record_books(["BTC/EUR", "ETH/USDC"], 5, start, now + timedelta(seconds=seconds), root,
                 exchange=FakeExchange(clock), clock=clock.now, sleep=clock.sleep)
    return clock


def test_a_restart_counts_only_its_own_downtime_as_a_gap(tmp_path: Path) -> None:
    _record(tmp_path, START, 15)  # ticks +0, +5, +10
    _restart(tmp_path, START + timedelta(minutes=10), 10)
    assert _gaps(tmp_path) == [{"from": (START + timedelta(seconds=15)).isoformat(),
                                "to": (START + timedelta(minutes=10)).isoformat(), "missed_slots": 117}]
    keys = [(r["tick"], r["symbol"]) for r in _rows(tmp_path)]
    assert len(keys) == len(set(keys)) and max(keys)[0] == START_MS + 605_000  # same grid, nothing twice


def test_a_restart_reads_the_last_tick_from_a_joined_hour_and_leftover_parts(tmp_path: Path) -> None:
    folder = _record(tmp_path, START, 15)
    kept = (folder / f"part-{START_MS + 10_000}.parquet").read_bytes()
    book_recorder.compact_hour(folder)
    (folder / f"part-{START_MS + 10_000}.parquet").write_bytes(kept)  # a stop between rename and removal
    (folder.parent / "05").mkdir()  # an empty newer folder is skipped
    assert book_recorder._last_tick_ms(tmp_path, START_MS) == START_MS + 10_000


def test_ticks_only_before_start_are_ignored(tmp_path: Path) -> None:
    _record(tmp_path, START, 10)
    later = START + timedelta(hours=1)
    _restart(tmp_path, later, 10, start=later)
    assert _gaps(tmp_path) == []
    assert min(r["tick"] for r in _rows(tmp_path) if r["tick"] >= int(later.timestamp() * 1000)) == int(
        later.timestamp() * 1000)


def test_an_off_grid_tick_resumes_on_the_next_grid_slot(tmp_path: Path) -> None:
    folder = _record(tmp_path, START, 5)
    first = folder / f"part-{START_MS}.parquet"
    (folder / f"part-{START_MS + 2_500}.parquet").write_bytes(first.read_bytes())
    resume = book_recorder._resume_tick(tmp_path, START, 5, lambda: START)
    assert resume == START + timedelta(seconds=5)


def test_a_tick_ahead_of_the_clock_is_never_written_again(tmp_path: Path) -> None:
    _record(tmp_path, START + timedelta(minutes=30), 5)  # a tick from a clock that ran ahead
    _restart(tmp_path, START, 30 * 60 + 10)
    keys = [(r["tick"], r["symbol"]) for r in _rows(tmp_path)]
    assert len(keys) == len(set(keys))  # the tick on disk was not written again
    assert _gaps(tmp_path) == [] and min(k[0] for k in keys) == START_MS + 1_800_000


def test_a_short_restart_under_the_gap_threshold_writes_no_row(tmp_path: Path) -> None:
    _record(tmp_path, START, 15)  # last tick +10
    _restart(tmp_path, START + timedelta(seconds=20), 5)  # one slot (+15) missed
    assert _gaps(tmp_path) == []


def test_a_fully_joined_newest_hour_still_gives_the_last_tick(tmp_path: Path) -> None:
    folder = _record(tmp_path, START, 15)
    book_recorder.compact_hour(folder)
    assert not list(folder.glob("part-*.parquet"))  # only the hour file is left
    assert book_recorder._last_tick_ms(tmp_path, START_MS) == START_MS + 10_000
