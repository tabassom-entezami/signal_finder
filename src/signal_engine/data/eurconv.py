"""EURconv: the M1 signal series u[i,t] in USD terms (frozen rules):

    t >= 2020-12-10:  u = BitvavoEUR.close[t] / BitvavoUSDC_EUR.close[t]       (same bar)
    t >= 2019-03-08:  u = BitvavoEUR.close[t] * ECB_EURUSD[last fix date <= t]
    else:             u = BinanceSpot[USDT].close[t]                            (warm-up only)

A missing input bar means no EURconv bar (a gap, never forward-filled; the ECB fix alone is carried
over weekends and holidays, as the formula says). Every bar must open at 00:00 UTC. Each row gets
`first_available_time` = the latest availability of its inputs (pit.available_time), so a bar at t
uses only same-bar or earlier inputs, plus a `licence_stage` tag from its price source.
"""

import importlib
import logging
from datetime import UTC, datetime
from types import ModuleType
from typing import Any

import pyarrow as pa

from signal_engine.data import ecb
from signal_engine.data.pit import DAY_MS, assert_grid, available_time

binance_vision: ModuleType | None
try:  # Private module, not in the public repo: create your own `signal_engine/data/binance_vision.py` providing
    # SOURCE and LICENCE_STAGE (the name and licence tag of the warm-up bar source)
    binance_vision = importlib.import_module("signal_engine.data.binance_vision")
except ImportError:
    binance_vision = None

ECB_FROM = datetime(2019, 3, 8, tzinfo=UTC)
USDC_FROM = datetime(2020, 12, 10, tzinfo=UTC)
ECB_FROM_MS = int(ECB_FROM.timestamp() * 1000)
USDC_FROM_MS = int(USDC_FROM.timestamp() * 1000)
BITVAVO_LICENCE_STAGE = "S2-own-use"  # Bitvavo Developer Agreement: own use; publishing needs a licence

SCHEMA = pa.schema(
    [
        ("ts", pa.int64()),
        ("close", pa.float64()),  # USD per coin
        ("close_time", pa.int64()),
        ("first_available_time", pa.int64()),
        ("source", pa.string()),  # "binance_vision", "bitvavo*ecb" or "bitvavo/usdc"
        ("licence_stage", pa.string()),
    ]
)

log = logging.getLogger(__name__)


def _by_ts(table: pa.Table) -> dict[int, tuple[float, int]]:
    """ts -> (close, availability) of a bar table, after the 00:00 UTC grid check."""
    assert_grid(table)
    pairs = zip(table.column("close").to_pylist(), available_time(table), strict=True)
    return dict(zip(table.column("ts").to_pylist(), pairs, strict=True))


def eurconv(
    eur: pa.Table,
    ecb_fixes: pa.Table,
    usdc_eur: pa.Table | None = None,
    binance_usdt: pa.Table | None = None,
) -> pa.Table:
    """EURconv daily bars of one coin from its Bitvavo EUR bars, the ECB fixes, Bitvavo USDC-EUR
    bars (from 2020-12-10) and Binance USDT bars (warm-up, before 2019-03-08)."""
    assert_grid(ecb_fixes)
    eur_rows = _by_ts(eur)
    usdc_rows = _by_ts(usdc_eur) if usdc_eur is not None else {}
    warm_rows = _by_ts(binance_usdt) if binance_usdt is not None else {}
    fix_avail = available_time(ecb_fixes)
    fix_rate = ecb_fixes.column("rate").to_pylist()

    records: list[dict[str, Any]] = []
    missing: list[int] = []
    if warm_rows and binance_vision is None:
        raise ImportError("warm-up bars need the private module signal_engine.data.binance_vision")
    for ts, (close, avail) in sorted(warm_rows.items()):
        if ts < ECB_FROM_MS and binance_vision is not None:
            records.append(_row(ts, close, avail, binance_vision.SOURCE, binance_vision.LICENCE_STAGE))
    eur_days = sorted(t for t in eur_rows if t >= ECB_FROM_MS)
    for ts, fix in zip(eur_days, ecb.fix_index(ecb_fixes, eur_days), strict=True):
        close, avail = eur_rows[ts]
        if ts < USDC_FROM_MS:
            if fix is None:
                missing.append(ts)
                continue
            records.append(
                _row(
                    ts,
                    close * fix_rate[fix],
                    max(avail, fix_avail[fix]),
                    "bitvavo*ecb",
                    BITVAVO_LICENCE_STAGE,
                )
            )
        elif ts in usdc_rows:
            usdc_close, usdc_avail = usdc_rows[ts]
            records.append(
                _row(ts, close / usdc_close, max(avail, usdc_avail), "bitvavo/usdc", BITVAVO_LICENCE_STAGE)
            )
        else:
            missing.append(ts)
    if missing:
        log.warning(
            "eurconv gaps",
            extra={
                "gap_count": len(missing),
                "gaps": [datetime.fromtimestamp(t / 1000, UTC).date().isoformat() for t in missing],
            },
        )
    table = pa.Table.from_pylist(records, schema=SCHEMA)
    assert_grid(table)
    return table


def _row(ts: int, close: float, avail: int, source: str, licence_stage: str) -> dict[str, Any]:
    return {
        "ts": ts,
        "close": close,
        "close_time": ts + DAY_MS,
        "first_available_time": avail,
        "source": source,
        "licence_stage": licence_stage,
    }
