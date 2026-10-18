from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pytest

from signal_engine.storage import partition_path, read_partition, write_partition

TS = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)


def _table() -> pa.Table:
    return pa.table({"ts": [1, 2, 3], "close": [100.0, 101.5, 99.25]})


def test_round_trip(tmp_path: Path) -> None:
    path = write_partition(_table(), tmp_path, "bitvavo", "BTC-EUR", TS)
    assert path.parent == tmp_path / "raw" / "bitvavo" / "BTC-EUR" / "2026" / "10"
    assert read_partition(path).equals(_table())


def test_empty_table_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="empty"):
        write_partition(_table().slice(0, 0), tmp_path, "bitvavo", "BTC-EUR", TS)


def test_naive_timestamp_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="UTC"):
        partition_path(tmp_path, "bitvavo", "BTC-EUR", datetime(2026, 10, 15))
