"""Research records store: one append-only SQLite file, `data/research.sqlite`.

All writes go through this module (stdlib `sqlite3` only). Every table is append-only: triggers refuse
UPDATE and DELETE, and a correction is a new row whose `supersedes` names the row it replaces. The DDL
lives in `sql/` as numbered migrations plus one per-table template (`triggers.sql`) and the derived views
(`views.sql`).

A research run is registered (one `research_runs` row, committed) before any computation; its results are
appended in one transaction, then sealed by one chained line in the run manifest (`runs.csv`), the only
other file the store writes besides the gate-log manifest (`gate-trials.csv`).
"""

# SQL names come from the store's own catalog (`tables()`, `columns()`, fixed views), never from user input.
# ruff: noqa: S608

import contextlib
import fcntl
import hashlib
import importlib
import json
import math
import re
import sqlite3
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import closing
from datetime import UTC, datetime, timedelta
from importlib import resources
from pathlib import Path

from signal_engine.data.pit import OOS_START
from signal_engine.settings import load_settings, required
from signal_engine.validation.trials import counted, n_trials, read_trials, sharpe_variance

ROOT = Path(__file__).resolve().parents[3]
DB = ROOT / "data" / "research.sqlite"
_SETTINGS = load_settings()
# Where the owner keeps the run manifests and the Markdown files with records:begin/end blocks (`.env`)
RUNS_MANIFEST = ROOT / _SETTINGS.records_manifest_dir / "runs.csv"
GATE_MANIFEST = ROOT / _SETTINGS.records_manifest_dir / "gate-trials.csv"
GATE_LOG = ROOT / "data" / "trials" / "trials.jsonl"
RESERVED_FILE = ROOT / "data" / "lab" / "bitvavo_reserved.json"
RENDER_FILES = tuple(ROOT / p for p in _SETTINGS.records_render_files)
GROUPS = ("meta", "ops", "knowledge", "decide", "research", "traders")
MIN_SQLITE = (3, 37)  # STRICT tables
BUSY_MS = 30_000
COMMON = ("id", "created_utc", "source", "owner", "supersedes")
RESERVED_END = (OOS_START - timedelta(days=1)).date().isoformat()  # last day a reserved coin may be read
PAIR_RE = (
    r"^([A-Z0-9]+?)(USDT|USDC|BUSD|FDUSD|EUR|USD)$"  # the research DB's pair rule (a test keeps them equal)
)
MAX_CLOCK_READS = 1_000
RUNS_HEADER = (
    "run_id",
    "run_kind",
    "n_rows",
    "rows_sha256",
    "prev_line_sha256",
    "line_sha256",
    "committed_utc",
)
GATE_HEADER = ("committed_utc", "lines", "file_sha256", "prev_line_sha256", "line_sha256")
LINK_TABLES = ("from_table", "from_id"), ("to_table", "to_id")
SCORE_VIEWS = (
    "score_agent_runs",
    "score_verification",
    "score_brier",
    "score_forecast_card",
    "score_decision_reviews",
    "score_ingest",
    "score_lessons",
    "score_gaps",
    "score_trials",
)
TRIAL_COLUMNS = (
    "family",
    "rule",
    "asset",
    "window",
    "status",
    "cost_bps_side",
    "seed",
    "sharpe_net",
    "sharpe_bh",
    "result_sha256",
    "reserved",
)
_BLOCK = re.compile(r"(<!-- records:begin (\w+)(?: ([\w,]+))? -->\n)(.*?)(<!-- records:end -->)", re.DOTALL)

Clock = Callable[[], datetime]


def utc_stamp(clock: Clock | None = None) -> str:
    """`YYYY-MM-DDTHH:MM:SS.ffffffZ`, the one timestamp form every `*_utc` column holds."""
    now = (clock or (lambda: datetime.now(UTC)))().astimezone(UTC)
    return now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def canonical(value: object) -> str:
    """Canonical JSON (sorted keys, no spaces, UTF-8); NaN and ±inf become null, so it never raises."""
    return json.dumps(
        _finite(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def _finite(value: object) -> object:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, Mapping):
        return {k: _finite(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_finite(v) for v in value]
    return value


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def connect(path: Path = DB, *, read_only: bool = False) -> sqlite3.Connection:
    """Open the store: WAL, foreign keys on, 30 s busy timeout; explicit transactions (BEGIN IMMEDIATE)."""
    if sqlite3.sqlite_version_info < MIN_SQLITE:
        raise RuntimeError(f"SQLite {sqlite3.sqlite_version} is older than 3.37 (STRICT tables)")
    if read_only:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, isolation_level=None)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(path, isolation_level=None)
        con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA foreign_keys = ON")
    con.execute(f"PRAGMA busy_timeout = {BUSY_MS}")
    return con


@contextlib.contextmanager
def _write(con: sqlite3.Connection) -> Iterator[None]:
    """One write transaction: all or nothing, and no transaction left open after a refusal."""
    con.execute("BEGIN IMMEDIATE")
    try:
        yield
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise


def _sql(name: str) -> str:
    return resources.files(__package__).joinpath("sql", name).read_text(encoding="utf-8")


def _statements(script: str) -> Iterator[str]:
    """Split a script into whole statements (a trigger body holds `;` of its own)."""
    buf = ""
    for line in script.splitlines(keepends=True):
        if not buf and line.lstrip().startswith("--"):
            continue
        buf += line
        if sqlite3.complete_statement(buf):
            yield buf.strip()
            buf = ""
    if buf.strip():
        raise ValueError(f"incomplete SQL statement: {buf.strip()[:80]}")


def _migrations() -> list[tuple[int, str]]:
    files = resources.files(__package__).joinpath("sql").iterdir()
    found = sorted(f.name for f in files if f.name[:4].isdigit() and f.name.endswith(".sql"))
    return [(int(name[:4]), name) for name in found]


def tables(con: sqlite3.Connection) -> list[str]:
    """The record tables (one per group prefix), in name order."""
    rows = con.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name").fetchall()
    return [name for (name,) in rows if name.split("_", 1)[0] in GROUPS]


def migrate(con: sqlite3.Connection, *, clock: Clock | None = None) -> list[str]:
    """Apply the numbered migrations not yet applied (`PRAGMA user_version`); returns the applied file names.

    Each migration runs in one transaction with the per-table template for every new table and one
    `meta_tables` catalog row per new table. The derived views are rebuilt on every call.
    """
    version = int(con.execute("PRAGMA user_version").fetchone()[0])
    applied = []
    for number, name in _migrations():
        if number <= version:
            continue
        before = set(tables(con))
        with _write(con):
            for stmt in _statements(_sql(name)):
                con.execute(stmt)
            template = _sql("triggers.sql")
            new = [t for t in tables(con) if t not in before]
            for table in new:
                for stmt in _statements(template.replace("{t}", table)):
                    con.execute(stmt)
            stamp = utc_stamp(clock)
            for table in new:
                con.execute(
                    "INSERT INTO meta_tables"
                    " (id, created_utc, source, owner, name, group_name, writer, truth, stage)"
                    " VALUES (?, ?, ?, 'records', ?, ?, 'signal_engine.records', 'sqlite', 'S1')",
                    (uuid.uuid4().hex, stamp, f"sql/{name}", table, table.split("_", 1)[0]),
                )
            con.execute(f"PRAGMA user_version = {number}")
        applied.append(name)
    with _write(con):
        for stmt in _statements(_sql("views.sql")):
            con.execute(stmt)
    return applied


def columns(con: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in con.execute(f"PRAGMA table_info({table})").fetchall()]


def _validate(con: sqlite3.Connection, table: str, row: Mapping[str, object]) -> None:
    if table not in tables(con):
        raise ValueError(f"unknown table: {table}")
    unknown = sorted(set(row) - set(columns(con, table)))
    if unknown:
        raise ValueError(f"unknown columns for {table}: {', '.join(unknown)}")
    if "created_utc" in row:
        raise ValueError("created_utc is set by the store")


def _insert(con: sqlite3.Connection, table: str, row: Mapping[str, object], stamp: str) -> str:
    """Insert inside the caller's transaction; the caller has validated `table` and the column names."""
    values = {"id": uuid.uuid4().hex, **row, "created_utc": stamp}
    names = list(values)
    con.execute(
        f"INSERT INTO {table} ({', '.join(names)}) VALUES ({', '.join('?' for _ in names)})",
        [values[n] for n in names],
    )
    return str(values["id"])


def append(
    con: sqlite3.Connection, table: str, row: Mapping[str, object], *, clock: Clock | None = None
) -> str:
    """Insert one row; returns its id. Fills `id` (unless given) and `created_utc`; refuses unknown names."""
    if table in ("decide_forecasts", "decide_forecast_resolutions"):  # the ledger file is their truth
        raise ValueError(f"{table} is written through the forecasts ledger (importers.log_forecast)")
    _validate(con, table, row)
    with _write(con):
        return _insert(con, table, row, utc_stamp(clock))


# --- research runs and trials ----------------------------------------------------------------------------


def _base(asset: str) -> str:
    name = asset.upper()
    if re.fullmatch(r"[A-Z0-9]+[/-][A-Z0-9]+", name):
        return re.split(r"[/-]", name)[0]
    pair = re.match(PAIR_RE, name)
    return pair.group(1) if pair else name


def touches_reserved(assets: Sequence[str], window: str | None, reserved: set[str]) -> bool:
    """True when a reserved coin is read past the cut; a window with no date counts as past it."""
    ends = [d + "-31" if len(d) == 7 else d for d in re.findall(r"\d{4}-\d{2}(?:-\d{2})?", window or "")]
    if ends and max(ends) <= RESERVED_END:
        return False
    bases = {_base(a) for a in assets}
    return any(b in reserved or re.sub(r"^(1000+|1M)", "", b) in reserved for b in bases)


def register_run(
    con: sqlite3.Connection,
    *,
    run_kind: str,
    code_sha256: str | None,
    config_sha256: str | None,
    data_sha256: str | None,
    planned_cells: int,
    window: str | None,
    assets: Sequence[str],
    seed: int | None,
    cost_bps_side: float | None,
    prereg_sha256: str | None,
    owner: str,
    source: str,
    agent_run_id: str | None = None,
    reserved_file: Path = RESERVED_FILE,
    clock: Clock | None = None,
) -> str:
    """Commit the run's registration row before any computation; returns the run id."""
    prereg_id = None
    if prereg_sha256 is not None:
        found = con.execute(
            "SELECT id FROM research_preregistrations_current WHERE sha256 = ?", (prereg_sha256,)
        ).fetchone()
        if found is None:
            raise ValueError(f"no preregistration with sha256 {prereg_sha256}")
        prereg_id = found[0]
    try:  # Private module, not in the public repo: create your own `signal_engine/data/reserved.py` providing
        # load_reserved(path) -> the set of held-out base assets that a run may not read after RESERVED_END
        reserved_rule = importlib.import_module("signal_engine.data.reserved")
    except ImportError as exc:
        raise ValueError("registering a run needs the private module signal_engine.data.reserved") from exc
    reserved = reserved_rule.load_reserved(reserved_file)
    row = {
        "source": source,
        "owner": owner,
        "registered_utc": utc_stamp(clock),
        "run_kind": run_kind,
        "code_sha256": code_sha256,
        "config_sha256": config_sha256,
        "data_sha256": data_sha256,
        "prereg_id": prereg_id,
        "touches_reserved": int(touches_reserved(assets, window, set(reserved))),
        "reserved_list_sha256": _sha("\n".join(reserved)),
        "planned_cells": planned_cells,
        "window": window,
        "assets_json": canonical(sorted(assets)),
        "seed": seed,
        "cost_bps_side": cost_bps_side,
        "agent_run_id": agent_run_id,
    }
    return append(con, "research_runs", row, clock=clock)


def _after(registered: str, clock: Clock | None) -> str:
    """The first clock reading later than the registration (microsecond stamps can tie on a fast run)."""
    for _ in range(MAX_CLOCK_READS):
        stamp = utc_stamp(clock)
        if stamp > registered:
            return stamp
    raise RuntimeError(f"the clock did not pass the registration time {registered}")


def _number(value: object) -> object:
    return None if isinstance(value, float) and not math.isfinite(value) else value


def rows_sha256(con: sqlite3.Connection, run_id: str) -> tuple[int, str]:
    """(rows, sha256) of the run's `row_json` lines sorted by trial id and joined by newlines."""
    rows = con.execute(
        "SELECT row_json FROM research_trials WHERE run_id = ? ORDER BY trial_id, row_json", (run_id,)
    ).fetchall()
    return len(rows), _sha("\n".join(r[0] for r in rows))


def append_trials(
    con: sqlite3.Connection,
    run_id: str,
    rows: Sequence[Mapping[str, object]],
    *,
    manifest: Path = RUNS_MANIFEST,
    clock: Clock | None = None,
) -> str:
    """Append the run's result rows in one transaction, then seal them in the manifest; returns rows_sha256.

    Each row needs `trial_id`; its whole payload is stored as canonical JSON (`row_json`, the hash input).
    Code and data hashes default to the run's; `config_sha256` defaults to the hash of canonical `params`.
    """
    if not rows:
        raise ValueError("no trial rows")
    run = con.execute(
        "SELECT registered_utc, run_kind, withdrawn, code_sha256, data_sha256, owner, source"
        " FROM research_runs WHERE id = ?",
        (run_id,),
    ).fetchone()
    if run is None:
        raise ValueError(f"unknown run: {run_id}")
    registered, kind, withdrawn, code, data, owner, source = run
    result_utc = _after(registered, clock)
    with _write(con):
        for row in rows:
            if not row.get("trial_id"):
                raise ValueError("a trial row needs a trial_id")
            params = row.get("params")
            values: dict[str, object] = {c: _number(row.get(c)) for c in TRIAL_COLUMNS}
            values |= {
                "source": source,
                "owner": owner,
                "trial_id": str(row["trial_id"]),
                "run_id": run_id,
                "registered_utc": registered,
                "result_utc": result_utc,
                "params_json": None if params is None else canonical(params),
                "row_json": canonical(row),
                "config_sha256": row.get("config_sha256")
                or (None if params is None else _sha(canonical(params))),
                "code_sha256": row.get("code_sha256", code),
                "data_sha256": row.get("data_sha256", data),
                "reserved": int(bool(row.get("reserved"))),
                "withdrawn": withdrawn,
                "supersedes": row.get("supersedes"),  # a correction goes through here, so it is sealed too
            }
            values["provenance"] = "full" if values["code_sha256"] and values["data_sha256"] else "legacy"
            _insert(con, "research_trials", values, result_utc)
    return _seal_run(con, run_id, kind, manifest, clock)


def _seal_run(con: sqlite3.Connection, run_id: str, kind: str, manifest: Path, clock: Clock | None) -> str:
    return _seal_runs(con, [(run_id, kind)], manifest, clock)[0]


def _seal_runs(
    con: sqlite3.Connection, runs: Sequence[tuple[str, str]], manifest: Path, clock: Clock | None
) -> list[str]:
    """One chained manifest line per (run id, kind), all appended under one lock; returns the rows_sha256s."""
    stamp = utc_stamp(clock)
    lines, digests = [], []
    for run_id, kind in runs:
        n_rows, digest = rows_sha256(con, run_id)
        digests.append(digest)
        lines.append(
            {
                "run_id": run_id,
                "run_kind": kind,
                "n_rows": str(n_rows),
                "rows_sha256": digest,
                "committed_utc": stamp,
            }
        )
    _seal(manifest, RUNS_HEADER, lines)
    return digests


def reseal(
    con: sqlite3.Connection, run_id: str, *, manifest: Path = RUNS_MANIFEST, clock: Clock | None = None
) -> str:
    """Append the manifest line a crash left out, rebuilt from the run's rows; returns rows_sha256."""
    run = con.execute("SELECT run_kind FROM research_runs WHERE id = ?", (run_id,)).fetchone()
    if run is None:
        raise ValueError(f"unknown run: {run_id}")
    return _seal_run(con, run_id, run[0], manifest, clock)


# --- manifests: one chained CSV line per seal --------------------------------------------------------------


def _line_sha(header: Sequence[str], fields: Mapping[str, str]) -> str:
    return _sha(",".join(fields[c] for c in header if c != "line_sha256"))


def _seal(path: Path, header: Sequence[str], rows: Sequence[Mapping[str, str]]) -> None:
    """Append one line per row, each chained to the one before (`prev_line_sha256`); `line_sha256` hashes
    every other field. One exclusive lock for the batch makes a second writer wait, so it chains to the last
    line written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8", newline="") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        fh.seek(0)
        lines = fh.read().splitlines()
        out = [] if lines else [",".join(header)]
        prev = lines[-1].split(",")[header.index("line_sha256")] if len(lines) > 1 else ""
        for fields in rows:
            line = {**fields, "prev_line_sha256": prev}
            line["line_sha256"] = prev = _line_sha(header, line)
            out.append(",".join(line[c] for c in header))
        fh.write("".join(f"{line}\n" for line in out))


def _manifest(path: Path, header: Sequence[str]) -> tuple[list[dict[str, str]], list[str]]:
    """The manifest's lines and its chain errors (bad header, bad line hash, broken link)."""
    if not path.exists():
        return [], []
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != ",".join(header):
        return [], [f"{path.name}: header is not {','.join(header)}"]
    out, errors, prev = [], [], ""
    for i, line in enumerate(lines[1:], start=2):
        parts = line.split(",")
        if len(parts) != len(header):
            errors.append(f"{path.name}:{i}: {len(parts)} fields, expected {len(header)}")
            continue
        fields = dict(zip(header, parts, strict=True))
        if fields["line_sha256"] != _line_sha(header, fields):
            errors.append(f"{path.name}:{i}: line_sha256 does not match the line")
        if fields["prev_line_sha256"] != prev:
            errors.append(f"{path.name}:{i}: prev_line_sha256 breaks the chain")
        prev = fields["line_sha256"]
        out.append(fields)
    return out, errors


# --- agent runs ------------------------------------------------------------------------------------------


def log_agent_run(
    con: sqlite3.Connection,
    *,
    round_label: str,
    persona: str,
    task: str,
    subagent_tokens: int,
    tool_uses: int,
    duration_ms: int,
    output_path: str | None,
    verdict: str,
    model: str,
    run_ref: str | None = None,
    kind: str | None = None,
    goal_id: str | None = None,
    thread: str | None = None,
    cap: int | None = None,
    prompt_sha: str | None = None,
    status: str | None = None,
    started_utc: str | None = None,
    verdict_note: str | None = None,
    owner: str | None = None,
    source: str = "task notification",
    clock: Clock | None = None,
) -> str:
    """Log one finished agent run (and its output, if any) under its round; returns the agent-run id."""
    owner = str(required("run_owner")) if owner is None else owner
    run = {
        "source": source,
        "owner": owner,
        "persona": persona,
        "task": task,
        "subagent_tokens": subagent_tokens,
        "tool_uses": tool_uses,
        "duration_ms": duration_ms,
        "output_path": output_path,
        "verdict": verdict,
        "model": model,
        "run_ref": run_ref,
        "kind": kind,
        "goal_id": goal_id,
        "thread": thread,
        "cap": cap,
        "prompt_sha": prompt_sha,
        "status": status,
        "started_utc": started_utc,
        "verdict_note": verdict_note,
    }
    stamp = utc_stamp(clock)
    with _write(con):
        found = con.execute("SELECT id FROM ops_rounds_current WHERE label = ?", (round_label,)).fetchone()
        round_id = (
            found[0]
            if found
            else _insert(con, "ops_rounds", {"source": source, "owner": owner, "label": round_label}, stamp)
        )
        run_id = _insert(con, "ops_agent_runs", {**run, "round_id": round_id}, stamp)
        if output_path:
            _insert(
                con,
                "knowledge_outputs",
                {
                    "source": source,
                    "owner": owner,
                    "path": output_path,
                    "agent_run_id": run_id,
                    "round_id": round_id,
                    "verdict": verdict,
                },
                stamp,
            )
    return run_id


# --- gate log mirror -------------------------------------------------------------------------------------


def _rel(path: Path) -> str:
    resolved = path.resolve()
    return str(resolved.relative_to(ROOT)) if resolved.is_relative_to(ROOT) else str(resolved)


def sync_gate(
    con: sqlite3.Connection,
    path: Path = GATE_LOG,
    *,
    manifest: Path = GATE_MANIFEST,
    owner: str | None = None,
    clock: Clock | None = None,
) -> int:
    """Mirror the gate log's new lines (raw text, keyed by file and position); returns how many were added.

    The gate log stays the truth: a mirrored line that no longer matches the file is refused, never rewritten.
    """
    if not path.exists():
        return 0
    owner = str(required("gate_owner")) if owner is None else owner
    raw = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    source_file = _rel(path)
    have = con.execute(
        "SELECT seq, line_sha256 FROM research_gate_lines WHERE source_file = ? ORDER BY seq", (source_file,)
    ).fetchall()
    for seq, sha in have:
        if seq >= len(raw) or _sha(raw[seq]) != sha:
            raise ValueError(
                f"{path} line {seq + 1} changed after it was mirrored: the gate log is append-only"
            )
    new = raw[len(have) :]
    if not new:
        return 0
    preregs = dict(con.execute("SELECT sha256, id FROM research_preregistrations_current").fetchall())
    stamp = utc_stamp(clock)
    with _write(con):
        for seq, line in enumerate(new, start=len(have)):
            trial = json.loads(line)
            _insert(
                con,
                "research_gate_lines",
                {
                    "source": source_file,
                    "owner": owner,
                    "source_file": source_file,
                    "seq": seq,
                    "trial_id": None if trial.get("id") is None else str(trial["id"]),
                    "line_json": line,
                    "line_sha256": _sha(line),
                    "prereg_id": preregs.get(trial.get("prereg_sha256")),
                },
                stamp,
            )
    file_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    _seal(manifest, GATE_HEADER, [{"committed_utc": stamp, "lines": str(len(raw)), "file_sha256": file_sha}])
    return len(new)


def gate_lines(con: sqlite3.Connection, path: Path = GATE_LOG) -> list[dict[str, object]]:
    rows = con.execute(
        "SELECT line_json FROM research_gate_lines WHERE source_file = ? ORDER BY seq", (_rel(path),)
    ).fetchall()
    return [json.loads(r[0]) for r in rows]


# --- checks, render, expiry, score, backup ----------------------------------------------------------------


def check(
    con: sqlite3.Connection,
    *,
    manifest: Path = RUNS_MANIFEST,
    gate: Path = GATE_LOG,
    gate_manifest: Path = GATE_MANIFEST,
    render_files: Sequence[Path] = RENDER_FILES,
    now: str | None = None,
) -> list[str]:
    """Every problem found (empty = pass): integrity, foreign keys, orphan links, the run-manifest chain and
    seals, gate parity, unmatched preregistrations, outputs without an agent run, render drift, retention."""
    errors = [f"integrity: {r[0]}" for r in con.execute("PRAGMA integrity_check") if r[0] != "ok"]
    errors += [
        f"foreign key: {t} row {rowid} -> {parent}"
        for t, rowid, parent, _ in con.execute("PRAGMA foreign_key_check")
    ]
    known = set(tables(con))
    for link_id, *ends in con.execute(
        "SELECT id, from_table, from_id, to_table, to_id FROM meta_links_current"
    ):
        for table, row_id in (ends[:2], ends[2:]):
            if (
                table not in known
                or not con.execute(f"SELECT 1 FROM {table} WHERE id = ?", (row_id,)).fetchone()
            ):
                errors.append(f"meta_links {link_id}: {table}/{row_id} does not exist")
    sealed, chain = _manifest(manifest, RUNS_HEADER)
    errors += chain
    last = {line["run_id"]: line for line in sealed}
    for (run_id,) in con.execute("SELECT DISTINCT run_id FROM research_trials ORDER BY run_id").fetchall():
        n_rows, digest = rows_sha256(con, run_id)
        line = last.get(run_id)
        if line is None:
            errors.append(f"run {run_id}: no manifest line (run `records reseal {run_id}`)")
        elif (line["n_rows"], line["rows_sha256"]) != (str(n_rows), digest):
            errors.append(f"run {run_id}: rows do not match the manifest ({n_rows} rows, {digest[:12]})")
    errors += _columns_match_row_json(con)
    errors += _gate_parity(con, gate, gate_manifest)
    frozen = {r[0] for r in con.execute("SELECT sha256 FROM research_preregistrations_current")}
    for seq, line in con.execute("SELECT seq, line_json FROM research_gate_lines ORDER BY seq").fetchall():
        sha = json.loads(line).get("prereg_sha256")
        if sha and sha not in frozen:
            errors.append(f"gate line {seq}: preregistration {sha[:12]} is not in research_preregistrations")
    errors += [
        f"knowledge_outputs {i}: {p} has a round but no agent run"
        for i, p in con.execute(
            "SELECT id, path FROM knowledge_outputs_current"
            " WHERE round_id IS NOT NULL AND agent_run_id IS NULL"
        )
    ]
    errors += [
        f"render: {p} differs from the store (hand edit?); run `records render`"
        for p in render(con, render_files, write=False)
    ]
    errors += [f"expired raw file (retention): {p}" for p in expire(con, now or utc_stamp())]
    return errors


def _columns_match_row_json(con: sqlite3.Connection) -> list[str]:
    """The seal covers `row_json`; the views read the columns, so a live row's columns must equal its payload.
    (Backfilled rows keep their source line as `row_json` in another shape; M3 checks them against files.)"""
    errors = []
    names = ("trial_id", *TRIAL_COLUMNS)
    for row_id, row_json, *stored in con.execute(
        f"SELECT id, row_json, {', '.join(names)} FROM research_trials WHERE backfilled = 0"
    ).fetchall():
        payload = json.loads(row_json)
        expected = [
            str(payload.get("trial_id")),
            *(_number(payload.get(c)) for c in TRIAL_COLUMNS[:-1]),
            int(bool(payload.get("reserved"))),
        ]
        if stored != expected:
            errors.append(f"research_trials {row_id}: columns differ from its sealed row_json")
    return errors


def _gate_parity(con: sqlite3.Connection, gate: Path, gate_manifest: Path) -> list[str]:
    mirrored = gate_lines(con, gate)
    if not gate.exists():
        return ["gate log missing but mirrored"] if mirrored else []
    if mirrored and not gate_manifest.exists():
        return [f"{gate_manifest.name} is missing but the gate log is mirrored"]
    logged = read_trials(gate)
    errors = []
    if canonical(counted(mirrored)) != canonical(counted(logged)):
        errors.append("gate mirror differs from the gate log (run `records sync-gate`)")
    if (n_trials(mirrored), sharpe_variance(mirrored)) != (n_trials(logged), sharpe_variance(logged)):
        errors.append("gate N or Sharpe variance differs between the mirror and the gate log")
    sealed, chain = _manifest(gate_manifest, GATE_HEADER)
    errors += chain
    file_sha = hashlib.sha256(gate.read_bytes()).hexdigest()
    if sealed and sealed[-1]["file_sha256"] != file_sha:
        errors.append(f"{gate_manifest.name}: last file_sha256 is not the gate log's sha256")
    return errors


def _cell(value: object) -> str:
    return "" if value is None else str(value).replace("|", "\\|").replace("\n", " ")


def render(
    con: sqlite3.Connection, files: Sequence[Path] = RENDER_FILES, *, write: bool = True
) -> list[Path]:
    """Rewrite each `<!-- records:begin <table> [col,col] -->` ... `<!-- records:end -->` block from the
    table's current rows; returns the files that changed (or would change, with `write=False`)."""
    known = set(tables(con))

    def table(match: re.Match[str]) -> str:
        name, cols = match.group(2), match.group(3)
        if name not in known:
            raise ValueError(f"render: unknown table {name}")
        names = cols.split(",") if cols else [c for c in columns(con, name) if c not in COMMON]
        rows = con.execute(
            f"SELECT {', '.join(names)} FROM {name}_current ORDER BY created_utc, id"
        ).fetchall()
        body = ["| " + " | ".join(names) + " |", "|" + "---|" * len(names)]
        body += ["| " + " | ".join(_cell(v) for v in row) + " |" for row in rows]
        return match.group(1) + "\n".join(body) + "\n" + match.group(5)

    changed = []
    for path in files:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        new = _BLOCK.sub(table, text)
        if new != text:
            changed.append(path)
            if write:
                path.write_text(new, encoding="utf-8")
    return changed


def expire(con: sqlite3.Connection, now: str) -> list[Path]:
    """Raw files past their retention expiry, for the owner to confirm deletion; never deletes anything."""
    rows = con.execute(
        "SELECT path FROM meta_raw_files_current"
        " WHERE expires_utc IS NOT NULL AND expires_utc <= ? ORDER BY path",
        (now,),
    ).fetchall()
    return [Path(r[0]) for r in rows]


def score(con: sqlite3.Connection, gate: Path = GATE_LOG) -> dict[str, list[dict[str, object]]]:
    """The scoreboard: every `score_*` view, plus N (lab N, N_upper, and gate N = gate log + m2 runs)."""
    out = {}
    for view in (*SCORE_VIEWS, "research_n"):
        cur = con.execute(f"SELECT * FROM {view}")
        out[view] = [dict(zip([d[0] for d in cur.description], row, strict=True)) for row in cur.fetchall()]
    n = out["research_n"][0]
    n["gate_n"] = n_trials(gate_lines(con, gate)) + int(str(n["m2_n"]))
    return out


def backup(dest: Path, *, path: Path = DB) -> Path:
    """Copy the live store with SQLite's online backup (safe while a writer runs); refuses a corrupt copy."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with closing(connect(path, read_only=True)) as src, closing(sqlite3.connect(dest)) as out:
        src.backup(out)
        result = out.execute("PRAGMA integrity_check").fetchone()[0]
    if result != "ok":
        raise RuntimeError(f"backup {dest} failed its integrity check: {result}")
    return dest
