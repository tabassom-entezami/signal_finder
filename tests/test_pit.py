import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pytest

from signal_engine.data.bitvavo import SCHEMA
from signal_engine.data.pit import (
    DAY_MS,
    PREREG_SHA256_FILE,
    LoaderLockError,
    as_of,
    assert_grid,
    available_time,
    closed,
    is_locked_symbol,
    load,
    read_prereg_hash,
)
from signal_engine.storage import write_partition

PREREG_TEXT = b"frozen test rules file\n"
HASH = hashlib.sha256(PREREG_TEXT).hexdigest()
MINUTE = 60_000


def _ms(day: str) -> int:
    return int(datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp() * 1000)


def _bars(days: list[str], first_lag_ms: int | None = 5 * MINUTE) -> pa.Table:
    """Synthetic bars; first_available_time = close + first_lag_ms (None = unknown)."""
    rows = [
        {
            "ts": _ms(d),
            "open": 1.0,
            "high": 1.0,
            "low": 1.0,
            "close": float(i + 1),
            "volume": 1.0,
            "close_time": _ms(d) + DAY_MS,
            "first_available_time": None if first_lag_ms is None else _ms(d) + DAY_MS + first_lag_ms,
            "ingested_at": None,
            "source": "bitvavo",
        }
        for i, d in enumerate(days)
    ]
    return pa.Table.from_pylist(rows, schema=SCHEMA)


@pytest.fixture
def store(tmp_path: Path) -> Path:
    """Synthetic BTC-EUR and USDC-EUR partitions around the OOS start (no real prices)."""
    days = ["2019-08-15", "2019-08-16", "2019-08-17", "2019-08-18"]
    for symbol in ("BTC-EUR", "USDC-EUR"):
        write_partition(_bars(days), tmp_path, "bitvavo", symbol, datetime(2019, 8, 1, tzinfo=UTC))
    (tmp_path / "prereg.sha256").write_text(f"{HASH}  prereg.md\n")
    (tmp_path / "prereg.md").write_bytes(PREREG_TEXT)
    return tmp_path


def test_lock_refuses_oos_without_the_hash(store: Path) -> None:
    sha = store / "prereg.sha256"
    with pytest.raises(LoaderLockError, match="2019-08-17"):
        load(store, "bitvavo", "BTC-EUR", sha256_file=sha)
    with pytest.raises(LoaderLockError):
        load(store, "bitvavo", "BTC-EUR", prereg_hash="cd" * 32, purpose="m1-oos", sha256_file=sha)
    with pytest.raises(LoaderLockError):
        load(store, "bitvavo", "BTC-EUR", prereg_hash=HASH, purpose="research", sha256_file=sha)
    with pytest.raises(LoaderLockError):  # the first OOS bar alone is refused
        load(store, "bitvavo", "BTC-EUR", end=datetime(2019, 8, 18, tzinfo=UTC), sha256_file=sha)
    with pytest.raises(LoaderLockError):  # a missing hash file keeps the lock closed
        load(store, "bitvavo", "BTC-EUR", prereg_hash=HASH, purpose="m1-oos", sha256_file=store / "missing")


def test_lock_allows_oos_with_the_hash_and_purpose(store: Path) -> None:
    sha = store / "prereg.sha256"
    table = load(store, "bitvavo", "BTC-EUR", prereg_hash=HASH, purpose="m1-oos", sha256_file=sha)
    assert table.column("ts").to_pylist() == [
        _ms(d) for d in ("2019-08-15", "2019-08-16", "2019-08-17", "2019-08-18")
    ]


def test_warmup_range_and_unlocked_assets_load_without_hash(store: Path) -> None:
    sha = store / "prereg.sha256"
    table = load(store, "bitvavo", "BTC-EUR", end=datetime(2019, 8, 17, tzinfo=UTC), sha256_file=sha)
    assert table.column("ts").to_pylist() == [_ms("2019-08-15"), _ms("2019-08-16")]  # 08-16 allowed
    start = datetime(2019, 8, 16, tzinfo=UTC)
    assert load(store, "bitvavo", "USDC-EUR", start=start, sha256_file=sha).num_rows == 3


def test_locked_symbols_matched_on_base_asset() -> None:
    locked = ("BTC/EUR", "BTC-EUR", "BTCUSDT", "ETH-EUR", "eth/usdc", "SOL-EUR", "XRPUSDT", "WBTC-EUR")
    assert all(is_locked_symbol(s) for s in locked)  # altcoins are the next family's held-out OOS
    assert not any(is_locked_symbol(s) for s in ("USDC-EUR", "EUR-USD", "USDTEUR", "USDC/USDT"))


def test_load_refuses_naive_times_and_missing_store(store: Path) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        load(store, "bitvavo", "USDC-EUR", start=datetime(2019, 8, 1))
    with pytest.raises(FileNotFoundError):
        load(store, "bitvavo", "SOL-EUR", end=datetime(2019, 8, 1, tzinfo=UTC))  # locked after the OOS start


def test_default_hash_file_is_the_frozen_rules_file() -> None:
    md = PREREG_SHA256_FILE.with_suffix(".md")
    if not md.exists():
        pytest.skip("the frozen rules file is private (absent in the public copy)")
    assert read_prereg_hash() == hashlib.sha256(md.read_bytes()).hexdigest()


def test_live_bar_available_at_first_available_time_backfill_at_close_time() -> None:
    live = _bars(["2026-10-14"], first_lag_ms=5 * MINUTE)  # daily job, 5 min after the close
    backfill = _bars(["2019-03-08"], first_lag_ms=_ms("2026-10-16") - _ms("2019-03-09"))
    unknown = _bars(["2019-03-08"], first_lag_ms=None)  # legacy row
    close = _ms("2026-10-15")
    assert available_time(live) == [close + 5 * MINUTE]
    assert available_time(backfill) == [_ms("2019-03-09")]
    assert available_time(unknown) == [_ms("2019-03-09")]
    at_close = datetime(2026, 10, 15, tzinfo=UTC)
    assert as_of(live, at_close).num_rows == 0  # not yet ingested at the close
    assert as_of(live, at_close + timedelta(minutes=5)).num_rows == 1
    assert as_of(backfill, datetime(2019, 3, 9, tzinfo=UTC)).num_rows == 1  # history visible at its close
    assert as_of(backfill, datetime(2019, 3, 9, tzinfo=UTC), column="first_available_time").num_rows == 0
    assert as_of(unknown, datetime(2030, 1, 1, tzinfo=UTC), column="first_available_time").num_rows == 0
    assert as_of(backfill, datetime(2019, 3, 8, 23, 59, tzinfo=UTC)).num_rows == 0  # never before the close


def test_lag_boundary_one_day_is_still_live() -> None:
    assert available_time(_bars(["2026-10-14"], first_lag_ms=DAY_MS)) == [_ms("2026-10-16")]
    assert available_time(_bars(["2026-10-14"], first_lag_ms=DAY_MS + 1)) == [_ms("2026-10-15")]


def test_closed_and_assert_grid() -> None:
    table = _bars(["2026-10-14", "2026-10-15"])
    assert closed(table, datetime(2026, 10, 16, tzinfo=UTC)).num_rows == 2
    assert closed(table, datetime(2026, 10, 15, 23, 59, tzinfo=UTC)).num_rows == 1
    assert_grid(table)
    with pytest.raises(ValueError, match="off the grid"):
        assert_grid(table, offset_ms=3_600_000)


def test_an_edited_rules_text_keeps_the_lock_closed(store: Path) -> None:
    sha = store / "prereg.sha256"
    (store / "prereg.md").write_bytes(PREREG_TEXT + b"edited after the freeze\n")
    with pytest.raises(LoaderLockError):
        load(store, "bitvavo", "BTC-EUR", prereg_hash=HASH, purpose="m1-oos", sha256_file=sha)
    (store / "prereg.md").unlink()
    with pytest.raises(LoaderLockError):
        load(store, "bitvavo", "BTC-EUR", prereg_hash=HASH, purpose="m1-oos", sha256_file=sha)


def test_an_altcoin_is_locked_after_the_oos_start(store: Path) -> None:
    with pytest.raises(LoaderLockError, match="SOL-EUR"):
        load(store, "bitvavo", "SOL-EUR")


def test_the_m1_key_does_not_open_the_held_back_altcoins(store: Path) -> None:
    sha = store / "prereg.sha256"
    aug = datetime(2019, 8, 1, tzinfo=UTC)
    write_partition(_bars(["2019-08-16", "2019-08-17"]), store, "bitvavo", "SOL-EUR", aug)
    key = {"prereg_hash": HASH, "purpose": "m1-oos", "sha256_file": sha}
    assert load(store, "bitvavo", "BTC-EUR", **key).num_rows == 4  # type: ignore[arg-type]
    with pytest.raises(LoaderLockError, match="SOL-EUR"):
        load(store, "bitvavo", "SOL-EUR", prereg_hash=HASH, purpose="m1-oos", sha256_file=sha)


def test_an_ingest_before_the_close_falls_back_to_the_close() -> None:
    # Real sample: data/raw/ecb/EUR-USD/2026/10/part-0.parquet, 1 of 1,958 rows; the 2026-10-16 fix
    # was fetched at 14:34:17.230 UTC, 5,142,770 ms before its assumed 16:00 UTC close.
    early = _bars(["2026-10-14"], first_lag_ms=-5_142_770)
    assert available_time(early) == [_ms("2026-10-15")]
    assert available_time(_bars(["2026-10-14"], first_lag_ms=-1)) == [_ms("2026-10-15")]
    assert available_time(_bars(["2026-10-14"], first_lag_ms=0)) == [_ms("2026-10-15")]
    assert as_of(early, datetime(2026, 10, 14, 23, 59, tzinfo=UTC)).num_rows == 0


def test_a_bar_without_close_time_is_refused() -> None:
    rows = _bars(["2026-10-14"]).to_pylist()
    rows[0]["close_time"] = None
    with pytest.raises(ValueError, match="close_time"):
        available_time(pa.Table.from_pylist(rows, schema=SCHEMA))


def test_a_ts_stored_twice_is_refused(store: Path) -> None:
    # Same bar in a second month folder: a writer bug, never silently deduplicated.
    write_partition(_bars(["2019-08-15"]), store, "bitvavo", "USDC-EUR", datetime(2019, 9, 1, tzinfo=UTC))
    with pytest.raises(ValueError, match="more than once"):
        load(store, "bitvavo", "USDC-EUR", sha256_file=store / "prereg.sha256")


def test_the_lock_opens_exactly_up_to_the_oos_start(store: Path) -> None:
    sha = store / "prereg.sha256"
    oos = datetime(2019, 8, 17, tzinfo=UTC)
    assert load(store, "bitvavo", "BTC-EUR", end=oos, sha256_file=sha).num_rows == 2
    with pytest.raises(LoaderLockError):
        load(store, "bitvavo", "BTC-EUR", end=oos + timedelta(milliseconds=1), sha256_file=sha)
