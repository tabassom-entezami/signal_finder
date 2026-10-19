"""Record Bitvavo public order books and market status on a fixed time grid (no API key).

Built to capture a maintenance halt and its reopen: every `interval` seconds, the top-25 book of
each symbol plus the venue's market status string ("trading", "halted", "auction", ...).
A failed fetch never stops the run: it becomes a row with `error` set. One Parquet file per tick
under data/raw/bitvavo_books/<yyyy>/<mm>/<dd>/<hh>/, written atomically. Once an hour is over, its tick
files are joined into one `hour-<yyyymmddhh>.parquet` (zstd, every row kept, checked before a tick goes).
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
STALE_TMP_S = 600  # a join takes seconds: an older temporary file belongs to a stopped run
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


def _hour_start(folder: Path) -> datetime | None:
    """UTC start of a <yyyy>/<mm>/<dd>/<hh> folder; None for any other name."""
    day = folder.parent
    try:
        start = datetime.strptime(f"{day.parent.parent.name}/{day.parent.name}/{day.name}/{folder.name}",
                                  "%Y/%m/%d/%H")
    except ValueError:
        return None
    return start.replace(tzinfo=UTC)


def compact_hour(folder: Path) -> Path | None:
    """Join one finished hour's tick files into `hour-<yyyymmddhh>.parquet`; None when none is left.

    The joined file is read back and must equal its sources (same rows, one row per tick and symbol)
    before any tick file is removed. A run that stopped after the rename but before the removals leaves
    ticks already inside the hour file: they are removed without being added twice.
    """
    start = _hour_start(folder)
    parts = sorted(folder.glob("part-*.parquet"))
    if start is None or not parts:
        return None
    for stale in folder.glob(".hour-*.tmp"):  # left by a stopped run; a live one is seconds old
        if time.time() - stale.stat().st_mtime > STALE_TMP_S:
            stale.unlink(missing_ok=True)
    hour = folder / f"hour-{start:%Y%m%d%H}.parquet"
    odd = [p.name for p in ([hour] if hour.exists() else []) + parts if pq.read_schema(p) != SCHEMA]
    if odd:  # reading with SCHEMA would drop or invent columns, and the read-back could not see it
        raise RuntimeError(f"files with another schema in {folder}: {', '.join(odd[:3])}")
    tables = [pq.read_table(hour, schema=SCHEMA)] if hour.exists() else []
    done = set(tables[0].column("tick").to_pylist()) if tables else set()
    new = [p for p in parts if int(p.stem.removeprefix("part-")) not in done]
    tables += [pq.read_table(p, schema=SCHEMA) for p in new]
    joined = pa.concat_tables(tables).sort_by([("tick", "ascending"), ("symbol", "ascending")])
    keys = set(zip(joined.column("tick").to_pylist(), joined.column("symbol").to_pylist(), strict=True))
    if len(keys) != joined.num_rows:
        raise RuntimeError(f"duplicate (tick, symbol) rows in {folder}")
    tmp = folder / f".hour-{start:%Y%m%d%H}-{os.getpid()}.tmp"
    pq.write_table(joined, tmp, compression="zstd")
    if not pq.read_table(tmp, schema=SCHEMA).equals(joined):
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"joined hour differs from its tick files: {folder}")
    os.replace(tmp, hour)
    for p in parts:
        p.unlink(missing_ok=True)
    return hour


def compact_books(root: Path, now: datetime) -> list[Path]:
    """Join every hour that ended at least a minute before `now` (a running recorder may still be writing)."""
    out = []
    for folder in sorted((root / "raw" / SOURCE).glob("*/*/*/*")):
        start = _hour_start(folder)
        if start is None or start + timedelta(hours=1, minutes=1) > now:
            continue
        if path := compact_hour(folder):
            out.append(path)
    return out


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
    tick = _resume_tick(root, start, interval_s, clock)
    written: Path | None = None  # hour folder of the last snapshot this run wrote
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
        folder = write_snapshot(table, root, tick).parent
        if written is not None and folder != written:  # the hour this run was writing is over: join it
            _compact_finished(written)
        written = folder
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


def _last_tick_ms(root: Path, start_ms: int) -> int | None:
    """Newest tick at or after `start` already on disk (tick files and joined hour files), or None."""
    for folder in sorted((root / "raw" / SOURCE).glob("*/*/*/*"), reverse=True):
        if _hour_start(folder) is None:
            continue
        ticks = [int(p.stem.removeprefix("part-")) for p in folder.glob("part-*.parquet")
                 if p.stem.removeprefix("part-").isdigit()]
        for hour in folder.glob("hour-*.parquet"):
            ticks += pq.read_table(hour, columns=["tick"]).column("tick").to_pylist()
        ticks = [t for t in ticks if t >= start_ms]
        if ticks:
            return max(ticks)
    return None


def _resume_tick(root: Path, start: datetime, interval_s: float, clock: Callable[[], datetime]) -> datetime:
    """First grid slot (start + k·interval) after the last tick already recorded; `start` when none is.

    A restart then counts only its own downtime as a gap, never the span an earlier run recorded.
    """
    last = _last_tick_ms(root, _ms(start))
    if last is None:
        return start
    step_ms = round(interval_s * 1000)
    tick = start + timedelta(milliseconds=((last - _ms(start)) // step_ms + 1) * step_ms)
    if tick - clock() > timedelta(milliseconds=step_ms):  # a tick on disk lies ahead of this clock
        log.warning("record-books last tick is ahead of the clock", extra={"resume": tick.isoformat()})
    return tick


def _compact_finished(folder: Path) -> None:
    """Join the hour the recorder just left; a failure keeps the tick files and never stops recording."""
    began = time.monotonic()
    try:
        path = compact_hour(folder)
    except Exception as exc:  # logged, never raised: recording comes first
        log.error("compact-books failed", extra={"folder": str(folder), "error": repr(exc)})
        return
    seconds = round(time.monotonic() - began, 3)
    log.info("compact-books hour joined", extra={"file": str(path), "seconds": seconds})


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
