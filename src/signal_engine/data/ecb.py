"""ECB EUR/USD reference rate (series EXR.D.USD.EUR.SP00.A, USD per 1 EUR) from the ECB Data Portal.

One row per TARGET business day; the fix is set at 14:15 CET and published around 16:00 CET on its
own day, so `close_time` = fix date 16:00 UTC (after publication, before that day's 00:00 UTC bar
closes). Weekends and holidays have no row: `last_fix` returns the last fix dated on or before the
bar day ("ECB_EURUSD[last fix date <= t]"), never a later one.
"""

import bisect
import csv
import io
import logging
import urllib.error
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa

from signal_engine.data import http
from signal_engine.storage import partition_path, read_partition, write_partition

SOURCE = "ecb"
SYMBOL = "EUR-USD"
SERIES = "EXR.D.USD.EUR.SP00.A"
URL = "https://data-api.ecb.europa.eu/service/data/EXR/D.USD.EUR.SP00.A"
DAY_MS = 86_400_000
PUBLISHED_AFTER_MS = 16 * 3_600_000  # 16:00 UTC is after the ~16:00 CET/CEST publication
MAX_FIX_AGE_DAYS = 5  # longest TARGET closure (Good Friday to Easter Monday) is 4 days

SCHEMA = pa.schema(
    [
        ("ts", pa.int64()),  # fix date 00:00 UTC, ms
        ("rate", pa.float64()),  # USD per 1 EUR
        ("close_time", pa.int64()),  # fix date 16:00 UTC, ms
        ("first_available_time", pa.int64()),  # first ingest of the row, ms UTC; never changes
        ("ingested_at", pa.int64()),
        ("source", pa.string()),
    ]
)

log = logging.getLogger(__name__)


def _day_ms(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp() * 1000)


def parse_csv(body: bytes, ingested_ms: int) -> pa.Table:
    """ECB `format=csvdata` answer -> rows sorted by fix date; empty or NaN values are skipped."""
    by_ts: dict[int, float] = {}
    for row in csv.DictReader(io.StringIO(body.decode("utf-8"))):
        if row.get("KEY") != SERIES:
            raise ValueError(f"unexpected ECB series {row.get('KEY')!r}, want {SERIES}")
        value = (row.get("OBS_VALUE") or "").strip()
        if not value or value.lower() == "nan":
            continue
        by_ts[_day_ms(date.fromisoformat(row["TIME_PERIOD"]))] = float(value)
    records = [
        {
            "ts": ts,
            "rate": by_ts[ts],
            "close_time": ts + PUBLISHED_AFTER_MS,
            "first_available_time": ingested_ms,
            "ingested_at": ingested_ms,
            "source": SOURCE,
        }
        for ts in sorted(by_ts)
    ]
    return pa.Table.from_pylist(records, schema=SCHEMA)


def fetch_ecb(
    start: date, end: date, get: http.HttpGet | None = None, now: datetime | None = None
) -> pa.Table:
    """Fixes dated start..end (inclusive). An ECB 404 ("No results found") is an empty table."""
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware UTC (point-in-time rule)")
    get = get or http.get
    url = f"{URL}?startPeriod={start.isoformat()}&endPeriod={end.isoformat()}&format=csvdata"
    try:
        body = get(url)
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        log.info("ecb: no fixes in range", extra={"start": start.isoformat(), "end": end.isoformat()})
        body = b""
    return parse_csv(body, int(now.timestamp() * 1000))


def store_ecb(table: pa.Table, root: Path) -> list[Path]:
    """Monthly partitions under raw/ecb/EUR-USD/. A stored fix is kept; a different value is logged."""
    if table.num_rows == 0:
        log.info("no ECB fixes, nothing written")
        return []
    months: dict[datetime, list[dict[str, Any]]] = {}
    for row in table.to_pylist():
        month = datetime.fromtimestamp(row["ts"] / 1000, UTC).replace(day=1, hour=0, minute=0, second=0)
        months.setdefault(month, []).append(row)
    paths = []
    for month, rows in sorted(months.items()):
        existing = partition_path(root, SOURCE, SYMBOL, month) / "part-0.parquet"
        stored = {r["ts"]: r for r in (read_partition(existing).to_pylist() if existing.exists() else [])}
        for row in rows:
            old = stored.setdefault(row["ts"], row)
            if old["rate"] != row["rate"]:
                log.warning(
                    "bar_revision",
                    extra={"symbol": SYMBOL, "ts": row["ts"], "old": old["rate"], "new": row["rate"]},
                )
        merged = pa.Table.from_pylist([stored[ts] for ts in sorted(stored)], schema=SCHEMA)
        paths.append(write_partition(merged, root, SOURCE, SYMBOL, month))
    return paths


def fix_index(ecb: pa.Table, days_ms: Sequence[int]) -> list[int | None]:
    """Row index in `ecb` of the last fix dated on or before each bar day (ms UTC); None when there
    is none or it is older than MAX_FIX_AGE_DAYS (a gap in the fetched data, not a holiday)."""
    ts_all = ecb.column("ts").to_pylist()
    order = sorted(range(len(ts_all)), key=ts_all.__getitem__)
    ts = [ts_all[i] for i in order]
    out: list[int | None] = []
    for day in days_ms:
        i = bisect.bisect_right(ts, day) - 1
        out.append(order[i] if i >= 0 and day - ts[i] <= MAX_FIX_AGE_DAYS * DAY_MS else None)
    return out


def last_fix(ecb: pa.Table, days_ms: Sequence[int]) -> list[float | None]:
    """USD per EUR of the last fix dated on or before each bar day; None as in `fix_index`."""
    rate = ecb.column("rate").to_pylist()
    return [None if i is None else rate[i] for i in fix_index(ecb, days_ms)]
