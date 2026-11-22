"""A JSON-lines log of agent runs: one row per run, written by this tool, never by hand.

Run:
  python3 tools/run_log.py add --role "Reviewer" --kind checker --task "one line" [--thread t.md] [--cap 15]
      prints the new run id (run-YYYYMMDD-NNN); put it in the agent's brief
  python3 tools/run_log.py finish run-YYYYMMDD-NNN --tool-calls 7 --verdict accepted [--status done]
  python3 tools/run_log.py check        exit 1 on a FAIL line
Every command takes `--log PATH` (default `runs.jsonl` in the current folder); `add` appends to it and
`finish` rewrites it. Default tool-call caps per kind: opinion 5, checker 15, builder 40, researcher 40.
Stdlib only; writes nothing but the log path.
"""

from __future__ import annotations

import argparse
import calendar
import json
import sys
import time
from pathlib import Path

CAPS = {"opinion": 5, "checker": 15, "builder": 40, "researcher": 40}
VERDICTS = ("accepted", "revised", "rejected")
STATUSES = ("running", "blocked", "done", "cancelled")
STALE_S = 24 * 3600
Row = dict[str, object]


def utc(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def load(log: Path) -> tuple[list[Row], list[str]]:
    """(rows, problems): a malformed line is reported, never dropped silently."""
    rows: list[Row] = []
    bad: list[str] = []
    if not log.exists():
        return rows, bad
    for i, line in enumerate(log.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            bad.append(f"line {i}: not JSON")
            continue
        if isinstance(row, dict):
            rows.append(row)
        else:
            bad.append(f"line {i}: not an object")
    return rows, bad


def add(log: Path, role: str, kind: str, task: str, now: float, thread: str | None = None,
        cap: int | None = None) -> str:
    rows, bad = load(log)
    if bad:
        raise ValueError("fix the log first: " + "; ".join(bad))
    day = time.strftime("%Y%m%d", time.gmtime(now))
    n = 1 + sum(1 for r in rows if str(r.get("id", "")).startswith(f"run-{day}-"))
    run: Row = {"id": f"run-{day}-{n:03d}", "role": role, "kind": kind, "task": task, "thread": thread,
                "cap": cap or CAPS[kind], "status": "running", "started_utc": utc(now), "finished_utc": None,
                "tool_calls": None, "verdict": None}
    with log.open("a", encoding="utf-8") as f:
        f.write(json.dumps(run, ensure_ascii=False) + "\n")
    return str(run["id"])


def finish(log: Path, run_id: str, tool_calls: int, verdict: str | None, status: str, now: float) -> None:
    rows, bad = load(log)
    hits = [r for r in rows if r.get("id") == run_id]
    if bad or len(hits) != 1:
        raise ValueError(f"{run_id} not found exactly once" + (f"; {'; '.join(bad)}" if bad else ""))
    hits[0].update({"status": status, "tool_calls": tool_calls, "verdict": verdict, "finished_utc": utc(now)})
    log.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def problems(rows: list[Row], now: float) -> tuple[list[str], list[str]]:
    """(fails, warnings) for every row."""
    fails: list[str] = []
    warns: list[str] = []
    for r in rows:
        rid, status = r.get("id"), r.get("status")
        if r.get("verdict") not in (*VERDICTS, None):
            fails.append(f"{rid}: verdict {r.get('verdict')!r} is not one of {'/'.join(VERDICTS)}")
        if status not in STATUSES:
            fails.append(f"{rid}: status {status!r} unknown")
        start = r.get("started_utc")
        if status == "running" and isinstance(start, str):
            age = now - calendar.timegm(time.strptime(start, "%Y-%m-%dT%H:%M:%SZ"))
            if age > STALE_S:
                fails.append(f"{rid}: running for {age / 3600:.0f} h; finish it or mark it blocked/cancelled")
        if status == "done" and r.get("verdict") is None:
            warns.append(f"{rid}: done with no verdict")
        calls, cap = r.get("tool_calls"), r.get("cap")
        if isinstance(calls, int) and isinstance(cap, int) and calls > cap:
            warns.append(f"{rid}: {calls} tool calls over the {cap}-call cap")
    return fails, warns


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Log agent runs and check the log.")
    p.add_argument("--log", type=Path, default=Path("runs.jsonl"))
    sub = p.add_subparsers(dest="cmd", required=True)
    a_add = sub.add_parser("add")
    a_add.add_argument("--role", required=True)
    a_add.add_argument("--kind", required=True, choices=sorted(CAPS))
    a_add.add_argument("--task", required=True)
    a_add.add_argument("--thread")
    a_add.add_argument("--cap", type=int)
    a_fin = sub.add_parser("finish")
    a_fin.add_argument("id")
    a_fin.add_argument("--tool-calls", type=int, required=True)
    a_fin.add_argument("--verdict", choices=VERDICTS)
    a_fin.add_argument("--status", default="done", choices=STATUSES)
    sub.add_parser("check")
    a = p.parse_args(argv)
    now = time.time()
    try:
        if a.cmd == "add":
            print(add(a.log, a.role, a.kind, a.task, now, a.thread, a.cap))
        elif a.cmd == "finish":
            finish(a.log, a.id, a.tool_calls, a.verdict, a.status, now)
    except ValueError as e:
        print("run_log:", e, file=sys.stderr)
        return 1
    if a.cmd != "check":
        return 0
    rows, bad = load(a.log)
    fails, warns = problems(rows, now)
    for line in bad + fails:
        print("FAIL", line)
    for line in warns:
        print("WARN", line)
    print(f"{len(rows)} runs, {len(bad) + len(fails)} fail, {len(warns)} warn")
    return 1 if bad or fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
