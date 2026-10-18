import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import ccxt
import pyarrow as pa
import pytest

from signal_engine.__main__ import main
from signal_engine.data import bitvavo
from signal_engine.data.bitvavo import DAY_MS, fetch_daily, store_daily
from signal_engine.storage import read_partition, write_partition

D0 = datetime(2026, 9, 28, tzinfo=UTC)
D0_MS = int(D0.timestamp() * 1000)


def _day(i: int) -> int:
    return D0_MS + i * DAY_MS


def _bar(i: int, close: float = 100.0) -> list[Any]:
    return [_day(i), close - 1, close + 2, close - 3, close, 12.5]


def _now(days_after_d0: float) -> datetime:
    return D0 + timedelta(days=days_after_d0)


class FakeExchange:
    """Answers like Bitvavo: bars in the window [since, since + limit days), in the given order."""

    def __init__(self, bars: list[list[Any]], errors: list[Exception] | None = None) -> None:
        self.bars = bars
        self.errors = list(errors or [])
        self.calls: list[tuple[str, str, int | None, int | None]] = []

    def fetch_ohlcv(self, symbol: str, timeframe: str, since: int | None, limit: int | None) -> Any:
        self.calls.append((symbol, timeframe, since, limit))
        if self.errors:
            raise self.errors.pop(0)
        assert since is not None and limit is not None
        return [b for b in self.bars if since <= b[0] < since + limit * DAY_MS]


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    recorded: list[float] = []
    monkeypatch.setattr(bitvavo, "_sleep", recorded.append)
    return recorded


def test_unfinished_last_bar_dropped(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    fake = FakeExchange([_bar(0), _bar(1), _bar(2), _bar(3)])
    table, _ = fetch_daily("BTC/EUR", D0, exchange=fake, now=_now(3.5))
    assert table.column("ts").to_pylist() == [_day(0), _day(1), _day(2)]
    assert any(r.message == "dropped unfinished bar" for r in caplog.records)


def test_bar_closed_exactly_at_now_is_kept() -> None:
    table, _ = fetch_daily("BTC/EUR", D0, exchange=FakeExchange([_bar(0), _bar(1)]), now=_now(2))
    assert table.num_rows == 2


def test_duplicates_removed_and_sorted() -> None:
    fake = FakeExchange([_bar(2), _bar(0), _bar(1), _bar(0)])
    table, gaps = fetch_daily("BTC/EUR", D0, exchange=fake, now=_now(5))
    assert table.column("ts").to_pylist() == [_day(0), _day(1), _day(2)]
    assert table.schema == bitvavo.SCHEMA
    assert gaps == []


def test_gap_reported_not_filled(caplog: pytest.LogCaptureFixture) -> None:
    fake = FakeExchange([_bar(0), _bar(1), _bar(4)])
    table, gaps = fetch_daily("BTC/EUR", D0, exchange=fake, now=_now(6))
    assert gaps == [_day(2), _day(3)]
    assert table.num_rows == 3
    rec = next(r for r in caplog.records if r.message == "candle gaps")
    assert rec.levelno == logging.WARNING and rec.__dict__["gaps"] == ["2026-09-30", "2026-10-01"]


def test_naive_since_refused() -> None:
    with pytest.raises(ValueError, match="UTC"):
        fetch_daily("BTC/EUR", datetime(2026, 9, 28), exchange=FakeExchange([]))


def test_paginates_and_skips_empty_window_before_listing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bitvavo, "PAGE_LIMIT", 2)
    fake = FakeExchange([_bar(i) for i in range(5)])
    table, _ = fetch_daily("BTC/EUR", D0 - timedelta(days=3), exchange=fake, now=_now(5.5))
    assert table.column("ts").to_pylist() == [_day(i) for i in range(5)]
    assert len(fake.calls) > 3  # one empty window before listing, then several pages


def test_default_exchange_is_rate_limited_bitvavo(monkeypatch: pytest.MonkeyPatch) -> None:
    configs: list[dict[str, Any]] = []
    fake = FakeExchange([_bar(0)])

    def factory(config: dict[str, Any]) -> FakeExchange:
        configs.append(config)
        return fake

    monkeypatch.setattr(ccxt, "bitvavo", factory)
    table, _ = fetch_daily("BTC/EUR", D0, now=_now(1))
    assert configs == [{"enableRateLimit": True}] and table.num_rows == 1
    assert fake.calls[0][:2] == ("BTC/EUR", "1d")


def test_network_error_retried_then_raised(sleeps: list[float]) -> None:
    fake = FakeExchange([], errors=[ccxt.NetworkError("down")] * 4)
    with pytest.raises(ccxt.NetworkError):
        fetch_daily("BTC/EUR", D0, exchange=fake, now=_now(1))
    assert len(fake.calls) == 4  # first attempt + 3 retries
    assert sleeps == [1.0, 2.0, 4.0]


def test_network_error_recovers_on_retry(sleeps: list[float]) -> None:
    fake = FakeExchange([_bar(0)], errors=[ccxt.RequestTimeout("slow")])
    table, _ = fetch_daily("BTC/EUR", D0, exchange=fake, now=_now(1))
    assert table.num_rows == 1 and sleeps == [1.0]


def test_non_transient_error_raised_immediately(sleeps: list[float]) -> None:
    fake = FakeExchange([], errors=[ccxt.BadSymbol("no such market")])
    with pytest.raises(ccxt.BadSymbol):
        fetch_daily("NOPE/EUR", D0, exchange=fake, now=_now(1))
    assert len(fake.calls) == 1 and sleeps == []


def test_empty_response_writes_nothing(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    table, gaps = fetch_daily("BTC/EUR", D0, exchange=FakeExchange([]), now=_now(1))
    assert table.num_rows == 0 and gaps == []
    assert store_daily(table, tmp_path, "BTC/EUR") == []
    assert not any(tmp_path.iterdir())
    assert any(r.message == "no candles, nothing written" for r in caplog.records)


def test_rerun_idempotent_keeps_stored_bars(tmp_path: Path) -> None:
    # D0 = 2026-09-28: bars 0-2 are September, bars 3-5 October
    first, _ = fetch_daily("BTC/EUR", D0, exchange=FakeExchange([_bar(i) for i in range(4)]), now=_now(9))
    second, _ = fetch_daily(
        "BTC/EUR", D0, exchange=FakeExchange([_bar(i) for i in range(1, 6)]), now=_now(10)
    )
    store_daily(first, tmp_path, "BTC/EUR")
    paths = store_daily(second, tmp_path, "BTC/EUR")
    store_daily(second, tmp_path, "BTC/EUR")
    base = tmp_path / "raw" / "bitvavo" / "BTC-EUR"
    assert paths == [base / "2026" / "09" / "part-0.parquet", base / "2026" / "10" / "part-0.parquet"]
    sep, octo = (read_partition(p) for p in paths)
    assert sep.column("ts").to_pylist() == [_day(0), _day(1), _day(2)]
    assert octo.column("ts").to_pylist() == [_day(3), _day(4), _day(5)]
    # bars 0-3 keep the first ingest time, bars 4-5 were first seen by the second run
    assert sep.column("first_available_time").to_pylist() == [int(_now(9).timestamp() * 1000)] * 3
    assert (
        octo.column("first_available_time").to_pylist()
        == [int(_now(9).timestamp() * 1000)] + [int(_now(10).timestamp() * 1000)] * 2
    )
    assert not (base / "revisions").exists()  # identical values are no revision


def test_refetch_with_changed_values_keeps_stored_row_and_records_revision(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    first, _ = fetch_daily("BTC/EUR", D0, exchange=FakeExchange([_bar(0), _bar(1)]), now=_now(3))
    store_daily(first, tmp_path, "BTC/EUR")
    stored = read_partition(tmp_path / "raw" / "bitvavo" / "BTC-EUR" / "2026" / "09" / "part-0.parquet")
    revised, _ = fetch_daily("BTC/EUR", D0, exchange=FakeExchange([_bar(0), _bar(1, 200.0)]), now=_now(4))
    [path] = store_daily(revised, tmp_path, "BTC/EUR")
    store_daily(revised, tmp_path, "BTC/EUR")  # the same revision seen again is recorded once
    assert read_partition(path).equals(stored)  # the whole stored row, ingest times included
    rev_path = tmp_path / "raw" / "bitvavo" / "BTC-EUR" / "revisions" / "2026" / "09" / "part-0.parquet"
    rev = read_partition(rev_path).to_pylist()
    now4 = int(_now(4).timestamp() * 1000)
    assert rev == [
        {
            "ts": _day(1),
            "open": 199.0,
            "high": 202.0,
            "low": 197.0,
            "close": 200.0,
            "volume": 12.5,
            "close_time": _day(2),
            "first_available_time": now4,
            "ingested_at": now4,
            "source": "bitvavo",
        }
    ]
    recs = [r for r in caplog.records if r.message == "bar_revision"]
    assert len(recs) == 2 and recs[0].levelno == logging.WARNING
    assert recs[0].__dict__["bar"] == "2026-09-29"
    assert "close" in recs[0].__dict__["fields"] and "old" not in recs[0].__dict__


def test_new_columns_types_and_values() -> None:
    table, _ = fetch_daily("BTC/EUR", D0, exchange=FakeExchange([_bar(0), _bar(1)]), now=_now(2.5))
    now_ms = int(_now(2.5).timestamp() * 1000)
    assert table.schema.field("close_time").type == pa.int64()
    assert table.schema.field("first_available_time").type == pa.int64()
    assert table.schema.field("ingested_at").type == pa.int64()
    assert table.schema.field("source").type == pa.string()
    assert table.column("close_time").to_pylist() == [_day(1), _day(2)]
    assert table.column("first_available_time").to_pylist() == [now_ms, now_ms]
    assert table.column("ingested_at").to_pylist() == [now_ms, now_ms]
    assert table.column("source").to_pylist() == ["bitvavo", "bitvavo"]


def test_legacy_partition_without_new_columns_is_upgraded(tmp_path: Path) -> None:
    legacy = pa.table(
        {name: [_day(0)] if name == "ts" else [100.0] for name in bitvavo.OHLCV},
        schema=pa.schema([bitvavo.SCHEMA.field(n) for n in bitvavo.OHLCV]),
    )
    write_partition(legacy, tmp_path, "bitvavo", "BTC-EUR", D0)
    table, _ = fetch_daily("BTC/EUR", D0, exchange=FakeExchange([_bar(1)]), now=_now(2))
    [path] = store_daily(table, tmp_path, "BTC/EUR")
    rows = read_partition(path).to_pylist()
    assert [r["ts"] for r in rows] == [_day(0), _day(1)]
    assert rows[0]["close_time"] == _day(1) and rows[0]["source"] == "bitvavo"
    assert rows[0]["first_available_time"] is None and rows[0]["ingested_at"] is None  # unknown, not invented
    assert rows[1]["first_available_time"] == int(_now(2).timestamp() * 1000)


def test_cli_fetch_candles_calls_fetcher(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, datetime]] = []

    def fake_fetch(symbol: str, since: datetime) -> tuple[pa.Table, list[int]]:
        calls.append((symbol, since))
        return bitvavo._to_table([_bar(0)], D0_MS + DAY_MS), []

    monkeypatch.setattr("signal_engine.__main__.fetch_daily", fake_fetch)
    argv = ["fetch-candles", "--symbol", "BTC/EUR", "--since", "2020-01-01", "--data-dir", str(tmp_path)]
    assert main(argv) == 0
    assert calls == [("BTC/EUR", datetime(2020, 1, 1, tzinfo=UTC))]
    assert (tmp_path / "raw" / "bitvavo" / "BTC-EUR" / "2026" / "09" / "part-0.parquet").exists()


def test_cli_fetch_candles_requires_symbol() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["fetch-candles", "--since", "2020-01-01"])
    assert exc.value.code == 2
