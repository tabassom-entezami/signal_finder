"""Point-in-time helpers and the loader with the frozen-rules lock.

Availability of a stored bar (`available_time`):
- archive and backfilled bars are available at `close_time`;
- live-ingested bars at `first_available_time`.
A bar counts as live-ingested when its first ingest came at most BACKFILL_LAG_MS after its close;
a later first ingest (a backfill, e.g. `fetch-candles --since 2019-03-08`) or an unknown one (null,
legacy rows) means `close_time`, so a backtest sees history at its close, not at the backfill date.
Archive rows carry `first_available_time = close_time` and fall under the same rule.

Loader lock: coin bars (every base asset except USDC/USDT/EUR/USD) at or after 2019-08-17 are only
loaded by a call that passes a rules-file hash and a purpose in SCOPE ("m1-oos", "predlog"), and only
while the rules-file text still hashes to its record (the frozen rules file and its .sha256 line).
The range is checked before any file is read, so a locked bar never enters memory.
"""

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.compute as pc

DAY_MS = 86_400_000
BACKFILL_LAG_MS = DAY_MS
OOS_START = datetime(2019, 8, 17, tzinfo=UTC)
# Only cash-like series stay open: every coin's price path after the OOS start is held-out test data,
# BTC/ETH for M1 and the never-loaded altcoins for the next family.
UNLOCKED_ASSETS = ("USDC", "USDT", "EUR", "USD")
QUOTES = ("USDT", "USDC", "EUR", "USD")
PURPOSE = "m1-oos"
PREDLOG_PURPOSE = "predlog"  # the forward paper prediction log runs the frozen B1.1.0 rule on today's bars
# A key opens only the coins its frozen rules test: the M1 key never opens the altcoins held
# back for the next family (they get their own purpose and hash).
SCOPE = {PURPOSE: ("BTC", "ETH"), PREDLOG_PURPOSE: ("BTC", "ETH")}
PREREG_SHA256_FILE = Path(__file__).resolve().parents[3] / "docs" / "reports" / "m1-preregistration.sha256"
PREDLOG_SHA256_FILE = PREREG_SHA256_FILE.with_name("predlog-preregistration.sha256")


class LoaderLockError(PermissionError):
    """A coin bar from the OOS window was requested without the rules-file unlock."""


def read_prereg_hash(path: Path = PREREG_SHA256_FILE) -> str:
    """First token of a `shasum -a 256` line."""
    return path.read_text().split()[0]


def is_unlocked(
    prereg_hash: str | None,
    purpose: str | None,
    sha256_file: Path = PREREG_SHA256_FILE,
    symbol: str | None = None,
) -> bool:
    """True only when the given hash, the .sha256 record and the digest of the rules-file
    text next to it (same name, .md) all agree: an edited rules file never unlocks."""
    text = sha256_file.with_suffix(".md")
    if prereg_hash is None or purpose not in SCOPE or not sha256_file.exists() or not text.exists():
        return False
    if symbol is not None and base_asset(symbol) not in SCOPE[purpose]:
        return False
    digest = hashlib.sha256(text.read_bytes()).hexdigest()
    return prereg_hash == read_prereg_hash(sha256_file) == digest


def base_asset(symbol: str) -> str:
    """BTC/EUR, BTC-EUR, BTCUSDT -> BTC; EUR-USD -> EUR."""
    s = symbol.upper()
    for sep in ("/", "-"):
        if sep in s:
            return s.split(sep)[0]
    for quote in QUOTES:
        if s.endswith(quote) and len(s) > len(quote):
            return s[: -len(quote)]
    return s


def is_locked_symbol(symbol: str) -> bool:
    """Every coin is locked after the OOS start; only stablecoin and FX series are open."""
    return base_asset(symbol) not in UNLOCKED_ASSETS


def _ms(ts: datetime) -> int:
    if ts.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware UTC (point-in-time rule)")
    return int(ts.timestamp() * 1000)


def closed(table: pa.Table, now: datetime, interval_ms: int = DAY_MS) -> pa.Table:
    """Bars whose interval has ended at `now` (`ts + interval <= now`)."""
    return table.filter(pc.less_equal(pc.add(table.column("ts"), interval_ms), _ms(now)))


def assert_grid(table: pa.Table, offset_ms: int = 0, interval_ms: int = DAY_MS) -> None:
    """Raise unless every `ts % interval == offset` (daily default: every bar opens at 00:00 UTC)."""
    off = [t for t in table.column("ts").to_pylist() if t is None or t % interval_ms != offset_ms]
    if off:
        raise ValueError(
            f"{len(off)} bar(s) off the grid (offset {offset_ms} ms, interval {interval_ms} ms): {off[:5]}"
        )


def available_time(table: pa.Table) -> list[int]:
    """Per row, the earliest time the bar may be used (see the module docstring)."""
    out = []
    for close, first in zip(
        table.column("close_time").to_pylist(), table.column("first_available_time").to_pylist(), strict=True
    ):
        if close is None:
            raise ValueError("bar without close_time: its availability is unknown (point-in-time rule)")
        # An ingest before the close (e.g. an ECB fix published before the assumed time) never
        # makes a bar usable earlier than its close.
        live = first is not None and 0 <= first - close <= BACKFILL_LAG_MS
        out.append(first if live else close)
    return out


def as_of(table: pa.Table, t: datetime, *, column: str | None = None) -> pa.Table:
    """Rows usable at `t`. column=None applies `available_time`; "close_time" or
    "first_available_time" use that column only (null = not available)."""
    t_ms = _ms(t)
    times = available_time(table) if column is None else table.column(column).to_pylist()
    return table.filter(pa.array([v is not None and v <= t_ms for v in times]))


def load(
    root: Path,
    source: str,
    symbol: str,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    prereg_hash: str | None = None,
    purpose: str | None = None,
    sha256_file: Path = PREREG_SHA256_FILE,
) -> pa.Table:
    """Stored bars of `raw/<source>/<symbol>/` with `start <= ts < end`, sorted by ts.

    Raises LoaderLockError when the range reaches the OOS window for a coin without the unlock.
    """
    end_ms = None if end is None else _ms(end)
    if is_locked_symbol(symbol) and (end_ms is None or end_ms > _ms(OOS_START)):
        if not is_unlocked(prereg_hash, purpose, sha256_file, symbol):
            raise LoaderLockError(
                f"{symbol}: bars on or after {OOS_START.date()} are held-out OOS data; pass "
                f"end <= {OOS_START.date()}, or prereg_hash (from {sha256_file.name}) and purpose={PURPOSE!r}"
            )
    folder = root / "raw" / source / symbol
    if not any(folder.glob("*/*/*.parquet")):
        raise FileNotFoundError(f"no partitions under {folder}")
    where, params = ["TRUE"], [str(folder / "*" / "*" / "*.parquet")]
    params_ms: list[int] = []
    if start is not None:
        where.append("ts >= ?")
        params_ms.append(_ms(start))
    if end_ms is not None:
        where.append("ts < ?")
        params_ms.append(end_ms)
    sql = f"SELECT * FROM read_parquet(?, union_by_name = true) WHERE {' AND '.join(where)} ORDER BY ts"  # noqa: S608 (fixed fragments)
    con = duckdb.connect()
    try:
        table = con.execute(sql, [*params, *params_ms]).to_arrow_table()
    finally:
        con.close()
    if pc.count_distinct(table.column("ts")).as_py() != table.num_rows:
        raise ValueError(f"{symbol}: the same ts is stored more than once under {folder} (writer bug)")
    return table
