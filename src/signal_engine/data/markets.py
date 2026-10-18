"""Daily Bitvavo `/v2/markets` + `/v2/assets` snapshot (`record-markets`) with a diff vs the latest
earlier snapshot: listings, delistings, status and field changes, each with `known_at` (our fetch time).

Snapshot: data/raw/bitvavo_markets/<yyyy-mm-dd>.parquet, one row per market and per asset; a rerun on
the same UTC day replaces that day's file. Diff: data/raw/bitvavo_markets/diffs/<yyyy-mm-dd>.parquet
(written even when empty: "no change" is information). Timestamps are int64 ms UTC.
No price field: `minOrderInBaseAsset` and `maxOrderInBaseAsset` are dropped at fetch, because each
is a quote amount divided by the current price (a daily price proxy).

Terms and robots checked 2026-10-17: Bitvavo public REST API v2 (documented, no key), used
"as documented, rate-limited, honest User-Agent"); api.bitvavo.com/robots.txt answers 404 (no
rules). Two requests per run, 1 s apart.
"""

import json
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from signal_engine.data import http
from signal_engine.storage import day_path, write_atomic

SOURCE = "bitvavo_markets"
API = "https://api.bitvavo.com/v2"
PRICE_PROXY_FIELDS = ("minOrderInBaseAsset", "maxOrderInBaseAsset")
REQUEST_GAP_S = 1.0

SCHEMA = pa.schema(
    [
        ("known_at", pa.int64()),  # our fetch time, ms UTC
        ("kind", pa.string()),  # market | asset
        ("key", pa.string()),  # e.g. BTC-EUR, or BTC
        ("status", pa.string()),  # market status; asset "deposit=<s>;withdrawal=<s>"
        ("fields", pa.string()),  # the remaining raw fields as sorted JSON
    ]
)
DIFF_SCHEMA = pa.schema(
    [
        ("known_at", pa.int64()),
        ("prev_known_at", pa.int64()),
        ("prev_date", pa.string()),  # yyyy-mm-dd of the snapshot compared against
        ("kind", pa.string()),
        ("key", pa.string()),
        ("change", pa.string()),  # listed | delisted | status | field
        ("field", pa.string()),
        ("old", pa.string()),
        ("new", pa.string()),
    ]
)

log = logging.getLogger(__name__)


def _row(known_at: int, kind: str, key: str, status: str | None, raw: dict[str, Any]) -> dict[str, Any]:
    fields = {k: v for k, v in raw.items() if k not in PRICE_PROXY_FIELDS}
    return {
        "known_at": known_at,
        "kind": kind,
        "key": key,
        "status": status,
        "fields": json.dumps(fields, sort_keys=True),
    }


def snapshot_rows(markets: list[dict[str, Any]], assets: list[dict[str, Any]], known_at: int) -> pa.Table:
    rows = [_row(known_at, "market", m["market"], m.get("status"), m) for m in markets]
    rows += [
        _row(
            known_at,
            "asset",
            a["symbol"],
            f"deposit={a.get('depositStatus')};withdrawal={a.get('withdrawalStatus')}",
            a,
        )
        for a in assets
    ]
    return pa.Table.from_pylist(rows, schema=SCHEMA)


def diff(prev: pa.Table, cur: pa.Table, prev_date: str) -> pa.Table:
    """Rows that changed between two snapshots (keyed on kind + key)."""
    old = {(r["kind"], r["key"]): r for r in prev.to_pylist()}
    new = {(r["kind"], r["key"]): r for r in cur.to_pylist()}
    known_at = cur.column("known_at")[0].as_py()
    prev_known_at = prev.column("known_at")[0].as_py()
    out: list[dict[str, Any]] = []

    def add(kind: str, key: str, change: str, field: str | None, a: Any, b: Any) -> None:
        out.append(
            {
                "known_at": known_at,
                "prev_known_at": prev_known_at,
                "prev_date": prev_date,
                "kind": kind,
                "key": key,
                "change": change,
                "field": field,
                "old": None if a is None else str(a),
                "new": None if b is None else str(b),
            }
        )

    for k in sorted(new.keys() - old.keys()):
        add(*k, "listed", None, None, new[k]["status"])
    for k in sorted(old.keys() - new.keys()):
        add(*k, "delisted", None, old[k]["status"], None)
    for k in sorted(new.keys() & old.keys()):
        a, b = old[k], new[k]
        if a["status"] != b["status"]:
            add(*k, "status", "status", a["status"], b["status"])
        fa, fb = json.loads(a["fields"]), json.loads(b["fields"])
        for f in sorted(fa.keys() | fb.keys()):
            if fa.get(f) != fb.get(f) and f not in ("status", "depositStatus", "withdrawalStatus"):
                add(*k, "field", f, fa.get(f), fb.get(f))
    return pa.Table.from_pylist(out, schema=DIFF_SCHEMA)


def record_markets(
    root: Path,
    get: http.HttpGet = http.get,
    clock: Callable[[], datetime] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> dict[str, int]:
    """Snapshot + diff; raises on an HTTP error or an empty `/markets` or `/assets` answer."""
    clock = clock or (lambda: datetime.now(UTC))
    sleep = sleep or time.sleep
    now = clock()
    markets = json.loads(get(f"{API}/markets"))
    sleep(REQUEST_GAP_S)
    assets = json.loads(get(f"{API}/assets"))
    if not markets or not assets:
        raise ValueError(f"empty answer: {len(markets)} markets, {len(assets)} assets")
    table = snapshot_rows(markets, assets, int(now.timestamp() * 1000))
    path = day_path(root, SOURCE, now)
    earlier = sorted(p for p in path.parent.glob("*.parquet") if p.name < path.name)
    write_atomic(table, path)
    counts = {"markets": len(markets), "assets": len(assets), "changes": -1}
    if earlier:
        changes = diff(pq.read_table(earlier[-1]), table, earlier[-1].stem)
        write_atomic(changes, path.parent / "diffs" / path.name)
        counts["changes"] = changes.num_rows
    log.info(
        "record-markets done",
        extra={**counts, "file": str(path), "prev": earlier[-1].stem if earlier else None},
    )
    return counts
