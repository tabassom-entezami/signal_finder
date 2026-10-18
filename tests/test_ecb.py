import logging
import urllib.error
from datetime import UTC, date, datetime
from email.message import Message
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest

from signal_engine.__main__ import main
from signal_engine.data import http
from signal_engine.data.ecb import DAY_MS, fetch_ecb, last_fix, parse_csv, store_ecb
from signal_engine.storage import read_partition

HEADER = "KEY,FREQ,CURRENCY,CURRENCY_DENOM,EXR_TYPE,EXR_SUFFIX,TIME_PERIOD,OBS_VALUE,OBS_STATUS,TITLE\n"
NOW = datetime(2026, 10, 16, 12, tzinfo=UTC)
NOW_MS = int(NOW.timestamp() * 1000)


def _csv(fixes: dict[str, str]) -> bytes:
    rows = "".join(
        f'EXR.D.USD.EUR.SP00.A,D,USD,EUR,SP00,A,{d},{v},A,"US dollar/Euro"\n' for d, v in fixes.items()
    )
    return (HEADER + rows).encode()


def _ms(day: str) -> int:
    return int(datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp() * 1000)


# Real calendar: 2019-03-08 Fri, 03-11 Mon; Easter 2019: 04-19 Good Friday and 04-22 Easter Monday
# have no fix (TARGET closed), so the 04-18 fix serves 04-19 .. 04-22.
FIXES = parse_csv(
    _csv({"2019-03-08": "1.1214", "2019-03-11": "1.1243", "2019-04-18": "1.125", "2019-04-23": "1.1245"}),
    NOW_MS,
)


def test_parse_csv_rows_types_and_publication_time() -> None:
    table = parse_csv(_csv({"2019-04-23": "1.1245", "2019-04-17": "1.1301", "2019-04-18": "NaN"}), NOW_MS)
    rows = table.to_pylist()
    assert [r["ts"] for r in rows] == [_ms("2019-04-17"), _ms("2019-04-23")]  # sorted, NaN skipped
    assert rows[0] == {
        "ts": _ms("2019-04-17"),
        "rate": 1.1301,
        "close_time": _ms("2019-04-17") + 16 * 3_600_000,
        "first_available_time": NOW_MS,
        "ingested_at": NOW_MS,
        "source": "ecb",
    }
    assert rows[0]["close_time"] < rows[0]["ts"] + DAY_MS  # available before the bar of its day closes


def test_wrong_series_refused() -> None:
    with pytest.raises(ValueError, match="unexpected ECB series"):
        parse_csv((HEADER + "EXR.D.GBP.EUR.SP00.A,D,GBP,EUR,SP00,A,2019-04-17,0.86,A,x\n").encode(), NOW_MS)


def test_last_fix_weekend_uses_friday_and_weekday_uses_same_day() -> None:
    days = [_ms(d) for d in ("2019-03-08", "2019-03-09", "2019-03-10", "2019-03-11")]
    assert last_fix(FIXES, days) == [1.1214, 1.1214, 1.1214, 1.1243]


def test_last_fix_easter_holidays_use_maundy_thursday() -> None:
    days = [_ms(f"2019-04-{d}") for d in (18, 19, 20, 21, 22, 23)]
    assert last_fix(FIXES, days) == [1.125, 1.125, 1.125, 1.125, 1.125, 1.1245]


def test_last_fix_never_uses_a_later_fix_or_a_stale_one() -> None:
    # before the first fix: None (not the 03-08 rate); 03-17 is 6 days after 03-11: a data gap
    assert last_fix(FIXES, [_ms("2019-03-07"), _ms("2019-03-16"), _ms("2019-03-17")]) == [None, 1.1243, None]


def test_fetch_ecb_url_and_404_is_empty() -> None:
    urls: list[str] = []

    def fake_get(url: str) -> bytes:
        urls.append(url)
        if "2030" in url:
            raise urllib.error.HTTPError(url, 404, "No results found", Message(), None)
        return _csv({"2019-03-01": "1.1383"})

    table = fetch_ecb(date(2019, 3, 1), date(2019, 4, 1), get=fake_get, now=NOW)
    assert urls == [
        "https://data-api.ecb.europa.eu/service/data/EXR/D.USD.EUR.SP00.A"
        "?startPeriod=2019-03-01&endPeriod=2019-04-01&format=csvdata"
    ]
    assert table.column("rate").to_pylist() == [1.1383]
    assert fetch_ecb(date(2030, 1, 1), date(2030, 1, 2), get=fake_get, now=NOW).num_rows == 0


def test_fetch_ecb_other_http_error_raised() -> None:
    def fake_get(url: str) -> bytes:
        raise urllib.error.HTTPError(url, 400, "Bad request", Message(), None)

    with pytest.raises(urllib.error.HTTPError):
        fetch_ecb(date(2019, 3, 1), date(2019, 3, 2), get=fake_get, now=NOW)


def test_store_ecb_keeps_stored_fix_and_logs_revision(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    [path] = store_ecb(parse_csv(_csv({"2019-03-08": "1.1214"}), NOW_MS), tmp_path)
    later = parse_csv(_csv({"2019-03-08": "1.2", "2019-03-11": "1.1243"}), NOW_MS + 1)
    with caplog.at_level(logging.WARNING):
        assert store_ecb(later, tmp_path) == [path]
    rows = read_partition(path).to_pylist()
    assert [(r["rate"], r["first_available_time"]) for r in rows] == [(1.1214, NOW_MS), (1.1243, NOW_MS + 1)]
    assert [r.message for r in caplog.records] == ["bar_revision"]
    assert path == tmp_path / "raw" / "ecb" / "EUR-USD" / "2019" / "03" / "part-0.parquet"


def test_store_ecb_empty_writes_nothing(tmp_path: Path) -> None:
    assert store_ecb(parse_csv(b"", NOW_MS), tmp_path) == []


def test_cli_fetch_ecb(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[date, date]] = []

    def fake_fetch(start: date, end: date) -> pa.Table:
        calls.append((start, end))
        return parse_csv(_csv({"2019-03-01": "1.1383"}), NOW_MS)

    monkeypatch.setattr("signal_engine.__main__.fetch_ecb", fake_fetch)
    argv = ["fetch-ecb", "--start", "2019-03-01", "--end", "2019-04-01", "--data-dir", str(tmp_path)]
    assert main(argv) == 0
    assert calls == [(date(2019, 3, 1), date(2019, 4, 1))]
    assert (tmp_path / "raw" / "ecb" / "EUR-USD" / "2019" / "03" / "part-0.parquet").exists()


class _Response:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def read(self) -> bytes:
        return self.body


def test_http_get_retries_network_errors_then_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(http, "_sleep", sleeps.append)
    attempts: list[str] = []

    def failing(request: Any, timeout: float) -> _Response:
        attempts.append(request.full_url)
        raise urllib.error.URLError("connection reset")

    monkeypatch.setattr("urllib.request.urlopen", failing)
    with pytest.raises(urllib.error.URLError):
        http.get("https://example.org/x")
    assert len(attempts) == 4 and sleeps == [1.0, 2.0, 4.0]


def test_http_get_4xx_not_retried_5xx_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(http, "_sleep", lambda s: None)
    answers: list[Any] = [
        urllib.error.HTTPError("u", 503, "busy", Message(), None),
        _Response(b"ok"),
        urllib.error.HTTPError("u", 404, "missing", Message(), None),
    ]

    def fake(request: Any, timeout: float) -> _Response:
        assert timeout == http.TIMEOUT_S
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer  # type: ignore[no-any-return]

    monkeypatch.setattr("urllib.request.urlopen", fake)
    assert http.get("https://example.org/x") == b"ok"
    with pytest.raises(urllib.error.HTTPError):
        http.get("https://example.org/x")
    assert answers == []


def test_http_get_https_only() -> None:
    with pytest.raises(ValueError, match="https only"):
        http.get("file:///etc/passwd")
