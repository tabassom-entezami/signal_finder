"""Record Bitvavo public order books and market status on a fixed time grid (no API key).

Built to capture a maintenance halt and its reopen: every `interval` seconds, the top-25 book of
each symbol plus the venue's market status string ("trading", "halted", "auction", ...).
A failed fetch never stops the run: it becomes a row with `error` set. One Parquet file per tick
under data/raw/bitvavo_books/<yyyy>/<mm>/<dd>/<hh>/, written atomically.
"""

import json
import logging
import math
import os
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import ccxt
import pyarrow as pa
import pyarrow.parquet as pq

SOURCE = "bitvavo_books"
DEPTH = 25
TIMEOUT_MS = 3000  # ccxt default is 10 s; a hanging API must not stretch the 5 s grid
MAX_WAIT_S = 60.0  # sleep in chunks and re-read the wall clock (laptop sleep pauses timers)
LEVELS = pa.list_(pa.list_(pa.float64()))

SCHEMA = pa.schema(
    [
        ("ts", pa.int64()),  # time the book answer arrived, ms UTC
        ("tick", pa.int64()),  # grid time of the snapshot round, ms UTC
        ("symbol", pa.string()),
        ("bids", LEVELS),  # [[price, amount], ...] best first; null on error
        ("asks", LEVELS),
        ("nonce", pa.int64()),  # Bitvavo book nonce; unchanged nonce = frozen book
        ("status", pa.string()),  # raw Bitvavo market status; null if the status fetch failed
        ("error", pa.string()),  # null when both fetches worked
    ]
)

log = logging.getLogger(__name__)


class BookExchange(Protocol):
    def fetch_order_book(self, symbol: str, limit: int | None = None) -> Any: ...

    def fetch_markets(self) -> Any: ...


def _ms(ts: datetime) -> int:
    return int(ts.timestamp() * 1000)


def _levels(side: Any) -> list[list[float]]:
    return [[float(level[0]), float(level[1])] for level in side[:DEPTH]]


def _statuses(exchange: BookExchange) -> tuple[dict[str, str], str | None]:
    try:
        markets = exchange.fetch_markets()
        return {m["symbol"]: m["info"]["status"] for m in markets if m.get("info", {}).get("status")}, None
    except Exception as exc:  # recorded as a row error, never raised
        return {}, f"status: {exc!r}"


def snapshot(
    exchange: BookExchange, symbols: Sequence[str], tick: datetime, clock: Callable[[], datetime]
) -> pa.Table:
    """One round: market status once, then one book per symbol. Errors become row values."""
    statuses, status_error = _statuses(exchange)
    rows = []
    for symbol in symbols:
        row: dict[str, Any] = {"tick": _ms(tick), "symbol": symbol, "status": statuses.get(symbol)}
        errors = [status_error] if status_error else []
        try:
            book = exchange.fetch_order_book(symbol, limit=DEPTH)
            row |= {"bids": _levels(book["bids"]), "asks": _levels(book["asks"]), "nonce": book.get("nonce")}
        except Exception as exc:  # recorded as a row error, never raised
            errors.append(f"book: {exc!r}")
        row["ts"] = _ms(clock())
        row["error"] = "; ".join(errors) or None
        rows.append(row)
    return pa.Table.from_pylist(rows, schema=SCHEMA)


def write_snapshot(table: pa.Table, root: Path, tick: datetime) -> Path:
    """Atomic write (temporary name outside the *.parquet glob, then rename)."""
    tick = tick.astimezone(UTC)
    folder = root / "raw" / SOURCE / f"{tick:%Y}" / f"{tick:%m}" / f"{tick:%d}" / f"{tick:%H}"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"part-{_ms(tick)}.parquet"
    tmp = folder / f".part-{_ms(tick)}.tmp"
    pq.write_table(table, tmp)
    os.replace(tmp, path)
    return path


def _wait_until(target: datetime, clock: Callable[[], datetime], sleep: Callable[[float], None]) -> None:
    while (left := (target - clock()).total_seconds()) > 0:
        sleep(min(left, MAX_WAIT_S))


def record_books(
    symbols: Sequence[str],
    interval_s: float,
    start: datetime,
    end: datetime | None,
    root: Path,
    exchange: BookExchange | None = None,
    clock: Callable[[], datetime] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> dict[str, int]:
    """Snapshot every `interval_s` on the grid start + k·interval until `end` (None: until stopped).

    The Mac is not always on: after a sleep the recorder resumes on the next slot and
    appends the skipped span to `<root>/ops/book-gaps.jsonl`, so a gap is recorded, never silent.
    """
    if start.tzinfo is None or (end is not None and end.tzinfo is None):
        raise ValueError("start and end must be timezone-aware UTC (point-in-time rule)")
    if (end is not None and end <= start) or interval_s <= 0 or not symbols:
        raise ValueError("need end > start, interval > 0 and at least one symbol")
    clock = clock or (lambda: datetime.now(UTC))
    sleep = sleep or time.sleep
    if exchange is None:
        exchange = ccxt.bitvavo({"enableRateLimit": True, "timeout": TIMEOUT_MS})
    step = timedelta(seconds=interval_s)
    progress_every = max(1, round(60 / interval_s))
    counts = {"ticks": 0, "rows": 0, "errors": 0, "skipped": 0}

    if clock() < start:
        log.info("waiting for start", extra={"start": start.isoformat(), "symbols": list(symbols)})
        _wait_until(start, clock, sleep)
    tick = start
    while True:
        now = clock()
        if end is not None and now >= end:
            break
        if now - tick >= step:  # a whole slot missed (slow round, laptop sleep): skip to the next slot
            missed = math.ceil((now - tick) / step)
            counts["skipped"] += missed
            _record_gap(root, tick, tick + missed * step, missed)
            tick += missed * step
        if end is not None and tick >= end:
            break
        _wait_until(tick, clock, sleep)
        table = snapshot(exchange, symbols, tick, clock)
        write_snapshot(table, root, tick)
        errors = table.column("error").drop_null().to_pylist()
        counts["ticks"] += 1
        counts["rows"] += table.num_rows
        counts["errors"] += len(errors)
        if errors:
            log.warning("book snapshot errors", extra={"tick": tick.isoformat(), "errors": errors})
        if counts["ticks"] % progress_every == 0:
            log.info("record-books progress", extra={"tick": tick.isoformat(), **counts})
        tick += step
    log.info("record-books done", extra={"end": end.isoformat() if end else None, **counts})
    return counts


GAP_MIN_SLOTS = 3  # one or two late slots are a slow round, not a gap worth a row


def _record_gap(root: Path, first_missed: datetime, resumed: datetime, missed: int) -> None:
    """Append one row per skipped span (sleep, outage) so the gap is data, not a silent hole."""
    if missed < GAP_MIN_SLOTS:
        return
    log.warning("record-books gap", extra={"from": first_missed.isoformat(), "to": resumed.isoformat(),
                                            "missed_slots": missed})
    path = root / "ops" / "book-gaps.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"from": first_missed.isoformat(), "to": resumed.isoformat(),
                             "missed_slots": missed}) + "\n")
