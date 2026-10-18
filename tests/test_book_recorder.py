import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import ccxt
import pyarrow as pa
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
