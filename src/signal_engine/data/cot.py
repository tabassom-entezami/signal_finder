"""Weekly CFTC Commitments of Traders, Traders in Financial Futures (futures only), for the CME bitcoin
and ether contracts (`record-cot`), with the actual release time.

`release_at` is the row's Socrata `:created_at`, the moment the CFTC published it (measured
2026-10-17: every recent week reads Friday 19:30 UTC = 15:30 ET, holiday shifts included), never
report_date + 3. `known_at` = release_at; `known_at_type` P when the release is within 10 days of the
Tuesday report date, else A (late: the 2022-09-13 bulk load of older history, and the 2025
shutdown backlog; an upper bound, so still free of look-ahead). First run 2026-10-17: 796 P, 476 A.
Append-only: a (market_code, report_date) row is stored once, the first time it is seen, in
data/raw/cot/<yyyy-mm-dd of the fetch>.parquet. Timestamps are int64 ms UTC. No price field exists.

Terms and robots checked 2026-10-17: CFTC public reporting API (US government work, public domain),
terms checked; publicreporting.cftc.gov/robots.txt: `Crawl-delay: 1`, disallows /browse
query pages only (the /resource and /api/views paths used here are allowed). One request per run.
"""

import json
import logging
import urllib.parse
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa

from signal_engine.data import http
from signal_engine.storage import append_new

SOURCE = "cot"
URL = "https://publicreporting.cftc.gov/resource/gpe5-46if.json"  # TFF - Futures Only
MARKETS = {  # cftc_contract_market_code: name (CME)
    "133741": "BITCOIN",
    "133742": "MICRO BITCOIN",
    "146021": "ETHER CASH SETTLED",
    "146022": "MICRO ETHER",
}
POSITIONS = {  # our column: CFTC field (verified against the dataset's column list 2026-10-17)
    "open_interest": "open_interest_all",
    "dealer_long": "dealer_positions_long_all",
    "dealer_short": "dealer_positions_short_all",
    "dealer_spread": "dealer_positions_spread_all",
    "asset_mgr_long": "asset_mgr_positions_long",
    "asset_mgr_short": "asset_mgr_positions_short",
    "asset_mgr_spread": "asset_mgr_positions_spread",
    "lev_money_long": "lev_money_positions_long",
    "lev_money_short": "lev_money_positions_short",
    "lev_money_spread": "lev_money_positions_spread",
    "other_rept_long": "other_rept_positions_long",
    "other_rept_short": "other_rept_positions_short",
    "other_rept_spread": "other_rept_positions_spread",
    "nonrept_long": "nonrept_positions_long_all",
    "nonrept_short": "nonrept_positions_short_all",
}
ON_TIME_MS = 10 * 86_400_000

SCHEMA = pa.schema(
    [
        ("key", pa.string()),  # <market_code>|<report_date yyyy-mm-dd>, the dedupe key
        ("market_code", pa.string()),
        ("market", pa.string()),
        ("report_date", pa.int64()),  # Tuesday as-of date, 00:00 UTC ms
        ("release_at", pa.int64()),  # CFTC publication time, ms UTC
        ("observed_at", pa.int64()),
        ("known_at", pa.int64()),
        ("known_at_type", pa.string()),  # P | A
        *[(c, pa.int64()) for c in POSITIONS],  # contracts
    ]
)

log = logging.getLogger(__name__)


def query_url() -> str:
    codes = ",".join(f"'{c}'" for c in MARKETS)
    params = {
        "$select": ":created_at,cftc_contract_market_code,report_date_as_yyyy_mm_dd,"
        + ",".join(POSITIONS.values()),
        "$where": f"cftc_contract_market_code in({codes})",
        "$order": "report_date_as_yyyy_mm_dd",
        "$limit": "50000",
    }
    return f"{URL}?{urllib.parse.urlencode(params)}"


def _iso_ms(text: str) -> int:
    ts = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return int((ts if ts.tzinfo else ts.replace(tzinfo=UTC)).timestamp() * 1000)


def parse(body: bytes, observed_at: int) -> pa.Table:
    rows: list[dict[str, Any]] = []
    for r in json.loads(body):
        code = r["cftc_contract_market_code"]
        report = _iso_ms(r["report_date_as_yyyy_mm_dd"])  # Socrata floating date = the Tuesday
        release = _iso_ms(r[":created_at"])
        if release < report:
            raise ValueError(f"release before report date: {r}")
        row = {
            "key": f"{code}|{r['report_date_as_yyyy_mm_dd'][:10]}",
            "market_code": code,
            "market": MARKETS[code],
            "report_date": report,
            "release_at": release,
            "observed_at": observed_at,
            "known_at": release,
            "known_at_type": "P" if release - report <= ON_TIME_MS else "A",
        }
        row |= {c: int(float(r[f])) if r.get(f) not in (None, "") else None for c, f in POSITIONS.items()}
        rows.append(row)
    return pa.Table.from_pylist(rows, schema=SCHEMA)


def record_cot(
    root: Path, get: http.HttpGet = http.get, clock: Callable[[], datetime] | None = None
) -> dict[str, int]:
    """Fetch all weeks of the four CME contracts and store the rows not seen before."""
    clock = clock or (lambda: datetime.now(UTC))
    now = clock()
    table = parse(get(query_url()), int(now.timestamp() * 1000))
    if table.num_rows == 0:
        raise ValueError("0 COT rows fetched")
    new = append_new(table, root, SOURCE, "key", now)
    last = max(table.column("report_date").to_pylist())
    counts = {"fetched": table.num_rows, "new": new}
    log.info(
        "record-cot done",
        extra={**counts, "last_report": datetime.fromtimestamp(last / 1000, UTC).date().isoformat()},
    )
    return counts
