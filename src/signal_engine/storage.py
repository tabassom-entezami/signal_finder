"""Parquet partitions under data/raw/<source>/<symbol>/<yyyy>/<mm>/, read back through DuckDB; day files
under data/raw/<source>/<yyyy-mm-dd>.parquet for the append-only forward recorders."""

import os
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq


def partition_path(root: Path, source: str, symbol: str, ts: datetime) -> Path:
    if ts.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware UTC (point-in-time rule)")
    ts = ts.astimezone(UTC)
    return root / "raw" / source / symbol / f"{ts:%Y}" / f"{ts:%m}"


def write_partition(table: pa.Table, root: Path, source: str, symbol: str, ts: datetime) -> Path:
    if table.num_rows == 0:
        raise ValueError("refusing to write an empty partition")
    folder = partition_path(root, source, symbol, ts)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "part-0.parquet"
    pq.write_table(table, path)
    return path


def read_partition(path: Path) -> pa.Table:
    con = duckdb.connect()
    try:
        return con.execute("SELECT * FROM read_parquet(?)", [str(path)]).to_arrow_table()
    finally:
        con.close()


def day_path(root: Path, source: str, ts: datetime) -> Path:
    """Day file of an append-only forward recorder: data/raw/<source>/<yyyy-mm-dd>.parquet (UTC day)."""
    if ts.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware UTC (point-in-time rule)")
    return root / "raw" / source / f"{ts.astimezone(UTC):%Y-%m-%d}.parquet"


def write_atomic(table: pa.Table, path: Path) -> Path:
    """Write to a dot-tmp name outside the *.parquet glob, then rename (a reader never sees half a file)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.stem}.tmp")
    pq.write_table(table, tmp)
    os.replace(tmp, path)
    return path


def append_new(table: pa.Table, root: Path, source: str, key: str, ts: datetime) -> int:
    """Append rows whose `key` is in no earlier file of `source` (nor earlier in `table`) to the day
    file of `ts`; returns the number of new rows. Earlier rows are never rewritten (first seen wins)."""
    path = day_path(root, source, ts)
    seen: set[str] = set()
    for old in sorted(path.parent.glob("*.parquet")):
        seen.update(pq.read_table(old, columns=[key]).column(key).to_pylist())
    keep = []
    for i, value in enumerate(table.column(key).to_pylist()):
        if value not in seen:
            seen.add(value)
            keep.append(i)
    if not keep:
        return 0
    new = table.take(keep)
    if path.exists():
        new = pa.concat_tables([pq.read_table(path), new.cast(pq.read_schema(path))])
    write_atomic(new, path)
    return len(keep)
