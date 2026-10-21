"""Append-only trial log (`data/trials/trials.jsonl`): every variant tried is one line; DSR reads N here."""

import fcntl
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

N_FLOOR = 9  # DSR N = max(9, counted lines)
REQUIRED = ("T1", "T2", "T3", "T4", "O1", "O2", "O3", "T5", "T6")


def log_trial(path: Path, trial: dict[str, Any]) -> None:
    """Append one trial line; existing lines are never rewritten. The line carries `prev_sha256`, the sha256
    of every byte before it, so a later edit of any earlier line (chained or not) breaks `verify_chain`."""
    if not trial.get("id"):
        raise ValueError("a trial needs an id")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)  # a second writer waits, so it hashes the bytes the first one wrote
        fh.seek(0)
        before = fh.read()
        if before and not before.endswith(b"\n"):
            raise ValueError(f"{path} does not end with a newline: the next line would fuse onto the last")
        line = trial | {"prev_sha256": hashlib.sha256(before).hexdigest()}
        fh.write((json.dumps(line, sort_keys=True) + "\n").encode("utf-8"))


def verify_chain(path: Path) -> int | None:
    """Line number (1-based) of the first break, or None when intact. Lines before the first chained line
    are covered by that line's hash; after it, every line must carry a matching `prev_sha256`. Lines cut off
    the end are not detected here: the M2 stamp covers the sha256 of the whole file."""
    if not path.exists():
        return None
    raw = path.read_bytes().splitlines(keepends=True)
    started = False
    for i, line in enumerate(raw):
        if not line.strip():
            continue
        try:
            prev = json.loads(line).get("prev_sha256")
        except (json.JSONDecodeError, AttributeError):
            return i + 1
        if prev is None and not started:
            continue
        started = True
        if prev != hashlib.sha256(b"".join(raw[:i])).hexdigest():
            return i + 1
    return None


def read_trials(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def check_registered(lines: list[dict[str, Any]], prereg_sha256: str) -> None:
    """Refuse the run unless every pre-registered trial is logged and tagged with the frozen hash."""
    tagged = {line.get("id") for line in lines if line.get("prereg_sha256") == prereg_sha256}
    missing = [t for t in REQUIRED if t not in tagged]
    if missing:
        raise ValueError(f"trial log lacks {missing} tagged with the rules-file hash")


def counted(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One entry per trial id, its latest line (a result appended after the registration line
    updates it, the log stays append-only); bug-fix re-runs (`<id>-fix`) are not counted."""
    latest: dict[str, dict[str, Any]] = {}
    for line in lines:
        trial = str(line.get("id", ""))
        if not trial.endswith("-fix"):
            latest[trial] = latest.get(trial, {}) | {k: v for k, v in line.items() if v is not None}
    return list(latest.values())


def n_trials(lines: list[dict[str, Any]]) -> int:
    return max(N_FLOOR, len(counted(lines)))


def sharpe_variance(lines: list[dict[str, Any]]) -> float:
    """Cross-trial variance (ddof 1) of the counted daily Sharpes; 0 when fewer than two are logged."""
    values = [float(line["daily_sharpe"]) for line in counted(lines) if line.get("daily_sharpe") is not None]
    return float(np.var(values, ddof=1)) if len(values) > 1 else 0.0
