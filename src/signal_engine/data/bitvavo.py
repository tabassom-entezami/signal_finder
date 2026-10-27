"""Bitvavo daily candles via ccxt's public OHLCV endpoint (no API key), point-in-time clean.

Rules: UTC only, sorted and deduplicated by bar start, the still-open last bar dropped,
gaps reported (never filled), transient network errors retried and then raised.
A stored closed bar is never overwritten: a re-fetch with different values is logged
(`bar_revision`) and kept under `<symbol>/revisions/`, the stored row stays as it was.
"""

import logging
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import ccxt
import pyarrow as pa

from signal_engine.storage import partition_path, read_partition, write_partition

SOURCE = "bitvavo"
DAY_MS = 86_400_000
PAGE_LIMIT = 1440  # Bitvavo's maximum candles per request
MAX_RETRIES = 3  # retries after the first attempt, on ccxt.NetworkError only
BACKOFF_S = 1.0  # sleeps 1 s, 2 s, 4 s

SCHEMA = pa.schema(
    [
        ("ts", pa.int64()),
        ("open", pa.float64()),
        ("high", pa.float64()),
        ("low", pa.float64()),
        ("close", pa.float64()),
        ("volume", pa.float64()),
        ("close_time", pa.int64()),  # bar start + 1 day, ms UTC
        ("first_available_time", pa.int64()),  # first ingest of the closed bar, ms UTC; never changes
        ("ingested_at", pa.int64()),  # ingest run of this row, ms UTC
        ("source", pa.string()),
    ]
)
OHLCV = ("ts", "open", "high", "low", "close", "volume")
VALUES = OHLCV[1:]  # compared to detect a revision; the ingest columns are not

log = logging.getLogger(__name__)
_sleep = time.sleep  # patched in tests


class OhlcvExchange(Protocol):
    def fetch_ohlcv(self, symbol: str, timeframe: str, since: int | None, limit: int | None) -> Any: ...


def _iso(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, UTC).date().isoformat()


def _to_table(rows: Iterable[Sequence[Any]], ingested_ms: int) -> pa.Table:
    """OHLCV rows of one fetch, sorted and deduplicated by ts (within a fetch the later row wins)."""
    by_ts = {int(r[0]): r for r in rows}
    records = [
        dict(zip(OHLCV, by_ts[ts], strict=False))
        | {
            "ts": ts,
            "close_time": ts + DAY_MS,
            "first_available_time": ingested_ms,
            "ingested_at": ingested_ms,
            "source": SOURCE,
        }
        for ts in sorted(by_ts)
    ]
    return pa.Table.from_pylist(records, schema=SCHEMA)


def _fetch_page(exchange: OhlcvExchange, symbol: str, since_ms: int) -> list[list[Any]]:
    for attempt in range(MAX_RETRIES + 1):
        try:
            return list(exchange.fetch_ohlcv(symbol, "1d", since=since_ms, limit=PAGE_LIMIT))
        except ccxt.NetworkError as exc:
            if attempt == MAX_RETRIES:
                raise
            delay = BACKOFF_S * 2**attempt
            log.warning(
                "ccxt network error, retrying",
                extra={"symbol": symbol, "attempt": attempt + 1, "delay_s": delay, "error": repr(exc)},
            )
            _sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover


def find_gaps(ts: Sequence[int]) -> list[int]:
    """Missing daily bar starts between the first and the last bar (ts sorted, ms UTC)."""
    if not ts:
        return []
    have = set(ts)
    return [t for t in range(ts[0], ts[-1] + DAY_MS, DAY_MS) if t not in have]


class MarketCandles:
    """Bitvavo's `/{market}/candles` endpoint through ccxt's implicit public API, without ccxt's local
    market lookup: `fetch_ohlcv` raises for a market missing from ccxt's cached `/markets` list, so a
    delisted market's own candles answer could never be asked for. Same window as ccxt's `fetch_ohlcv`
    (`start = since`, `end = since + limit days`); rows are parsed like ccxt's `parse_ohlcv`."""

    def __init__(self, exchange: Any = None) -> None:
        self.exchange = exchange if exchange is not None else ccxt.bitvavo({"enableRateLimit": True})

    def fetch_ohlcv(
        self, symbol: str, timeframe: str, since: int | None, limit: int | None
    ) -> list[list[Any]]:
        if timeframe != "1d" or since is None or limit is None:
            raise ValueError("MarketCandles answers daily windows with since and limit only")
        request = {
            "market": symbol.replace("/", "-").upper(),
            "interval": "1d",
            "start": since,
            "end": since + limit * DAY_MS,
            "limit": limit,
        }
        answer = self.exchange.publicGetMarketCandles(request)
        if not isinstance(answer, list):
            raise ValueError(f"candles answer is not a list: {type(answer).__name__}")
        return [[int(r[0]), *(float(v) for v in r[1:6])] for r in answer]


@dataclass(frozen=True)
class DailyFetch:
    """A fetch with the windows it covered: each `(start_ms, end_ms)` window got a parsed answer
    (an empty list included), so a day inside a covered window with no bar was answered "no candle",
    not a fetch error."""

    table: pa.Table
    gaps: list[int]
    windows: list[tuple[int, int]]


def fetch_daily(
    symbol: str,
    since: datetime,
    exchange: OhlcvExchange | None = None,
    now: datetime | None = None,
) -> tuple[pa.Table, list[int]]:
    """Closed daily bars from `since` (UTC-aware) to now; returns (table, gap bar starts in ms)."""
    result = fetch_daily_windows(symbol, since, exchange, now)
    return result.table, result.gaps


def fetch_daily_windows(
    symbol: str,
    since: datetime,
    exchange: OhlcvExchange | None = None,
    now: datetime | None = None,
) -> DailyFetch:
    """`fetch_daily` plus the covered windows; any fetch error (after the retries) is raised."""
    if since.tzinfo is None:
        raise ValueError("since must be timezone-aware UTC (point-in-time rule)")
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware UTC (point-in-time rule)")
    if exchange is None:
        exchange = ccxt.bitvavo({"enableRateLimit": True})
    since_ms = int(since.timestamp() * 1000)
    now_ms = int(now.timestamp() * 1000)

    rows: list[list[Any]] = []
    windows: list[tuple[int, int]] = []
    cursor = since_ms
    while cursor <= now_ms:
        page = [r for r in _fetch_page(exchange, symbol, cursor) if int(r[0]) >= since_ms]
        windows.append((cursor, min(cursor + PAGE_LIMIT * DAY_MS, now_ms)))
        rows.extend(page)
        if page:
            cursor = max(max(int(r[0]) for r in page), cursor) + DAY_MS
        else:
            # Bitvavo answers a time window [since, since + limit days); an empty window
            # (e.g. before the market was listed) is skipped, not taken as the end.
            cursor += PAGE_LIMIT * DAY_MS

    closed = [r for r in rows if int(r[0]) + DAY_MS <= now_ms]
    if len(closed) < len(rows):
        open_ts = sorted({int(r[0]) for r in rows} - {int(r[0]) for r in closed})
        log.info("dropped unfinished bar", extra={"symbol": symbol, "bars": [_iso(t) for t in open_ts]})
    table = _to_table(closed, now_ms)
    gaps = find_gaps(table.column("ts").to_pylist())
    if gaps:
        log.warning(
            "candle gaps", extra={"symbol": symbol, "gap_count": len(gaps), "gaps": [_iso(t) for t in gaps]}
        )
    return DailyFetch(table, gaps, windows)


def _store_revisions(rows: list[dict[str, Any]], root: Path, store_symbol: str, month: datetime) -> None:
    """Append revised bars to `<symbol>/revisions/<yyyy>/<mm>/`; a revision already recorded is skipped."""
    folder = f"{store_symbol}/revisions"
    path = partition_path(root, SOURCE, folder, month) / "part-0.parquet"
    kept = read_partition(path).to_pylist() if path.exists() else []
    seen = {tuple(r[k] for k in OHLCV) for r in kept}
    new = [r for r in rows if tuple(r[k] for k in OHLCV) not in seen]
    if new:
        write_partition(pa.Table.from_pylist(kept + new, schema=SCHEMA), root, SOURCE, folder, month)


def store_daily(table: pa.Table, root: Path, symbol: str) -> list[Path]:
    """Write into monthly partitions. A bar already stored is kept as it is (point-in-time):
    new bars are added, a re-fetch with different values is logged and stored as a revision."""
    store_symbol = symbol.replace("/", "-")
    if table.num_rows == 0:
        log.info("no candles, nothing written", extra={"symbol": store_symbol})
        return []
    months: dict[datetime, list[dict[str, Any]]] = {}
    for row in table.to_pylist():
        month = datetime.fromtimestamp(row["ts"] / 1000, UTC).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        months.setdefault(month, []).append(row)
    paths = []
    for month, new_rows in sorted(months.items()):
        existing = partition_path(root, SOURCE, store_symbol, month) / "part-0.parquet"
        stored = {r["ts"]: r for r in (read_partition(existing).to_pylist() if existing.exists() else [])}
        for row in stored.values():
            # rows stored before these columns existed: close_time is derivable,
            # the ingest times are unknown and stay null
            row.setdefault("close_time", row["ts"] + DAY_MS)
            row.setdefault("source", SOURCE)
        revised = []
        for row in new_rows:
            old = stored.get(row["ts"])
            if old is None:
                stored[row["ts"]] = row
            elif any(old[k] != row[k] for k in VALUES):
                revised.append(row)
                log.warning(
                    "bar_revision",
                    extra={
                        "symbol": store_symbol,
                        "bar": _iso(row["ts"]),
                        # field names only: prices stay in the revisions partition, out of the logs,
                        # so a re-fetch never shows a locked out-of-sample price
                        "fields": [k for k in VALUES if old[k] != row[k]],
                    },
                )
        if revised:
            _store_revisions(revised, root, store_symbol, month)
        merged = pa.Table.from_pylist([stored[ts] for ts in sorted(stored)], schema=SCHEMA)
        paths.append(write_partition(merged, root, SOURCE, store_symbol, month))
    return paths
