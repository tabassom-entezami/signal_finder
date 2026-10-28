"""Point-in-time helpers and the loader with the frozen-rules lock.

Availability of a stored bar (`available_time`):
- archive and backfilled bars are available at `close_time`;
- live-ingested bars at `first_available_time`.
A bar counts as live-ingested when its first ingest came at most BACKFILL_LAG_MS after its close;
a later first ingest (a backfill, e.g. `fetch-candles --since 2019-03-08`) or an unknown one (null,
legacy rows) means `close_time`, so a backtest sees history at its close, not at the backfill date.
Archive rows carry `first_available_time = close_time` and fall under the same rule.

Loader lock: coin bars (every base asset except USDC/USDT/EUR/USD) at or after 2019-08-17 are only
loaded by a call that passes a rules-file hash and a purpose in SCOPE, and only while the rules-file text
still hashes to its record (the frozen rules file and its .sha256 line). Each purpose opens only with
its own rules file(s) in PINNED (compared by resolved path; no caller or environment override).
The "forward" purpose runs a frozen rule forward from its hash time: it opens every base asset, but only
for a range that starts on or after the first UTC day starting after the `# hashed_utc <ISO time>` on
line 2 of the .sha256 record; an earlier or open (None) start is refused, never trimmed.
The "lookback" purpose opens only the closes a frozen rule needs before its first forward day:
exact (source, symbol) pairs, `[forward start - LOOKBACK_CLOSES days, forward start)`, start and end
both given, and only once the wall clock read here has reached the forward start.
The "venue-premium" purpose opens only the closes of the (source, symbol) pairs listed in its frozen
report spec (`pairs:` line), from the spec's `first_day:` line to the start of the current UTC day
(wall clock read here), start and end both given.
Under both narrow purposes the query selects `ts` and `close` only.
The range is checked before any file is read, so a locked bar never enters memory.
"""

import hashlib
from datetime import UTC, date, datetime, timedelta
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
FORWARD_PURPOSE = "forward"  # a frozen rule run forward from its hash time, every coin, post-hash days only
LOOKBACK_PURPOSE = "lookback"  # the closes before a frozen rule's first forward day, nothing else
VENUE_PURPOSE = "venue-premium"  # a frozen descriptive report spec (fields only, no rule), closes only
ALL_ASSETS = ("*",)
# A key opens only the coins its frozen rules test: the M1 key never opens the altcoins held
# back for the next family (they get their own purpose and hash).
SCOPE = {
    PURPOSE: ("BTC", "ETH"),
    PREDLOG_PURPOSE: ("BTC", "ETH"),
    FORWARD_PURPOSE: ALL_ASSETS,
    LOOKBACK_PURPOSE: ("BTC", "ETH"),
    VENUE_PURPOSE: ("BTC", "ETH"),
}
REPORTS_DIR = Path(__file__).resolve().parents[3] / "docs" / "reports"
PREREG_SHA256_FILE = REPORTS_DIR / "m1-preregistration.sha256"
PREDLOG_SHA256_FILE = REPORTS_DIR / "predlog-preregistration.sha256"
HMAX_SHA256_FILE = REPORTS_DIR / "hmax-preregistration.sha256"
R2_SHA256_FILE = REPORTS_DIR / "r2-preregistration.sha256"
VENUE_SHA256_FILE = REPORTS_DIR / "venue-premium-preregistration.sha256"
# Each purpose opens only with its own frozen rules file(s): any other valid .md + .sha256 pair is refused.
PINNED: dict[str, tuple[Path, ...]] = {
    PURPOSE: (PREREG_SHA256_FILE,),
    PREDLOG_PURPOSE: (PREDLOG_SHA256_FILE,),
    FORWARD_PURPOSE: (HMAX_SHA256_FILE, R2_SHA256_FILE),
    LOOKBACK_PURPOSE: (R2_SHA256_FILE,),
    VENUE_PURPOSE: (VENUE_SHA256_FILE,),
}
LOOKBACK_CLOSES = 200
LOOKBACK_PAIRS = (("bitvavo", "BTC-USDC"), ("bitvavo", "ETH-USDC"))
CLOSE_COLUMNS = ("ts", "close")
HASHED_UTC_TAG = ("#", "hashed_utc")
FIRST_DAY_TAG = "first_day:"
PAIRS_TAG = "pairs:"


class LoaderLockError(PermissionError):
    """A coin bar from the OOS window was requested without the rules-file unlock."""


def _now() -> datetime:
    """The lock's own wall clock (patched in tests); callers never pass a time to the narrow purposes."""
    return datetime.now(UTC)


def day_start() -> datetime:
    """00:00 UTC of the lock's current day: the latest `end` the "venue-premium" purpose accepts."""
    return _now().astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def read_prereg_hash(path: Path = PREREG_SHA256_FILE) -> str:
    """First token of a `shasum -a 256` line."""
    return path.read_text().split()[0]


def forward_start(sha256_file: Path) -> datetime | None:
    """First UTC day that starts strictly after the hash time on line 2 (`# hashed_utc <ISO>Z`) of the
    .sha256 record; None when the line is missing, unparseable or not timezone-aware."""
    if not sha256_file.exists():
        return None
    lines = sha256_file.read_text().splitlines()
    parts = lines[1].split() if len(lines) > 1 else []
    if len(parts) < 3 or tuple(parts[:2]) != HASHED_UTC_TAG:
        return None
    try:
        hashed = datetime.fromisoformat(parts[2])
    except ValueError:
        return None
    if hashed.tzinfo is None:
        return None
    day = hashed.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return day + timedelta(days=1)


def _pinned(purpose: str, sha256_file: Path) -> bool:
    """The record is one of the purpose's own rules files (read at call time, so tests patch PINNED)."""
    return sha256_file.resolve() in {p.resolve() for p in PINNED.get(purpose, ())}


def _lookback_ok(sha256_file: Path, source: str | None, symbol: str | None, start: datetime | None,
                 end: datetime | None) -> bool:
    if (source, symbol) not in LOOKBACK_PAIRS or start is None or end is None:
        return False
    first = forward_start(sha256_file)
    if first is None or _ms(_now()) < _ms(first):
        return False
    return _ms(first) - LOOKBACK_CLOSES * DAY_MS <= _ms(start) and _ms(end) <= _ms(first)


def _spec_lines(text: Path) -> dict[str, str]:
    """`first_day:` and `pairs:` lines of a frozen report spec (the text is hash-checked by the caller)."""
    out = {}
    for line in text.read_text().splitlines():
        for tag in (FIRST_DAY_TAG, PAIRS_TAG):
            if line.startswith(tag):
                out[tag] = line[len(tag) :].strip()
    return out


def _venue_ok(text: Path, source: str | None, symbol: str | None, start: datetime | None,
              end: datetime | None) -> bool:
    if start is None or end is None or symbol is None or not symbol.upper().startswith(SCOPE[VENUE_PURPOSE]):
        return False
    spec = _spec_lines(text)
    pairs = {tuple(p.split(":", 1)) for p in spec.get(PAIRS_TAG, "").split() if ":" in p}
    try:
        first = datetime.combine(date.fromisoformat(spec.get(FIRST_DAY_TAG, "")), datetime.min.time(), UTC)
    except ValueError:
        return False
    return (source, symbol) in pairs and _ms(first) <= _ms(start) and _ms(end) <= _ms(day_start())


def is_unlocked(
    prereg_hash: str | None,
    purpose: str | None,
    sha256_file: Path = PREREG_SHA256_FILE,
    symbol: str | None = None,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    source: str | None = None,
) -> bool:
    """True only when the record is pinned to the purpose and the given hash, the .sha256 record and the
    digest of the rules-file text next to it (same name, .md) all agree: an edited rules file never unlocks.
    Under the "forward" purpose `start` must also be on or after `forward_start(sha256_file)`; under
    "lookback" and "venue-premium" the (source, symbol) pair, start and end must meet their windows."""
    text = sha256_file.with_suffix(".md")
    if prereg_hash is None or purpose not in SCOPE or not sha256_file.exists() or not text.exists():
        return False
    if not _pinned(purpose, sha256_file):
        return False
    if purpose == LOOKBACK_PURPOSE:
        if not _lookback_ok(sha256_file, source, symbol, start, end):
            return False
    elif purpose == VENUE_PURPOSE:
        if not _venue_ok(text, source, symbol, start, end):
            return False
    elif symbol is not None and SCOPE[purpose] != ALL_ASSETS and base_asset(symbol) not in SCOPE[purpose]:
        return False
    if purpose == FORWARD_PURPOSE:
        first = forward_start(sha256_file)
        if start is None or first is None or _ms(start) < _ms(first):
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
    return table.filter(pa.array([v is not None and v <= t_ms for v in times], type=pa.bool_()))  # empty-safe


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

    Raises LoaderLockError when the range reaches the OOS window for a coin without the unlock,
    for any symbol under the "forward" purpose when the range starts before the post-hash day, and
    for any pair or range outside the "lookback" and "venue-premium" windows.
    """
    end_ms = None if end is None else _ms(end)
    narrow = purpose in (LOOKBACK_PURPOSE, VENUE_PURPOSE)
    unlocked = is_unlocked(prereg_hash, purpose, sha256_file, symbol, start=start, end=end, source=source)
    if purpose == FORWARD_PURPOSE and not unlocked:
        first = forward_start(sha256_file)
        first_day = first.date() if first else "no hashed_utc line in " + sha256_file.name
        raise LoaderLockError(
            f"{symbol}: purpose {FORWARD_PURPOSE!r} loads only a range starting on or after the first "
            f"UTC day after the hash time ({first_day}), with the matching prereg_hash; got start={start}"
        )
    if narrow and not unlocked:
        raise LoaderLockError(
            f"{source}/{symbol}: purpose {purpose!r} loads only the pairs and window of its pinned rules "
            f"file, start and end both given, with the matching prereg_hash; got start={start}, end={end}"
        )
    if is_locked_symbol(symbol) and (end_ms is None or end_ms > _ms(OOS_START)) and not unlocked:
        raise LoaderLockError(
            f"{symbol}: bars on or after {OOS_START.date()} are held-out OOS data; pass "
            f"end <= {OOS_START.date()}, or prereg_hash (from {sha256_file.name}) and purpose={PURPOSE!r}"
        )
    folder = root / "raw" / source / symbol
    files = str(folder / "*" / "*" / "*.parquet")
    if not any(folder.glob("*/*/*.parquet")):
        single = folder.with_suffix(".parquet")  # a one-file store, read only under "venue-premium"
        if purpose != VENUE_PURPOSE or not single.is_file():
            raise FileNotFoundError(f"no partitions under {folder}")
        files = str(single)
    where, params = ["TRUE"], [files]
    params_ms: list[int] = []
    if start is not None:
        where.append("ts >= ?")
        params_ms.append(_ms(start))
    if end_ms is not None:
        where.append("ts < ?")
        params_ms.append(end_ms)
    select = ", ".join(CLOSE_COLUMNS) if narrow else "*"  # narrow purposes never read open/high/low/volume
    sql = (
        f"SELECT {select} FROM read_parquet(?, union_by_name = true) "  # noqa: S608 (fixed fragments)
        f"WHERE {' AND '.join(where)} ORDER BY ts"
    )
    con = duckdb.connect()
    try:
        table = con.execute(sql, [*params, *params_ms]).to_arrow_table()
    finally:
        con.close()
    if pc.count_distinct(table.column("ts")).as_py() != table.num_rows:
        raise ValueError(f"{symbol}: the same ts is stored more than once under {folder} (writer bug)")
    return table
