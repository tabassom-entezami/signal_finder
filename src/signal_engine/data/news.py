"""Forward news-event recorder (`record-news`): one run pulls each green feed and appends the items it
has not stored before to data/raw/news/<source>/<yyyy-mm-dd>.parquet (UTC day of the fetch).

Every row carries the publisher time (`published_at`), our fetch time (`observed_at`), the time a
backtest may use (`known_at`) and its type (`known_at_type`):
- P publisher timestamp: known_at = min(published_at, observed_at); a P feed item without a date is O;
- O our observation: GDELT batch time (`DATE`, capped at observed_at: the file is listed before its
  nominal batch time), or observed_at;
- E event day only (DefiLlama hacks): known_at = observed_at, never the event day.
Timestamps are int64 ms UTC. No author, username, avatar or address field is stored (Snapshot
`author`, RSS `dc:creator` and GDELT persons are dropped at fetch); GDELT rows keep only date, URL,
domain, themes and tone (no text). No price field is stored.

Terms and robots checked 2026-10-17:
- status.bitvavo.com/feed.rss: robots 404 (no rules); status.coinbase.com and status.kraken.com:
  robots disallow /api/ and /embed/ only, so history.rss (not the JSON API) is used;
- sec.gov: robots allows /news/; afm.nl: disallows /sitecore only; esma.europa.eu legal notice:
  reproduction authorised with the source acknowledged; fsa.go.jp: robots 404;
- api.llama.fi: robots 404, DefiLlama API terms; hub.snapshot.org: no disallow,
  free with a per-minute limit; data.gdeltproject.org: robots 404, "unlimited and unrestricted use".
- coinpost.jp/?feed=rss2: robots does not block the feed; its terms allow private use only, so only
  metadata is stored (title, URL, time).
Not fetched: sources whose terms forbid automated use, that need an account key, or that are HTML pages
(DefiLlama hacks is the primary hack source).
"""

import email.utils
import hashlib
import io
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET  # noqa: S405 (stdlib expat: no external entities, entity-expansion guard)
import zipfile
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pyarrow as pa

from signal_engine.data import http
from signal_engine.storage import append_new

SOURCE = "news"
REQUEST_GAP_S = 1.0  # polite spacing between requests
GDELT_STEP = timedelta(minutes=15)
GDELT_MAX_FILES = 96  # one day of 15-minute batches per run at most
GDELT_LAST = "https://data.gdeltproject.org/gdeltv2/lastupdate.txt"
GDELT_FILE = "https://data.gdeltproject.org/gdeltv2/{:%Y%m%d%H%M%S}.gkg.csv.zip"
# Word-bounded, so "CRYPTOGRAPHER" or "bitcoinlike" do not match; checked on URL, themes, organisations.
CRYPTO_RE = re.compile(
    r"\b(bitcoin|btc|ethereum|crypto|cryptocurrenc(?:y|ies)|stablecoins?|blockchain|defi|"
    r"binance|coinbase|bitvavo|econ_bitcoin|econ_cryptocurrency)\b",
    re.I,
)
SNAPSHOT_URL = "https://hub.snapshot.org/graphql"
SNAPSHOT_SPACES = (
    "aave.eth",
    "uniswapgovernance.eth",
    "arbitrumfoundation.eth",
    "lido-snapshot.eth",
    "ens.eth",
    "safe.eth",
    "cow.eth",
    "balancer.eth",
    "opcollective.eth",
    "gitcoindao.eth",
)
LEGAL_HOLD = ("cryptopanic", "upbit", "naver", "slowmist", "rekt")  # not fetched: terms, key or HTML only

# source_id: (url, language, event_type)
FEEDS: dict[str, tuple[str, str, str]] = {
    "bitvavo_status": ("https://status.bitvavo.com/feed.rss", "en", "incident"),
    "coinbase_status": ("https://status.coinbase.com/history.rss", "en", "incident"),
    "kraken_status": ("https://status.kraken.com/history.rss", "en", "incident"),
    "sec": ("https://www.sec.gov/news/pressreleases.rss", "en", "regulatory"),
    "afm": ("https://www.afm.nl/nl-nl/rss-feed/nieuws-professionals", "nl", "regulatory"),
    "esma": ("https://www.esma.europa.eu/rss.xml", "en", "regulatory"),
    "fsa_japan": ("https://www.fsa.go.jp/fsaNewsListAll_rss2.xml", "ja", "regulatory"),
    # Private use only (its terms): metadata only, title, URL, time; no body, no author.
    "coinpost": ("https://coinpost.jp/?feed=rss2", "ja", "other"),
}
SOURCES = (*FEEDS, "defillama_hacks", "snapshot", "gdelt")

SCHEMA = pa.schema(
    [
        ("id", pa.string()),  # sha256(url | published_at), the dedupe key
        ("source", pa.string()),
        ("language", pa.string()),
        ("url", pa.string()),
        ("title", pa.string()),  # null for GDELT (no text stored)
        ("published_at", pa.int64()),  # publisher time, ms UTC; null if the item has none
        ("observed_at", pa.int64()),  # our fetch time, ms UTC
        ("known_at", pa.int64()),  # time a backtest may use, ms UTC
        ("known_at_type", pa.string()),  # P | O | E
        ("event_type", pa.string()),  # incident | regulatory | hack | governance | other
        ("extra", pa.string()),  # source-specific fields as JSON (no personal data)
    ]
)
DC_DATE = "{http://purl.org/dc/elements/1.1/}date"
TIME_ATTR = re.compile(r'datetime="([^"]+)"')  # ESMA puts the date only in the description

log = logging.getLogger(__name__)


def _ms(ts: datetime) -> int:
    return int(ts.timestamp() * 1000)


def _key(url: str, published_at: int | None) -> str:
    return hashlib.sha256(f"{url}|{published_at}".encode()).hexdigest()


def _row(
    source: str,
    language: str,
    url: str,
    title: str | None,
    published_at: int | None,
    observed_at: int,
    event_type: str,
    *,
    kind: str = "P",
    known_at: int | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if kind == "P" and published_at is None:
        kind = "O"
    if known_at is None:
        known_at = min(published_at, observed_at) if kind == "P" and published_at is not None else observed_at
    return {
        "id": _key(url, published_at),
        "source": source,
        "language": language,
        "url": url,
        "title": title,
        "published_at": published_at,
        "observed_at": observed_at,
        "known_at": known_at,
        "known_at_type": kind,
        "event_type": event_type,
        "extra": json.dumps(extra, sort_keys=True) if extra else None,
    }


def _rss_date(text: str | None) -> int | None:
    """RFC 822 (`pubDate`, JST spelled out by FSA Japan) or ISO 8601 (`dc:date`, ESMA `datetime=`)."""
    if not text or not text.strip():
        return None
    text = text.strip()
    try:
        ts = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            ts = email.utils.parsedate_to_datetime(re.sub(r"\bJST$", "+0900", text))
        except (TypeError, ValueError):
            return None
    return _ms(ts) if ts.tzinfo is not None else None  # a zone-less time is not point-in-time


def parse_rss(
    body: bytes, source: str, language: str, event_type: str, observed_at: int
) -> list[dict[str, Any]]:
    root = ET.fromstring(body)  # noqa: S314 (public feed; expat resolves no external entities)
    rows = []
    for item in root.iter("item"):
        url = (item.findtext("link") or item.findtext("guid") or "").strip()
        if not url:
            continue
        description = item.findtext("description") or ""
        stamp = item.findtext("pubDate") or item.findtext(DC_DATE)
        if not stamp and (m := TIME_ATTR.search(description)):
            stamp = m.group(1)
        title = (item.findtext("title") or "").strip() or None
        rows.append(_row(source, language, url, title, _rss_date(stamp), observed_at, event_type))
    return rows


def parse_hacks(body: bytes, observed_at: int) -> list[dict[str, Any]]:
    rows = []
    for hack in json.loads(body):
        day = int(hack["date"]) * 1000
        name = str(hack.get("name") or "")
        url = hack.get("source") or ""
        extra = {
            k: hack.get(k)
            for k in ("classification", "technique", "amount", "chain", "bridgeHack", "targetType")
            + ("returnedFunds", "defillamaId")
        }
        row = _row("defillama_hacks", "en", url, name, day, observed_at, "hack", kind="E", extra=extra)
        row["url"] = url or None
        # The source URL is often empty, and one protocol can have two hacks on one day (measured
        # 2026-10-17: Gamma, Merlin, OcelotDex), so the key spans the descriptive fields.
        ident = "|".join(str(extra[k]) for k in ("defillamaId", "classification", "technique", "amount"))
        row["id"] = _key(f"defillama_hacks|{name}|{ident}", day)
        rows.append(row)
    return rows


def snapshot_url(spaces: Sequence[str] = SNAPSHOT_SPACES, first: int = 100) -> str:
    space_list = ",".join(f'"{s}"' for s in spaces)
    query = (
        f'{{proposals(first:{first},where:{{space_in:[{space_list}]}},orderBy:"created",'
        "orderDirection:desc){id title space{id} state created start end}}"
    )
    return f"{SNAPSHOT_URL}?{urllib.parse.urlencode({'query': query})}"


def parse_snapshot(body: bytes, observed_at: int) -> list[dict[str, Any]]:
    payload = json.loads(body)
    if payload.get("errors"):
        raise ValueError(f"snapshot errors: {payload['errors']}")
    rows = []
    for p in payload["data"]["proposals"]:  # `author` (a signer address) is never requested
        space = p["space"]["id"]
        url = f"https://snapshot.org/#/{space}/proposal/{p['id']}"
        extra = {"space": space, "state": p.get("state"), "start": p.get("start"), "end": p.get("end")}
        rows.append(
            _row(
                "snapshot",
                "en",
                url,
                p.get("title"),
                int(p["created"]) * 1000,
                observed_at,
                "governance",
                extra=extra,
            )
        )
    return rows


def gdelt_latest(body: bytes) -> datetime:
    name = next(u for u in body.decode().split() if u.endswith(".gkg.csv.zip")).rsplit("/", 1)[1]
    return datetime.strptime(name[:14], "%Y%m%d%H%M%S").replace(tzinfo=UTC)


def gdelt_batches(last: datetime | None, latest: datetime, cap: int = GDELT_MAX_FILES) -> list[datetime]:
    """15-minute batches after the last processed one up to `latest` (the newest `cap`); first run: latest."""
    if last is None:
        return [latest]
    slots = []
    ts = last + GDELT_STEP
    while ts <= latest:
        slots.append(ts)
        ts += GDELT_STEP
    return slots[-cap:]


def parse_gkg(body: bytes, observed_at: int) -> tuple[int, list[dict[str, Any]]]:
    """(rows in the file, crypto-matching rows). Kept: DATE, URL, domain, V1 themes, tone. Persons,
    names, quotations and image fields are never read into a row."""
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        text = z.read(z.namelist()[0]).decode("utf-8", "replace")
    total, rows = 0, []
    for line in text.splitlines():
        f = line.split("\t")
        if len(f) < 16:
            continue
        total += 1
        url, themes, orgs = f[4], f[7], f[13]
        if not CRYPTO_RE.search(f"{url} {themes.replace(';', ' ')} {orgs.replace(';', ' ')}"):
            continue
        batch = _ms(datetime.strptime(f[1][:14], "%Y%m%d%H%M%S").replace(tzinfo=UTC))
        tone = f[15].split(",")[0]
        extra = {"domain": f[3], "themes": themes, "tone": float(tone) if tone else None}
        rows.append(
            _row(
                "gdelt",
                "en",
                url,
                None,
                batch,
                observed_at,
                "other",
                kind="O",
                known_at=min(batch, observed_at),
                extra=extra,
            )
        )
    return total, rows


def _table(rows: list[dict[str, Any]]) -> pa.Table:
    return pa.Table.from_pylist(rows, schema=SCHEMA)


def _gdelt_state(root: Path) -> Path:
    return root / "raw" / SOURCE / "gdelt" / ".last_batch"


def _record_gdelt(
    root: Path, get: http.HttpGet, now: Callable[[], datetime], sleep: Callable[[float], None], cap: int
) -> dict[str, int]:
    state = _gdelt_state(root)
    last = datetime.fromisoformat(state.read_text().strip()) if state.exists() else None
    batches = gdelt_batches(last, gdelt_latest(get(GDELT_LAST)), cap)
    counts = {"fetched": 0, "new": 0, "files": 0, "missing": 0}
    for ts in batches:
        sleep(REQUEST_GAP_S)
        try:
            body = get(GDELT_FILE.format(ts))
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
            counts["missing"] += 1  # GDELT skips a batch now and then
            continue
        total, rows = parse_gkg(body, _ms(now()))
        if total == 0:
            raise ValueError(f"empty GKG file {ts:%Y%m%d%H%M%S}")
        counts["files"] += 1
        counts["fetched"] += len(rows)
        counts["new"] += append_new(_table(rows), root, f"{SOURCE}/gdelt", "id", now()) if rows else 0
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(ts.isoformat())  # only after the batch is stored
    if counts["files"] == 0:  # no batch newer than the stored one yet, or not published yet (404): a skip
        log.info("gdelt: no new batch", extra={"last": last.isoformat() if last else None, **counts})
    return counts


def record_news(
    root: Path,
    sources: Sequence[str] = SOURCES,
    get: http.HttpGet = http.get,
    clock: Callable[[], datetime] | None = None,
    sleep: Callable[[float], None] | None = None,
    gdelt_max_files: int = GDELT_MAX_FILES,
) -> dict[str, dict[str, int | str]]:
    """Fetch each source; a failing source is logged and counted, the others still run.
    Returns per-source counts; a source with key `error` failed (0 items fetched is a failure)."""
    clock = clock or (lambda: datetime.now(UTC))
    sleep = sleep or time.sleep
    unknown = [s for s in sources if s not in SOURCES]
    if unknown:
        raise ValueError(f"unknown sources {unknown}; legal hold: {list(LEGAL_HOLD)}")
    log.info("legal hold, not fetched", extra={"sources": list(LEGAL_HOLD)})
    result: dict[str, dict[str, int | str]] = {}
    for source in sources:
        try:
            if source == "gdelt":
                result[source] = dict(_record_gdelt(root, get, clock, sleep, gdelt_max_files))
                continue
            sleep(REQUEST_GAP_S)
            observed = _ms(clock())
            if source in FEEDS:
                url, language, event_type = FEEDS[source]
                rows = parse_rss(get(url), source, language, event_type, observed)
            elif source == "defillama_hacks":
                rows = parse_hacks(get("https://api.llama.fi/hacks"), observed)
            else:
                rows = parse_snapshot(get(snapshot_url()), observed)
            if not rows:
                raise ValueError("0 items fetched")
            new = append_new(_table(rows), root, f"{SOURCE}/{source}", "id", clock())
            result[source] = {"fetched": len(rows), "new": new}
        except Exception as exc:  # one dead feed must not lose the others
            result[source] = {"error": repr(exc)}
            log.error("record-news source failed", extra={"source": source, "error": repr(exc)})
    log.info("record-news done", extra={"sources": result})
    return result
