"""Forward paper prediction log.

One run of `predict` (on demand; no scheduled job) appends, in this order, for each coin:
1. a `gap` line for every UTC day since the last line that had no run (never a back-filled prediction);
2. an `outcome` line for every prediction whose outcome bar has closed;
3. today's `prediction`, or a `no_decision` line with its reason.

The decision is the frozen B1.1.0-spot target weight (plumbing only: the rule failed its
out-of-sample gate) plus
p = P(u[as_of + 8 d] > u[as_of + 1 d]) from a hit-rate table per state counted on bars before the
OOS start. The window starts at the close of the bar in progress at commit time, so it opens after
the commit. Lines are chained with `trials.log_trial`; each private line (with its salt) has a public
twin without the salt. A same-day rerun writes nothing. The tail is stamped outside the files.
"""

import fcntl
import hashlib
import json
import math
import os
import secrets
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from signal_engine.backtest.engine import Bars
from signal_engine.strategy import b_spot
from signal_engine.validation.trials import log_trial, read_trials, verify_chain

DAY_MS = 86_400_000
VERSION = "1"
EVENT = "u[as_of+8d] > u[as_of+1d]"
REF_DAYS, OUT_DAYS = 1, 8  # reference bar and outcome bar, in days after the as_of bar
K_SHRINK = 20  # pseudo-observations pulling each state's hit rate to the coin's base rate
PLACES = 6
PREDICTIONS, COMMITMENTS, STAMP = "predictions.jsonl", "commitments.jsonl", "predlog-stamp.json"
PUBLIC_KEYS = ("id", "seq", "kind", "committed_at", "commitment")


class ChainError(ValueError):
    """The record is broken at line `seq`."""

    def __init__(self, seq: int, why: str) -> None:
        super().__init__(f"seq {seq}: {why}")
        self.seq = seq
        self.why = why


def dec(x: float | None, places: int = PLACES) -> str:
    """Fixed-decimal string, so a float repr can never change a hash; NaN, inf and None are refused."""
    if x is None or not math.isfinite(x):
        raise ValueError(f"not a finite number: {x!r}")
    return f"{x:.{places}f}"


def canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def bars_sha256(ts: np.ndarray, u: np.ndarray) -> str:
    """sha256 of the canonical (ts, u) rows the decision used; a missing bar is written as null."""
    rows = [[int(t), None if not math.isfinite(v) else dec(float(v))] for t, v in zip(ts, u, strict=True)]
    return hashlib.sha256(canon(rows).encode()).hexdigest()


def train_table(bars: Bars) -> dict[str, dict[str, str | int]]:
    """Per coin: base rate and shrunk hit rate per state, counted only on bars before the OOS start."""
    table: dict[str, dict[str, str | int]] = {}
    end = bars.oos_start
    for i, coin in enumerate(b_spot.COINS):
        u = bars.u[:end, i]
        tau, _ = b_spot.signals(u)
        hits: dict[str, list[bool]] = {"long": [], "flat": []}
        for j in range(len(u) - OUT_DAYS):
            ref, out = u[j + REF_DAYS], u[j + OUT_DAYS]
            if math.isfinite(tau[j]) and math.isfinite(ref) and math.isfinite(out):
                hits["long" if tau[j] > 0 else "flat"].append(bool(out > ref))
        n = len(hits["long"]) + len(hits["flat"])
        if n == 0:
            raise ValueError(f"{coin}: no training events before the OOS start")
        base = (sum(hits["long"]) + sum(hits["flat"])) / n
        row: dict[str, str | int] = {"base": dec(base), "n": n}
        for state, h in hits.items():
            row[f"p_{state}"] = dec((sum(h) + K_SHRINK * base) / (len(h) + K_SHRINK))
            row[f"n_{state}"] = len(h)
        table[coin] = row
    return table


def params_sha256(table: dict[str, dict[str, str | int]]) -> str:
    rule = {"strategy": b_spot.STRATEGY_VERSION, "event": EVENT, "k": K_SHRINK, "version": VERSION}
    return hashlib.sha256(canon({"rule": rule, "table": table}).encode()).hexdigest()


@contextmanager
def _locked(record_dir: Path) -> Iterator[None]:
    """One writer at a time for both files (`log_trial` takes its own lock on each file)."""
    record_dir.mkdir(parents=True, exist_ok=True)
    with (record_dir / ".predlog.lock").open("a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        yield


def _append(record_dir: Path, body: dict[str, Any], now_ms: int) -> dict[str, Any]:
    """Append one private line and its public twin; called under `_locked`."""
    lines = read_trials(record_dir / PREDICTIONS)
    if lines and lines[-1]["committed_at"] > iso(now_ms):
        raise ChainError(len(lines) + 1, "the clock went backwards: committed_at before the last line")
    salt = secrets.token_hex(32)
    commitment = hashlib.sha256(bytes.fromhex(salt) + canon(body).encode()).hexdigest()
    line = body | {"seq": len(lines) + 1, "committed_at": iso(now_ms), "salt": salt, "commitment": commitment}
    log_trial(record_dir / PREDICTIONS, line)
    log_trial(record_dir / COMMITMENTS, {k: line[k] for k in PUBLIC_KEYS})
    for name in (PREDICTIONS, COMMITMENTS):
        with (record_dir / name).open("ab") as fh:
            os.fsync(fh.fileno())
    return line


CHAIN_KEYS = ("seq", "committed_at", "salt", "commitment", "prev_sha256")  # added by `_append`


def _body_keys(line: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in line.items() if k not in CHAIN_KEYS}


def _stamp(record_dir: Path, stamp_path: Path) -> None:
    raw = (record_dir / PREDICTIONS).read_bytes()
    stamp = {"lines": len(raw.splitlines()), "sha256": hashlib.sha256(raw).hexdigest()}
    stamp_path.parent.mkdir(parents=True, exist_ok=True)
    stamp_path.write_text(canon(stamp) + "\n")


def verify(record_dir: Path, stamp_path: Path | None = None) -> int:
    """Number of lines when intact; raises ChainError at the first broken line."""
    private, public = record_dir / PREDICTIONS, record_dir / COMMITMENTS
    for path in (private, public):
        broken = verify_chain(path)
        if broken is not None:
            raise ChainError(broken, f"{path.name}: prev_sha256 does not match the bytes before the line")
    lines, twins = read_trials(private), read_trials(public)
    if len(lines) != len(twins):
        raise ChainError(min(len(lines), len(twins)) + 1, "private and public files differ in length")
    last = ""
    for n, (line, twin) in enumerate(zip(lines, twins, strict=True), start=1):
        if line.get("seq") != n:
            raise ChainError(n, f"seq {line.get('seq')} is not contiguous")
        if "salt" in twin or any(twin.get(k) != line.get(k) for k in PUBLIC_KEYS):
            raise ChainError(n, "the public line does not match its private line")
        want = hashlib.sha256(bytes.fromhex(line["salt"]) + canon(_body_keys(line)).encode()).hexdigest()
        if line["commitment"] != want:
            raise ChainError(n, "the commitment does not recompute from salt + line")
        if line["committed_at"] < last:
            raise ChainError(n, "committed_at goes backwards")
        last = line["committed_at"]
    if stamp_path is not None and stamp_path.exists():
        stamp = json.loads(stamp_path.read_text())
        raw = private.read_bytes().splitlines(keepends=True)
        if len(raw) < stamp["lines"]:
            raise ChainError(len(raw) + 1, f"lines cut off the end: the stamp counts {stamp['lines']}")
        if hashlib.sha256(b"".join(raw[: stamp["lines"]])).hexdigest() != stamp["sha256"]:
            raise ChainError(stamp["lines"], "the stamped lines changed")
    return len(lines)


def _day(ms: int) -> str:
    return iso(ms)[:10]


def bar_index(bars: Bars) -> dict[int, int]:
    index = {int(t): n for n, t in enumerate(bars.ts)}
    if len(index) != len(bars.ts):
        raise ValueError("the same bar ts appears twice in the input: refused, never deduplicated")
    return index


def outcome_body(
    bars: Bars, index: dict[int, int], i: int, pred: dict[str, Any], now_ms: int
) -> dict[str, Any] | None:
    """The outcome line of prediction `pred` for coin `i`, or None while it is not due or a bar is missing."""
    a = int(pred["as_of_ts"])
    ref, out = index.get(a + REF_DAYS * DAY_MS), index.get(a + OUT_DAYS * DAY_MS)
    if ref is None or out is None or a + (OUT_DAYS + 1) * DAY_MS > now_ms:
        return None
    u_ref, u_out = float(bars.u[ref, i]), float(bars.u[out, i])
    if not (math.isfinite(u_ref) and math.isfinite(u_out)):
        return None  # a missing bar keeps the outcome pending, never filled
    return {"id": f"outcome:{pred['id']}", "kind": "outcome", "symbol": pred["symbol"],
            "prediction_seq": pred["seq"], "u_ref": dec(u_ref), "u_out": dec(u_out), "hit": u_out > u_ref}


def decide_body(
    bars: Bars, index: dict[int, int], i: int, want_as_of: int, table: dict[str, dict[str, str | int]],
    p_sha: str, prereg_sha256: str, code: str,
) -> dict[str, Any]:
    """The prediction line for coin `i` on bar `want_as_of`, or a no_decision line with its reason."""
    coin = b_spot.COINS[i]
    symbol = f"{coin}-EUR"
    a_idx = index.get(want_as_of)
    reason = None
    if a_idx is None or bars.ts[-1] != want_as_of:
        reason = f"the last closed bar is not {_day(want_as_of)} (run fetch-candles first)"
    elif not math.isfinite(bars.u[a_idx, i]):
        reason = "the as_of bar is missing"
    tau = math.nan
    if reason is None and a_idx is not None:
        tau = float(b_spot.signals(bars.u[: a_idx + 1, i])[0][-1])
        if not math.isfinite(tau):
            reason = "a hole in the lookback window"
    if reason is not None or a_idx is None:
        return {"id": f"no_decision:{symbol}:{_day(want_as_of)}", "kind": "no_decision", "symbol": symbol,
                "day": _day(want_as_of), "reason": reason or "the as_of bar is missing"}
    state = "long" if tau > 0 else "flat"
    return {
        "id": f"prediction:{symbol}:{_day(want_as_of)}", "kind": "prediction", "symbol": symbol,
        "day": _day(want_as_of), "as_of": iso(want_as_of), "as_of_ts": want_as_of,
        "decision": {"target_weight": dec(tau, 4), "state": state, "p": table[coin][f"p_{state}"],
                     "event": EVENT, "horizon_days": OUT_DAYS - REF_DAYS},
        "input_sha256": bars_sha256(bars.ts[: a_idx + 1], bars.u[: a_idx + 1, i]),
        "params_sha256": p_sha, "prereg_sha256": prereg_sha256,
        "versions": {"strategy": b_spot.STRATEGY_VERSION, "predlog": VERSION}, "code": code,
    }


def predict(
    bars: Bars,
    record_dir: Path,
    now: datetime,
    *,
    prereg_sha256: str,
    code: str = "unknown",
    stamp_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Append gap, outcome and today's lines for every coin; returns the lines written (none on a rerun)."""
    now_ms = int(now.timestamp() * 1000)
    today_ms = now_ms - now_ms % DAY_MS
    want_as_of = today_ms - DAY_MS  # the last bar that has closed at `now`
    table = train_table(bars)
    p_sha = params_sha256(table)
    index = bar_index(bars)
    written: list[dict[str, Any]] = []
    with _locked(record_dir):
        verify(record_dir, stamp_path)
        lines = read_trials(record_dir / PREDICTIONS)
        done = {line["id"] for line in lines}
        for i, coin in enumerate(b_spot.COINS):
            symbol = f"{coin}-EUR"
            mine = [line for line in lines if line.get("symbol") == symbol and line["kind"] != "outcome"]
            if mine:  # 1. gaps since the last day of this coin
                day_ms = int(datetime.fromisoformat(mine[-1]["day"]).replace(tzinfo=UTC).timestamp() * 1000)
                for d in range(day_ms + DAY_MS, want_as_of, DAY_MS):
                    body: dict[str, Any] = {
                        "id": f"gap:{symbol}:{_day(d)}", "kind": "gap", "symbol": symbol, "day": _day(d),
                        "reason": "no run",
                    }
                    if body["id"] not in done:
                        written.append(_append(record_dir, body, now_ms))
            for pred in (line for line in lines if line["kind"] == "prediction" and line["symbol"] == symbol):
                if f"outcome:{pred['id']}" in done:  # 2. outcomes that came due
                    continue
                outcome = outcome_body(bars, index, i, pred, now_ms)
                if outcome is not None:
                    written.append(_append(record_dir, outcome, now_ms))
            pid = f"prediction:{symbol}:{_day(want_as_of)}"  # 3. today's line
            nid = f"no_decision:{symbol}:{_day(want_as_of)}"
            if pid in done or nid in done:
                continue
            body = decide_body(bars, index, i, want_as_of, table, p_sha, prereg_sha256, code)
            written.append(_append(record_dir, body, now_ms))
        if stamp_path is not None and (record_dir / PREDICTIONS).exists():
            _stamp(record_dir, stamp_path)
    return written


def _ms_of(stamp: str) -> int:
    return int(datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp() * 1000)


def _drift(want: dict[str, Any], got: dict[str, Any]) -> list[str]:
    """Why a rebuilt decision line differs from the stored one (empty when equal)."""
    if got == want:
        return []
    if got.get("kind") != want.get("kind") or got.get("reason") != want.get("reason"):
        return ["DECISION_DRIFT"]  # e.g. a no_decision line that rebuilds as a prediction
    reasons = []
    if got.get("input_sha256") != want.get("input_sha256"):
        reasons.append("INPUT_DRIFT")
    if got.get("params_sha256") != want.get("params_sha256"):
        reasons.append("PARAMS_DRIFT")
    if got.get("decision") != want.get("decision"):
        reasons.append("DECISION_DRIFT" if reasons else "LOGIC_DRIFT")
    return reasons or ["LOGIC_DRIFT"]


def replay(
    record_dir: Path,
    load_bars: Callable[[datetime], Bars],
    prereg_sha256: str,
    seqs: set[int] | None = None,
) -> list[tuple[int, str]]:
    """Rebuild every prediction, no_decision and outcome line (or only `seqs`) from the bars known at its
    `committed_at`; returns (seq, reason) for each mismatch, empty when every line rebuilds.

    `load_bars(t)` must return the bars as they were known at `t` (closed and first ingested by `t`).
    Gap lines carry no data: the chain check in `verify` covers them.
    """
    lines = read_trials(record_dir / PREDICTIONS)
    by_seq = {int(line["seq"]): line for line in lines}
    cache: dict[str, tuple[Bars, dict[int, int], dict[str, dict[str, str | int]], str]] = {}
    out: list[tuple[int, str]] = []
    for line in lines:
        seq = int(line["seq"])
        if line["kind"] == "gap" or (seqs is not None and seq not in seqs):
            continue
        at = line["committed_at"]
        if at not in cache:
            bars = load_bars(datetime.fromtimestamp(_ms_of(at) / 1000, UTC))
            table = train_table(bars)
            cache[at] = (bars, bar_index(bars), table, params_sha256(table))
        bars, index, table, p_sha = cache[at]
        now_ms = _ms_of(at)
        want = _body_keys(line)
        i = b_spot.COINS.index(line["symbol"].split("-")[0])
        if line["kind"] == "outcome":
            pred = by_seq.get(int(line["prediction_seq"]))
            got = outcome_body(bars, index, i, pred, now_ms) if pred is not None else None
            if got != want:
                out.append((seq, "OUTCOME_DRIFT"))
            continue
        if line.get("prereg_sha256", prereg_sha256) != prereg_sha256:
            out.append((seq, "PREREG_MISMATCH"))
            continue
        want_as_of = now_ms - now_ms % DAY_MS - DAY_MS
        got = decide_body(bars, index, i, want_as_of, table, p_sha, prereg_sha256, str(line.get("code", "")))
        out.extend((seq, reason) for reason in _drift(want, got))
    if seqs is None and lines:  # an outcome the last run could write but no line holds
        last = lines[-1]["committed_at"]
        bars = load_bars(datetime.fromtimestamp(_ms_of(last) / 1000, UTC))
        index = bar_index(bars)
        outcomes = {line.get("prediction_seq") for line in lines if line["kind"] == "outcome"}
        for line in lines:
            if line["kind"] == "prediction" and line["seq"] not in outcomes:
                i = b_spot.COINS.index(line["symbol"].split("-")[0])
                if outcome_body(bars, index, i, line, _ms_of(last)) is not None:
                    out.append((int(line["seq"]), "OUTCOME_MISSING"))
    return out


def verify_public(path: Path) -> int:
    """What a reader with only the public commitments can check: the chain, contiguous `seq` and commit
    times that never go backwards. Number of lines when intact; raises ChainError otherwise."""
    broken = verify_chain(path)
    if broken is not None:
        raise ChainError(broken, f"{path.name}: prev_sha256 does not match the bytes before the line")
    last = ""
    lines = read_trials(path)
    for n, line in enumerate(lines, start=1):
        if line.get("seq") != n:
            raise ChainError(n, f"seq {line.get('seq')} is not contiguous")
        if "salt" in line:
            raise ChainError(n, "a public line carries the salt")
        if line["committed_at"] < last:
            raise ChainError(n, "committed_at goes backwards")
        last = line["committed_at"]
    return len(lines)
