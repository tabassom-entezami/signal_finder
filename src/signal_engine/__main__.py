"""Command line entry: `python -m signal_engine --version`, `... fetch-candles`, `... record-books`,
`... fetch-ecb` and the forward recorders `... record-news`, `... record-markets` and `... record-cot`
(exit 1 when a source fails or returns no rows). Research commands come from an optional module."""

import argparse
import atexit
import importlib
import logging
import os
import subprocess  # noqa: S404 - read-only git call for the prediction line's code version
from datetime import UTC, date, datetime
from pathlib import Path
from types import ModuleType

from signal_engine import __version__
from signal_engine.data import pit
from signal_engine.data.bitvavo import fetch_daily, store_daily
from signal_engine.data.book_recorder import compact_books, record_books
from signal_engine.data.cot import record_cot
from signal_engine.data.ecb import fetch_ecb, store_ecb
from signal_engine.data.markets import record_markets
from signal_engine.data.news import GDELT_MAX_FILES, SOURCES, record_news
from signal_engine.log import setup_logging
from signal_engine.record import predlog
from signal_engine.settings import load_settings
from signal_engine.validation.m1_oos import PreconditionError, RealBarSource

research_cli: ModuleType | None
try:  # research commands live in a module the public copy does not ship
    research_cli = importlib.import_module("signal_engine.research_cli")
except ImportError:
    research_cli = None


def _write_pid(path: Path) -> None:
    """This process id, for liveness checks; removed again on a normal exit."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{os.getpid()}\n")
    atexit.register(lambda: path.unlink(missing_ok=True))


def _utc_datetime(value: str) -> datetime:
    """ISO date or datetime; a value without a zone is read as UTC."""
    ts = datetime.fromisoformat(value)
    return ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)


def _git_head() -> str:
    """The code version stamped on each prediction line; "unknown" outside a git checkout."""
    try:
        out = subprocess.run(  # noqa: S603 - fixed read-only git call
            ["git", "rev-parse", "HEAD"],  # noqa: S607
            capture_output=True, text=True, check=True, cwd=Path(__file__).resolve().parent,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return out.stdout.strip()


def _predict(root: Path, now: datetime, log: logging.Logger) -> int:
    """One on-demand run of the forward prediction log; exit 1 on missing data or a broken record."""
    try:
        prereg = pit.read_prereg_hash(pit.PREDLOG_SHA256_FILE)
        source = RealBarSource(
            root, prereg, now, purpose=pit.PREDLOG_PURPOSE, sha256_file=pit.PREDLOG_SHA256_FILE
        )
        written = predlog.predict(
            source.load(), root / "record", now, prereg_sha256=prereg, code=_git_head(),
            stamp_path=root / "ops" / predlog.STAMP,
        )
        total = predlog.verify(root / "record", root / "ops" / predlog.STAMP)
    except (OSError, PreconditionError, predlog.ChainError, ValueError) as exc:
        log.error("predict failed", extra={"error": repr(exc)})
        return 1
    kinds = [line["kind"] for line in written]
    log.info(
        "predict done",
        extra={
            "written": {k: kinds.count(k) for k in sorted(set(kinds))},
            "lines": total,
            "today": [{"symbol": x["symbol"], **x["decision"]} for x in written if x["kind"] == "prediction"],
        },
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="signal_engine")
    parser.add_argument("--version", action="store_true", help="log the version as JSON and exit")
    sub = parser.add_subparsers(dest="command")
    fetch = sub.add_parser("fetch-candles", help="fetch closed Bitvavo daily candles into Parquet")
    fetch.add_argument("--symbol", required=True, help="ccxt symbol, e.g. BTC/EUR")
    fetch.add_argument("--since", required=True, type=_utc_datetime, help="UTC start, e.g. 2020-01-01")
    fetch.add_argument("--data-dir", type=Path, default=None, help="data root (default: settings.data_dir)")
    books = sub.add_parser("record-books", help="record Bitvavo public order books and market status")
    books.add_argument("--symbols", required=True, type=lambda v: v.split(","), help="e.g. BTC/EUR,ETH/USDC")
    books.add_argument("--interval", type=float, default=5.0, help="seconds between snapshots (default 5)")
    books.add_argument("--start", required=True, type=_utc_datetime, help="UTC, e.g. 2026-10-18T02:30:00Z")
    books.add_argument("--end", type=_utc_datetime, default=None,
                       help="UTC, e.g. 2026-10-18T06:00:00Z (default: record until stopped)")
    books.add_argument("--data-dir", type=Path, default=None, help="data root (default: settings.data_dir)")
    compact = sub.add_parser("compact-books", help="join each finished hour of recorded books into one file")
    compact.add_argument("--data-dir", type=Path, default=None, help="data root (default: settings.data_dir)")
    ecb_cmd = sub.add_parser("fetch-ecb", help="fetch ECB EUR/USD reference rates into Parquet")
    ecb_cmd.add_argument(
        "--start", required=True, type=date.fromisoformat, help="first fix date, e.g. 2019-03-01"
    )
    ecb_cmd.add_argument("--end", required=True, type=date.fromisoformat, help="last fix date (inclusive)")
    ecb_cmd.add_argument("--data-dir", type=Path, default=None, help="data root (default: settings.data_dir)")
    news = sub.add_parser("record-news", help="append new items from the green news/status/regulator feeds")
    news.add_argument(
        "--sources",
        type=lambda v: v.split(","),
        default=list(SOURCES),
        help=f"CSV subset of {','.join(SOURCES)}",
    )
    news.add_argument(
        "--gdelt-max-files",
        type=int,
        default=GDELT_MAX_FILES,
        help="GKG 15-min batches per run (catch-up cap)",
    )
    news.add_argument("--data-dir", type=Path, default=None, help="data root (default: settings.data_dir)")
    mkts = sub.add_parser("record-markets", help="daily Bitvavo /markets + /assets snapshot and diff")
    mkts.add_argument("--data-dir", type=Path, default=None, help="data root (default: settings.data_dir)")
    cot = sub.add_parser("record-cot", help="CFTC COT (TFF) for the CME bitcoin and ether futures")
    cot.add_argument("--data-dir", type=Path, default=None, help="data root (default: settings.data_dir)")
    pred = sub.add_parser("predict", help="append today's paper prediction, due outcomes and gap lines")
    pred.add_argument("--data-dir", type=Path, default=None, help="data root (default: settings.data_dir)")
    pred.add_argument("--now", type=_utc_datetime, default=None, help="UTC clock override (default: now)")
    if research_cli is not None:
        research_cli.add_commands(sub)
    args = parser.parse_args(argv)
    settings = load_settings()
    log = setup_logging(settings.log_level)
    if args.version:
        log.info("version", extra={"version": __version__})
    if args.command == "fetch-candles":
        table, gaps = fetch_daily(args.symbol, args.since)
        paths = store_daily(table, args.data_dir or settings.data_dir, args.symbol)
        ts = table.column("ts").to_pylist()
        log.info(
            "fetch-candles done",
            extra={
                "symbol": args.symbol,
                "rows": table.num_rows,
                "first": datetime.fromtimestamp(ts[0] / 1000, UTC).isoformat() if ts else None,
                "last": datetime.fromtimestamp(ts[-1] / 1000, UTC).isoformat() if ts else None,
                "gap_count": len(gaps),
                "files": [str(p) for p in paths],
            },
        )
    if args.command == "record-books":
        root = args.data_dir or settings.data_dir
        _write_pid(root / "ops" / "record-books.pid")  # the heartbeat checks it without /bin/ps
        record_books(args.symbols, args.interval, args.start, args.end, root)
    if args.command == "compact-books":
        joined = compact_books(args.data_dir or settings.data_dir, datetime.now(UTC))
        log.info("compact-books done", extra={"hours": len(joined), "files": [str(p) for p in joined]})
    if args.command == "fetch-ecb":
        fixes = fetch_ecb(args.start, args.end)
        paths = store_ecb(fixes, args.data_dir or settings.data_dir)
        log.info("fetch-ecb done", extra={"rows": fixes.num_rows, "files": [str(p) for p in paths]})
    if args.command == "record-news":
        result = record_news(
            args.data_dir or settings.data_dir, args.sources, gdelt_max_files=args.gdelt_max_files
        )
        return 1 if any("error" in r for r in result.values()) else 0
    if args.command in ("record-markets", "record-cot"):
        recorder = record_markets if args.command == "record-markets" else record_cot
        try:
            recorder(args.data_dir or settings.data_dir)
        except Exception as exc:  # HTTP error, empty answer, malformed payload: exit 1 for the scheduler
            log.error(f"{args.command} failed", extra={"error": repr(exc)})
            return 1
    if args.command == "predict":
        return _predict(args.data_dir or settings.data_dir, args.now or datetime.now(UTC), log)
    if research_cli is not None:
        code: int | None = research_cli.run(args, settings, log)
        if code is not None:
            return code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
