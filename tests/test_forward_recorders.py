import io
import json
import urllib.error
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import pytest

from signal_engine import __main__ as cli
from signal_engine.data import cot, markets, news

T0 = datetime(2026, 10, 17, 6, 30, tzinfo=UTC)
T0_MS = int(T0.timestamp() * 1000)

# Trimmed from the live feeds fetched 2026-10-17 (shapes and date formats as served).
COINBASE_RSS = b"""<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"
 xmlns:dc="http://purl.org/dc/elements/1.1/"><channel><title>Coinbase Status</title>
<item><title>Delayed withdrawals for SOL</title><link>https://status.coinbase.com/incidents/abc</link>
<pubDate>Fri, 16 Oct 2026 13:26:33 -0700</pubDate><dc:creator>Jane Operator</dc:creator></item>
<item><title>Scheduled maintenance</title><link>https://status.coinbase.com/incidents/def</link>
<pubDate>Thu, 15 Oct 2026 08:00:00 +0000</pubDate></item>
</channel></rss>"""
FSA_RSS = """<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>金融庁</title>
<item><title>暗号資産交換業者に対する行政処分について</title><link>https://www.fsa.go.jp/news/r8/x.html</link>
<pubDate>Fri, 16 Oct 2026 17:00:00 JST</pubDate></item></channel></rss>""".encode()
ESMA_RSS = b"""<?xml version="1.0" encoding="utf-8"?><rss version="2.0"><channel><title>ESMA</title>
<item><title>ESMA updates MAR guidelines</title>
<link>https://www.esma.europa.eu/press-news/x</link>
<description>&lt;time datetime="2026-10-16T16:14:51+02:00"&gt;16 Oct&lt;/time&gt;</description></item>
<item><title>No date at all</title><link>https://www.esma.europa.eu/press-news/y</link></item>
</channel></rss>"""
HACKS = json.dumps(
    [
        {  # a second hack of the same protocol on the same day (as Gamma, Merlin, OcelotDex in the live list)
            "date": 1647475200,
            "name": "ApeCoin",
            "classification": "Governance",
            "technique": "Flashloan",
            "amount": 5000,
            "defillamaId": "2665",
            "source": "",
        },
        {
            "date": 1647475200,
            "name": "ApeCoin",
            "classification": "Governance",
            "technique": "Flashloan",
            "amount": 820000,
            "chain": ["Ethereum"],
            "bridgeHack": False,
            "targetType": "Token",
            "source": "",
            "returnedFunds": None,
            "defillamaId": "2665",
            "language": None,
        },
        {
            "date": 1641081600,
            "name": "DefiChain",
            "classification": "Protocol Logic",
            "technique": "x",
            "amount": 1000,
            "chain": ["DeFiChain"],
            "bridgeHack": False,
            "targetType": "DeFi Protocol",
            "source": "https://example.org/defichain",
            "returnedFunds": None,
            "defillamaId": None,
        },
    ]
).encode()
SNAPSHOT = json.dumps(
    {
        "data": {
            "proposals": [
                {
                    "id": "0xa11",
                    "title": "Rotate Sentinel Addresses",
                    "space": {"id": "uniswapgovernance.eth"},
                    "state": "closed",
                    "created": 1789742751,
                    "start": 1789742751,
                    "end": 1790174751,
                    "author": "fake-author-id",
                },
            ]
        }
    }
).encode()
LATEST = datetime(2026, 10, 17, 0, 30, tzinfo=UTC)
LASTUPDATE = (
    b"68917 2b http://data.gdeltproject.org/gdeltv2/20261017003000.export.CSV.zip\n"
    b"3948986 c8 http://data.gdeltproject.org/gdeltv2/20261017003000.gkg.csv.zip\n"
)


def gkg_line(date: str, url: str, themes: str, persons: str = "John Doe", orgs: str = "") -> str:
    f = [""] * 27
    f[0], f[1], f[2], f[3], f[4] = f"{date}-1", date, "1", "example.com", url
    f[7], f[8], f[11], f[12], f[13] = themes, themes, persons, persons, orgs
    f[15] = "-1.5,3.2,4.7,7.9,20,0.5,300"
    f[23] = f"{persons},10"  # AllNames
    return "\t".join(f)


def gkg_zip(*lines: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("x.gkg.csv", "\n".join(lines))
    return buf.getvalue()


def gkg_file(ts: datetime) -> bytes:
    d = f"{ts:%Y%m%d%H%M%S}"
    return gkg_zip(
        gkg_line(d, f"https://news.example/{d}/bitcoin-etf-flows", "ECON_BITCOIN;WB_1"),
        gkg_line(d, f"https://screen.example/{d}/movies", "TAX_FNCACT_CRYPTOGRAPHER;LEADER"),  # no match
    )


def payloads() -> dict[str, Any]:
    p: dict[str, Any] = {url: b"<rss><channel></channel></rss>" for url, _, _ in news.FEEDS.values()}
    p[news.FEEDS["coinbase_status"][0]] = COINBASE_RSS
    p[news.FEEDS["fsa_japan"][0]] = FSA_RSS
    p[news.FEEDS["esma"][0]] = ESMA_RSS
    p["https://api.llama.fi/hacks"] = HACKS
    p[news.snapshot_url()] = SNAPSHOT
    p[news.GDELT_LAST] = LASTUPDATE
    for k in range(4):
        ts = LATEST - k * news.GDELT_STEP
        p[news.GDELT_FILE.format(ts)] = gkg_file(ts)
    return p


class FakeGet:
    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers = answers
        self.calls: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.calls.append(url)
        answer = self.answers.get(url)
        if answer is None:
            raise urllib.error.HTTPError(url, 404, "Not Found", None, None)  # type: ignore[arg-type]
        if isinstance(answer, Exception):
            raise answer
        assert isinstance(answer, bytes)
        return answer


def rows(root: Path, source: str) -> list[dict[str, Any]]:
    files = sorted((root / "raw" / source).glob("*.parquet"))
    return [r for f in files for r in pq.read_table(f).to_pylist()]


def run_news(root: Path, get: FakeGet, now: datetime = T0, sources: tuple[str, ...] = news.SOURCES) -> Any:
    return news.record_news(root, sources, get=get, clock=lambda: now, sleep=lambda s: None)


# --- record-news -----------------------------------------------------------------------------------


def test_rss_times_follow_the_publisher_and_drop_authors() -> None:
    got = news.parse_rss(COINBASE_RSS, "coinbase_status", "en", "incident", T0_MS)
    assert [r["published_at"] for r in got] == [
        int(datetime(2026, 10, 16, 20, 26, 33, tzinfo=UTC).timestamp() * 1000),
        int(datetime(2026, 10, 15, 8, tzinfo=UTC).timestamp() * 1000),
    ]
    assert all(r["known_at"] == r["published_at"] and r["known_at_type"] == "P" for r in got)
    assert "Jane" not in json.dumps(got)
    fsa = news.parse_rss(FSA_RSS, "fsa_japan", "ja", "regulatory", T0_MS)[0]
    assert fsa["published_at"] == int(datetime(2026, 10, 16, 8, tzinfo=UTC).timestamp() * 1000)  # 17:00 JST
    esma = news.parse_rss(ESMA_RSS, "esma", "en", "regulatory", T0_MS)
    assert esma[0]["published_at"] == int(datetime(2026, 10, 16, 14, 14, 51, tzinfo=UTC).timestamp() * 1000)
    assert (esma[1]["published_at"], esma[1]["known_at"], esma[1]["known_at_type"]) == (None, T0_MS, "O")


def test_a_publisher_time_after_our_fetch_is_capped_at_the_fetch() -> None:
    late = COINBASE_RSS.replace(b"Fri, 16 Oct 2026 13:26:33 -0700", b"Sat, 17 Oct 2026 09:00:00 +0000")
    row = news.parse_rss(late, "coinbase_status", "en", "incident", T0_MS)[0]
    assert row["known_at"] == T0_MS < row["published_at"]


def test_record_news_stores_every_source_with_its_known_at_rule(tmp_path: Path) -> None:
    result = run_news(tmp_path, all_feeds_ok())
    assert result["coinbase_status"] == {"fetched": 2, "new": 2}
    assert result["defillama_hacks"] == {"fetched": 3, "new": 3}
    assert result["gdelt"] == {"fetched": 1, "new": 1, "files": 1, "missing": 0}  # first run: latest only
    day = tmp_path / "raw/news/coinbase_status/2026-10-17.parquet"
    assert day.exists() and not list(day.parent.glob(".*tmp"))
    hacks = rows(tmp_path, "news/defillama_hacks")
    assert {(r["known_at_type"], r["known_at"]) for r in hacks} == {("E", T0_MS)}  # never the event day
    assert {r["url"] for r in hacks} == {None, "https://example.org/defichain"}
    snap = rows(tmp_path, "news/snapshot")[0]
    assert snap["known_at"] == 1789742751000 and "fake-author-id" not in json.dumps(snap)
    gdelt = rows(tmp_path, "news/gdelt")
    assert len(gdelt) == 1 and gdelt[0]["known_at_type"] == "O"
    assert gdelt[0]["known_at"] == int(LATEST.timestamp() * 1000) and gdelt[0]["title"] is None
    early = news.parse_gkg(gkg_file(LATEST), int(LATEST.timestamp() * 1000) - 60_000)[1][0]
    assert early["known_at"] == early["observed_at"] < early["published_at"]  # listed before its batch time
    assert "John" not in json.dumps(gdelt) and json.loads(gdelt[0]["extra"])["tone"] == -1.5
    assert (tmp_path / "raw/news/gdelt/.last_batch").read_text() == LATEST.isoformat()


def all_feeds_ok() -> FakeGet:
    """All feeds answer; the empty ones are the subject of another test, so give them one item each."""
    p = payloads()
    for source, (url, _, _) in news.FEEDS.items():
        if p[url].startswith(b"<rss><channel></channel>"):
            p[url] = COINBASE_RSS.replace(b"status.coinbase.com", f"{source}.example".encode())
    return FakeGet(p)


def test_second_run_appends_only_unseen_items_to_its_own_day(tmp_path: Path) -> None:
    run_news(tmp_path, all_feeds_ok(), sources=("coinbase_status",))
    more = all_feeds_ok()
    url = news.FEEDS["coinbase_status"][0]
    more.answers[url] = COINBASE_RSS.replace(
        b"</channel>",
        b"<item><title>New</title>"
        b"<link>https://status.coinbase.com/incidents/new</link>"
        b"<pubDate>Sat, 17 Oct 2026 01:00:00 +0000</pubDate></item></channel>",
    )
    result = run_news(tmp_path, more, now=T0 + timedelta(days=1), sources=("coinbase_status",))
    assert result["coinbase_status"] == {"fetched": 3, "new": 1}
    second = pq.read_table(tmp_path / "raw/news/coinbase_status/2026-10-18.parquet").to_pylist()
    assert [r["title"] for r in second] == ["New"]
    assert len(rows(tmp_path, "news/coinbase_status")) == 3


def test_a_failing_or_empty_feed_is_an_error_but_the_others_are_stored(tmp_path: Path) -> None:
    get = FakeGet(payloads())  # bitvavo/kraken/sec/afm answer an empty channel
    get.answers[news.FEEDS["sec"][0]] = urllib.error.HTTPError("u", 503, "x", None, None)  # type: ignore[arg-type]
    result = run_news(tmp_path, get)
    assert "503" in str(result["sec"]["error"])
    assert "0 items" in str(result["kraken_status"]["error"])
    assert result["coinbase_status"]["new"] == 2 and rows(tmp_path, "news/coinbase_status")


def test_gdelt_catches_up_missed_batches_skips_404_and_respects_the_cap(tmp_path: Path) -> None:
    state = tmp_path / "raw/news/gdelt/.last_batch"
    state.parent.mkdir(parents=True)
    state.write_text((LATEST - 4 * news.GDELT_STEP).isoformat())
    get = FakeGet(payloads())
    del get.answers[news.GDELT_FILE.format(LATEST - news.GDELT_STEP)]
    result = run_news(tmp_path, get, sources=("gdelt",))
    assert result["gdelt"] == {"fetched": 3, "new": 3, "files": 3, "missing": 1}
    assert state.read_text() == LATEST.isoformat()
    assert news.gdelt_batches(LATEST - 10 * news.GDELT_STEP, LATEST, cap=2) == [
        LATEST - news.GDELT_STEP,
        LATEST,
    ]


def test_gdelt_without_a_new_or_published_batch_is_a_skip_not_an_error(tmp_path: Path) -> None:
    get = FakeGet(payloads())
    run_news(tmp_path, get, sources=("gdelt",))
    again = run_news(tmp_path, get, sources=("gdelt",))  # lastupdate.txt still lists the stored batch
    assert again["gdelt"] == {"fetched": 0, "new": 0, "files": 0, "missing": 0}
    nxt = LATEST + news.GDELT_STEP
    get.answers[news.GDELT_LAST] = LASTUPDATE.replace(b"20261017003000", f"{nxt:%Y%m%d%H%M%S}".encode())
    late = run_news(tmp_path, get, sources=("gdelt",))  # listed, but the file still answers 404
    assert late["gdelt"] == {"fetched": 0, "new": 0, "files": 0, "missing": 1}
    assert (tmp_path / "raw/news/gdelt/.last_batch").read_text() == LATEST.isoformat()  # retried next run


def test_gdelt_empty_file_is_an_error(tmp_path: Path) -> None:
    get = FakeGet(payloads())
    get.answers[news.GDELT_FILE.format(LATEST)] = gkg_zip("")
    assert "empty GKG" in str(run_news(tmp_path, get, sources=("gdelt",))["gdelt"]["error"])
    assert not (tmp_path / "raw/news/gdelt/.last_batch").exists()


def test_legal_hold_sources_are_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="legal hold"):
        run_news(tmp_path, FakeGet({}), sources=("slowmist",))


def test_record_news_cli_exits_1_when_any_source_failed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: list[Any] = []

    def fake(root: Path, sources: list[str], gdelt_max_files: int) -> dict[str, dict[str, Any]]:
        seen.append((root, sources, gdelt_max_files))
        return {"sec": {"error": "HTTPError 503"}, "esma": {"fetched": 1, "new": 1}}

    monkeypatch.setattr(cli, "record_news", fake)
    assert cli.main(["record-news", "--sources", "sec,esma", "--data-dir", str(tmp_path)]) == 1
    assert seen == [(tmp_path, ["sec", "esma"], news.GDELT_MAX_FILES)]
    monkeypatch.setattr(cli, "record_news", lambda *a, **k: {"esma": {"fetched": 1, "new": 0}})
    assert cli.main(["record-news", "--data-dir", str(tmp_path)]) == 0


# --- record-markets --------------------------------------------------------------------------------


def market(name: str, status: str = "trading", min_quote: str = "5.00") -> dict[str, Any]:
    return {
        "market": name,
        "status": status,
        "base": name.split("-")[0],
        "quote": "EUR",
        "pricePrecision": None,
        "minOrderInBaseAsset": "371.27716",
        "minOrderInQuoteAsset": min_quote,
        "maxOrderInBaseAsset": "742554317.56212",
        "quantityDecimals": 5,
        "tickSize": "0.0000010",
    }


def asset(symbol: str, withdrawal: str = "OK") -> dict[str, Any]:
    return {
        "symbol": symbol,
        "name": symbol,
        "decimals": 8,
        "depositStatus": "OK",
        "withdrawalStatus": withdrawal,
        "withdrawalFee": "1.3",
        "networks": [symbol],
        "message": "",
    }


def market_get(mkts: list[dict[str, Any]], assets: list[dict[str, Any]]) -> FakeGet:
    return FakeGet(
        {
            f"{markets.API}/markets": json.dumps(mkts).encode(),
            f"{markets.API}/assets": json.dumps(assets).encode(),
        }
    )


def test_two_snapshots_diff_listings_delistings_status_and_fields(tmp_path: Path) -> None:
    day1 = market_get([market("A-EUR"), market("B-EUR")], [asset("A"), asset("B")])
    assert markets.record_markets(tmp_path, day1, lambda: T0, lambda s: None)["changes"] == -1
    day2 = market_get(
        [market("A-EUR", "halted", min_quote="10.00"), market("C-EUR")],
        [asset("A", "MAINTENANCE"), asset("B")],
    )
    t1 = T0 + timedelta(days=2)  # a skipped day: diff against the latest earlier file
    counts = markets.record_markets(tmp_path, day2, lambda: t1, lambda s: None)
    assert counts == {"markets": 2, "assets": 2, "changes": 5}
    folder = tmp_path / "raw/bitvavo_markets"
    snap = pq.read_table(folder / "2026-10-19.parquet").to_pylist()
    assert all("BaseAsset" not in r["fields"] for r in snap)  # price proxies dropped at fetch
    changes = pq.read_table(folder / "diffs/2026-10-19.parquet").to_pylist()
    got = {(c["kind"], c["key"], c["change"], c["field"], c["old"], c["new"]) for c in changes}
    assert got == {
        ("market", "C-EUR", "listed", None, None, "trading"),
        ("market", "B-EUR", "delisted", None, "trading", None),
        ("market", "A-EUR", "status", "status", "trading", "halted"),
        ("market", "A-EUR", "field", "minOrderInQuoteAsset", "5.00", "10.00"),
        ("asset", "A", "status", "status", "deposit=OK;withdrawal=OK", "deposit=OK;withdrawal=MAINTENANCE"),
    }
    assert {(c["known_at"], c["prev_known_at"], c["prev_date"]) for c in changes} == {
        (int(t1.timestamp() * 1000), T0_MS, "2026-10-17")
    }


def test_same_day_rerun_replaces_the_snapshot_and_diffs_the_earlier_day(tmp_path: Path) -> None:
    markets.record_markets(tmp_path, market_get([market("A-EUR")], [asset("A")]), lambda: T0, lambda s: None)
    t1 = T0 + timedelta(days=1)
    for _ in range(2):
        counts = markets.record_markets(
            tmp_path, market_get([market("A-EUR"), market("B-EUR")], [asset("A")]), lambda: t1, lambda s: None
        )
    assert counts["changes"] == 1  # vs 2026-10-17, not vs its own first run
    assert len(list((tmp_path / "raw/bitvavo_markets").glob("*.parquet"))) == 2


def test_empty_markets_answer_raises_and_cli_exits_1(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="empty answer"):
        markets.record_markets(tmp_path, market_get([], [asset("A")]), lambda: T0, lambda s: None)
    assert not list(tmp_path.rglob("*.parquet"))

    def boom(root: Path) -> None:
        raise urllib.error.URLError("down")

    monkeypatch.setattr(cli, "record_markets", boom)
    assert cli.main(["record-markets", "--data-dir", str(tmp_path)]) == 1


# --- record-cot ------------------------------------------------------------------------------------


def cot_row(code: str, report: str, created: str, lev_long: str = "100") -> dict[str, str]:
    row = {f: "1" for f in cot.POSITIONS.values()}
    row |= {
        "cftc_contract_market_code": code,
        "report_date_as_yyyy_mm_dd": f"{report}T00:00:00.000",
        ":created_at": created,
        "lev_money_positions_long": lev_long,
        "lev_money_positions_short": "40",
    }
    return row


def cot_get(*rows_: dict[str, str]) -> FakeGet:
    return FakeGet({cot.query_url(): json.dumps(list(rows_)).encode()})


def test_cot_known_at_is_the_release_time_and_rows_are_stored_once(tmp_path: Path) -> None:
    week = cot_row("133741", "2026-10-13", "2026-10-16T19:30:08.360Z")
    bulk = cot_row("146021", "2021-03-30", "2022-09-12T16:00:00.000Z")  # loaded long after the report
    assert cot.record_cot(tmp_path, cot_get(week, bulk), lambda: T0) == {"fetched": 2, "new": 2}
    stored = {r["market_code"]: r for r in rows(tmp_path, "cot")}
    btc = stored["133741"]
    assert (
        btc["known_at"]
        == btc["release_at"]
        == int(datetime(2026, 10, 16, 19, 30, 8, 360000, tzinfo=UTC).timestamp() * 1000)
    )
    assert btc["known_at_type"] == "P" and btc["lev_money_long"] == 100 and btc["market"] == "BITCOIN"
    assert stored["146021"]["known_at_type"] == "A"
    revised = cot_row("133741", "2026-10-13", "2026-10-16T19:30:08.360Z", lev_long="999")
    nxt = cot_row("133741", "2026-10-20", "2026-10-23T19:30:05.000Z")
    assert cot.record_cot(tmp_path, cot_get(revised, nxt), lambda: T0 + timedelta(days=7)) == {
        "fetched": 2,
        "new": 1,
    }
    assert [r["lev_money_long"] for r in rows(tmp_path, "cot") if r["key"] == "133741|2026-10-13"] == [100]


def test_cot_refuses_a_release_before_the_report_and_an_empty_answer(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="release before"):
        cot.record_cot(tmp_path, cot_get(cot_row("133741", "2026-10-13", "2026-10-12T00:00:00Z")), lambda: T0)
    with pytest.raises(ValueError, match="0 COT rows"):
        cot.record_cot(tmp_path, cot_get(), lambda: T0)


def test_record_cot_cli_exits_1_on_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def boom(root: Path) -> None:
        raise urllib.error.HTTPError("u", 500, "x", None, None)  # type: ignore[arg-type]

    monkeypatch.setattr(cli, "record_cot", boom)
    assert cli.main(["record-cot", "--data-dir", str(tmp_path)]) == 1
    monkeypatch.setattr(cli, "record_cot", lambda root: {"fetched": 1, "new": 0})
    assert cli.main(["record-cot", "--data-dir", str(tmp_path)]) == 0


# --- research DB views -----------------------------------------------------------------------------


def test_research_db_views_read_the_recorder_files(tmp_path: Path) -> None:
    rdb = pytest.importorskip("signal_engine.research_db")  # private module: absent in the public copy
    data = tmp_path / "data"
    run_news(data, all_feeds_ok(), sources=("coinbase_status", "gdelt"))
    markets.record_markets(data, market_get([market("A-EUR")], [asset("A")]), lambda: T0, lambda s: None)
    t1 = T0 + timedelta(days=1)
    markets.record_markets(data, market_get([market("B-EUR")], [asset("A")]), lambda: t1, lambda s: None)
    cot.record_cot(data, cot_get(cot_row("133741", "2026-10-13", "2026-10-16T19:30:08Z")), lambda: T0)
    db = rdb.build(tmp_path)

    def q(query: str) -> list[tuple[Any, ...]]:
        rows: list[tuple[Any, ...]] = rdb.sql(db, query)[1]
        return rows

    assert q("SELECT source, count(*) FROM market.news GROUP BY 1 ORDER BY 1") == [
        ("coinbase_status", 2),
        ("gdelt", 1),
    ]
    assert q("SELECT count(*) FROM market.bitvavo_markets") == [(4,)]
    assert q("SELECT key, change FROM market.bitvavo_market_changes ORDER BY 1") == [
        ("A-EUR", "delisted"),
        ("B-EUR", "listed"),
    ]
    assert q("SELECT lev_money_net, known_at_type FROM market.cot") == [(60, "P")]


def test_research_db_forward_views_exist_on_an_empty_root(tmp_path: Path) -> None:
    rdb = pytest.importorskip("signal_engine.research_db")  # private module: absent in the public copy
    counts = dict(rdb.info(rdb.build(tmp_path)))
    for view in ("market.news", "market.bitvavo_markets", "market.bitvavo_market_changes", "market.cot"):
        assert counts[view] == 0
