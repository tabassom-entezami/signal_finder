"""Research commands of the command line: the one-shot `m1-oos` run, the `records` store and, with the
private modules below, `fetch-binance-vision` and the records imports. `__main__` registers these commands
only when this module is present.
"""

import argparse
import importlib
import json
import logging
import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path
from types import ModuleType
from typing import Any

from signal_engine import records
from signal_engine.settings import Settings
from signal_engine.validation.m1_oos import (
    PREREG_SHA256,
    CodeDefectError,
    PreconditionError,
    RealBarSource,
    reproduce_dev,
    run_m1_oos,
)

binance_vision: ModuleType | None
try:  # Private module, not in the public repo: create your own `signal_engine/data/binance_vision.py`
    # providing WARMUP_END and download_range(symbol, since, until, root, prereg_hash) to enable
    # `fetch-binance-vision`
    binance_vision = importlib.import_module("signal_engine.data.binance_vision")
except ImportError:
    binance_vision = None
importers: list[ModuleType]
try:  # Private modules, not in the public repo: create your own `signal_engine/records/{importers,
    # import_docs,import_traders,import_raw}.py`, each providing SOURCES (name -> loader) and a
    # check_*_against_files(con) function, to enable `records import`, `records sync` and
    # `records check --against-files`
    importers = [
        importlib.import_module(f"signal_engine.records.{name}")
        for name in ("importers", "import_docs", "import_traders", "import_raw")
    ]
except ImportError:
    importers = []
SOURCES: dict[str, Any] = {k: v for module in importers for k, v in module.SOURCES.items()}
# `records sync` order: preregistrations before the gate, the ledger before verification, platforms before the
# trader tables (an unchanged file is a no-op)
SYNC_ORDER = (
    "preregistrations",
    "gate",
    "agent-runs",
    "lab-trials",
    "withdrawn-trials",
    "forecasts",  # after agent-runs: a forecast may name its agent run
    "decisions",
    "gaps",
    "ledger",
    "verification",
    "risks",
    "papers",
    "registry",
    "platforms",
    "watchlist",
    "cohorts",
    "snapshots",
    "raw",
)


def add_commands(sub: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    if binance_vision is not None:
        warmup_end = binance_vision.WARMUP_END
        bv = sub.add_parser("fetch-binance-vision", help="download Binance Vision 1d spot klines (research)")
        bv.add_argument("--symbol", required=True, help="Binance symbol, e.g. BTCUSDT")
        bv.add_argument("--since", required=True, type=date.fromisoformat, help="first month, e.g. 2017-08")
        bv.add_argument(
            "--until", type=date.fromisoformat, default=warmup_end, help=f"last day (default {warmup_end})"
        )
        bv.add_argument("--prereg-hash", default=None, help="rules-file SHA-256, for --until after warm-up")
        bv.add_argument("--data-dir", type=Path, default=None, help="data root (default: settings.data_dir)")
    oos = sub.add_parser("m1-oos", help="one-shot OOS run of the momentum rule (refuses unless ready)")
    oos.add_argument("--prereg", required=True, type=Path, help="the frozen rules file (Markdown)")
    oos.add_argument("--data-dir", required=True, type=Path, help="data root, e.g. data")
    oos.add_argument("--seeds", required=True, type=int, nargs="+", help="Monte Carlo seeds, e.g. 1 2")
    oos.add_argument("--out", required=True, type=Path, help="result JSON path")
    oos.add_argument("--report", required=True, type=Path, help="report Markdown path")
    dev = sub.add_parser("reproduce-dev", help="the whole M1 path on synthetic bars, with a result hash")
    dev.add_argument("--seed", type=int, default=7, help="synthetic market seed (default 7)")
    dev.add_argument("--out", required=True, type=Path, help="result JSON path")
    _add_records(sub)


def _add_records(sub: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    rec = sub.add_parser("records", help="research records store (data/research.sqlite, append-only)")
    rec.add_argument("--db", type=Path, default=records.DB, help=f"store file (default {records.DB})")
    cmd = rec.add_subparsers(dest="records_command", required=True)
    cmd.add_parser("init", help="create the store or apply new migrations")
    app = cmd.add_parser(
        "append", help="append one row (a correction names the row it replaces in supersedes)"
    )
    app.add_argument("table")
    app.add_argument("--json", required=True, dest="row", type=json.loads, help="the row as a JSON object")
    agent = cmd.add_parser("log-agent-run", help="log one finished agent run from its task notification")
    agent.add_argument("--round", required=True, help="round label")
    agent.add_argument("--persona", required=True, help='e.g. "Reviewer"')
    agent.add_argument("--task", required=True)
    agent.add_argument("--tokens", required=True, type=int, help="subagent_tokens")
    agent.add_argument("--tool-uses", required=True, type=int)
    agent.add_argument("--duration-ms", required=True, type=int)
    agent.add_argument("--output", default=None, help="output path, if the run wrote one")
    agent.add_argument("--verdict", required=True, choices=("accepted", "revised", "rejected"))
    agent.add_argument("--model", required=True)
    agent.add_argument("--run-ref", default=None, help="the run log id, AR-YYYYMMDD-NNN")
    gate = cmd.add_parser("sync-gate", help="mirror new gate-log lines (the gate log stays the truth)")
    gate.add_argument("--gate", type=Path, default=records.GATE_LOG)
    gate.add_argument("--gate-manifest", type=Path, default=records.GATE_MANIFEST)
    chk = cmd.add_parser("check", help="integrity, keys, manifests, gate parity, render drift, retention")
    chk.add_argument("--manifest", type=Path, default=records.RUNS_MANIFEST)
    chk.add_argument("--gate", type=Path, default=records.GATE_LOG)
    chk.add_argument("--gate-manifest", type=Path, default=records.GATE_MANIFEST)
    chk.add_argument(
        "--against-files", action="store_true", help="also compare imported rows with their files"
    )
    if importers:
        imp = cmd.add_parser("import", help="load today's files (order: rules files, gate, agent-runs)")
        imp.add_argument("source", choices=sorted(SOURCES))
        imp.add_argument("--file", type=Path, default=None, help="source file or folder (default: live one)")
        cmd.add_parser("sync", help="import every source in order (an unchanged file is a no-op)")
    seal = cmd.add_parser("reseal", help="rebuild a run's missing manifest line from its rows")
    seal.add_argument("run_id")
    seal.add_argument("--manifest", type=Path, default=records.RUNS_MANIFEST)
    score = cmd.add_parser("score", help="print the scoreboard and N")
    score.add_argument("--gate", type=Path, default=records.GATE_LOG)
    cmd.add_parser("render", help="rewrite the records:begin/end blocks of the rendered Markdown files")
    bak = cmd.add_parser("backup", help="online copy of the store, integrity-checked")
    bak.add_argument("dest", type=Path)
    exp = cmd.add_parser("expire", help="list raw files past their retention expiry (deletes nothing)")
    exp.add_argument("--now", default=None, help="UTC stamp (default: now)")


def _records(args: argparse.Namespace, log: logging.Logger) -> int:
    try:
        return _records_command(args, log)
    except (ValueError, RuntimeError, sqlite3.Error) as exc:  # a refused write or a bad argument: exit 1
        log.error(f"records {args.records_command} refused", extra={"reason": str(exc)})
        return 1


def _records_command(args: argparse.Namespace, log: logging.Logger) -> int:
    if args.records_command == "backup":
        dest = records.backup(args.dest, path=args.db)
        log.info("records backup done", extra={"dest": str(dest)})
        return 0
    with closing(records.connect(args.db)) as con:
        if args.records_command == "init":
            log.info(
                "records init done",
                extra={"applied": records.migrate(con), "tables": len(records.tables(con))},
            )
        elif args.records_command == "append":
            log.info(
                "records append done",
                extra={"table": args.table, "id": records.append(con, args.table, args.row)},
            )
        elif args.records_command == "log-agent-run":
            run_id = records.log_agent_run(
                con,
                round_label=args.round,
                persona=args.persona,
                task=args.task,
                subagent_tokens=args.tokens,
                tool_uses=args.tool_uses,
                duration_ms=args.duration_ms,
                output_path=args.output,
                verdict=args.verdict,
                model=args.model,
                run_ref=args.run_ref,
            )
            log.info("records log-agent-run done", extra={"id": run_id})
        elif args.records_command == "sync-gate":
            added = records.sync_gate(con, args.gate, manifest=args.gate_manifest)
            log.info("records sync-gate done", extra={"added": added})
        elif args.records_command == "check":
            errors = records.check(
                con, manifest=args.manifest, gate=args.gate, gate_manifest=args.gate_manifest
            )
            if args.against_files:
                if not importers:
                    raise ValueError("--against-files needs the private records importers")
                lines, docs, traders, raw = importers
                errors += lines.check_against_files(con)
                errors += lines.check_lab_against_files(con, lines.LAB_TRIALS)
                errors += lines.check_lab_against_files(con, lines.WITHDRAWN_TRIALS, withdrawn=True)
                errors += lines.check_forecasts_against_files(con)
                errors += docs.check_docs_against_files(con)
                errors += traders.check_traders_against_files(con)
                errors += raw.check_raw_against_files(con)
            if errors:
                log.error("records check failed", extra={"errors": errors})
                return 1
            log.info("records check passed")
        elif args.records_command == "import":
            load = SOURCES[args.source]
            result = load(con) if args.file is None else load(con, args.file)
            log.info(f"records import {args.source} done", extra={"result": vars(result)})
            # line sources give one row per line; a document row can give several facts (read = its rows)
            facts = result.loaded + result.unchanged + result.refused + result.skipped
            if args.source in importers[0].SOURCES and result.read != facts:
                log.error("records import counts do not add up", extra={"result": vars(result)})
                return 1
        elif args.records_command == "sync":
            loaded = {}
            for source in SYNC_ORDER:
                result = SOURCES[source](con)
                if not result.already_imported:
                    loaded[source] = {"loaded": result.loaded, "refused": result.refused}
            log.info("records sync done", extra={"changed": loaded})
        elif args.records_command == "reseal":
            log.info(
                "records reseal done",
                extra={"rows_sha256": records.reseal(con, args.run_id, manifest=args.manifest)},
            )
        elif args.records_command == "score":
            log.info("records score", extra={"score": records.score(con, args.gate)})
        elif args.records_command == "render":
            log.info("records render done", extra={"changed": [str(p) for p in records.render(con)]})
        elif args.records_command == "expire":
            paths = records.expire(con, args.now or records.utc_stamp())
            log.info("records expire", extra={"expired": [str(p) for p in paths]})
    return 0


def run(args: argparse.Namespace, settings: Settings, log: logging.Logger) -> int | None:
    """Exit code for a research command, or None when the command is not one of them."""
    if args.command == "fetch-binance-vision" and binance_vision is not None:
        root = args.data_dir or settings.data_dir
        paths = binance_vision.download_range(
            args.symbol, args.since, args.until, root, prereg_hash=args.prereg_hash
        )
        log.info("fetch-binance-vision done", extra={"symbol": args.symbol, "files": [str(p) for p in paths]})
        return 0
    if args.command == "m1-oos":
        try:
            source = RealBarSource(args.data_dir, PREREG_SHA256)
            body = run_m1_oos(args.prereg, args.data_dir, args.seeds, args.out, args.report, source)
        except (PreconditionError, CodeDefectError, ValueError, KeyError) as exc:
            log.error("m1-oos refused", extra={"reason": str(exc)})
            return 2
        log.info("m1-oos done", extra={"verdict": body["verdict"], "result_sha256": body["result_sha256"]})
        return 0
    if args.command == "reproduce-dev":
        try:
            body = reproduce_dev(args.seed, args.out)
        except (CodeDefectError, ValueError) as exc:
            log.error("reproduce-dev refused", extra={"reason": str(exc)})
            return 2
        done = {"verdict": body["verdict"], "result_sha256": body["result_sha256"]}
        log.info("reproduce-dev done", extra=done)
        return 0
    if args.command == "records":
        return _records(args, log)
    return None
