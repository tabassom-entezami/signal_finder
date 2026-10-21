"""`m1-oos`: the one-shot out-of-sample run of the M1 momentum rule, wired to its frozen rules file.

Refusals come first and in this order: the rules-file hash, the trial log, then (real data
only) the repository preconditions; only then are bars loaded. Development and tests use a
synthetic `BarSource`, whose bars must lie before the OOS start (loader lock).
"""

import hashlib
import importlib
import json
import math
import subprocess  # noqa: S404 - read-only git and pytest calls for the real-data preconditions
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any, Protocol

import numpy as np
import purgedcv
import pyarrow as pa
import pyarrow.compute as pc

from signal_engine.backtest.engine import Bars, Params, RunResult, run_path, strategy_inputs
from signal_engine.backtest.resolver import Episode, exit_table
from signal_engine.backtest.synthetic import synthetic_bars
from signal_engine.data import bitvavo, ecb, eurconv, pit
from signal_engine.settings import required
from signal_engine.strategy import b_spot
from signal_engine.validation import montecarlo, stats, trials

binance_vision: ModuleType | None
try:  # Private module, not in the public repo: create your own `signal_engine/data/binance_vision.py`
    # providing SOURCE (the warm-up bar source name used in storage) to load the warm-up bars
    binance_vision = importlib.import_module("signal_engine.data.binance_vision")
except ImportError:
    binance_vision = None

PREREG_SHA256 = "9352c2954a200c40413096dcacd4bda07707879a131e682dc1b50c21d5679aa0"
OOS_START_MS = int(datetime(2019, 8, 17, tzinfo=UTC).timestamp() * 1000)
PSR_MIN, DSR_MIN, PBO_MAX, EPISODES_MIN = 0.98, 0.95, 0.20, 30
PSR_FAIL = 0.90
TRAIN_START_MS = int(datetime(2019, 3, 8, tzinfo=UTC).timestamp() * 1000)
GRID_VOL, GRID_BAND, GRID_K = (0.15, 0.20, 0.25), (0.05, 0.10, 0.15), (3.0, 4.0, 5.0)
PBO_BLOCKS = 16
N_INFO = 35  # DSR at 9 + 26 grid members, information only
SUB_WINDOWS = (("2019-08-17", "2021-12-31"), ("2022-01-01", "2022-12-31"), ("2024-01-01", "2100-01-01"))
REQUIRED_TESTS = (
    "tests/test_stats.py",  # PSR 0.913 / 0.953, PBO on noise, DSR(N = 1) = PSR
    "tests/test_engine.py",  # vectorised parity, the 15-bar fixture, look-ahead
    "tests/test_resolver.py",
    "tests/test_b_spot.py",
    "tests/test_eurconv.py",  # an EURconv bar uses only same-bar or earlier inputs
    "tests/test_papersim.py",  # the third adapter of the 15-bar fixture
)
PSR_AGREE = 1e-6


class PreconditionError(RuntimeError):
    """The run refuses to start."""


class CodeDefectError(RuntimeError):
    """Own and purgedcv numbers disagree: a code defect, never a market verdict."""


class BarSource(Protocol):
    @property
    def real(self) -> bool: ...

    def load(self) -> Bars: ...


class RealBarSource:
    """Bitvavo EUR + EURconv + Binance warm-up bars from `data_dir`, assembled by `signal_engine.data`."""

    real = True

    def __init__(self, data_dir: Path, prereg_hash: str | None = None, now: datetime | None = None) -> None:
        self.data_dir = data_dir
        self.prereg_hash = prereg_hash  # given by m1-oos only; None keeps the loader lock closed
        self.now = now  # the clock for dropping unfinished bars; None = the wall clock

    def load(self) -> Bars:
        """Aligned BTC/ETH bars on one daily grid; a bar missing for a coin stays NaN (never filled).

        Every read goes through `pit.load` with the rules-file hash; only closed bars are used.
        """
        try:
            return self._load()
        except FileNotFoundError as exc:
            raise PreconditionError(f"real data missing: {exc}") from exc
        except pit.LoaderLockError as exc:
            raise PreconditionError(f"loader lock: {exc}") from exc

    def _load(self) -> Bars:
        now = self.now or datetime.now(UTC)

        def bitvavo_closed(symbol: str) -> pa.Table:
            table = pit.load(
                self.data_dir, bitvavo.SOURCE, symbol, prereg_hash=self.prereg_hash, purpose=pit.PURPOSE
            )
            return pit.closed(table, now)

        ecb_fixes = pit.load(self.data_dir, ecb.SOURCE, ecb.SYMBOL)
        usdc_eur = bitvavo_closed("USDC-EUR")
        if binance_vision is None:
            raise PreconditionError("warm-up bars need the private module signal_engine.data.binance_vision")
        eur, u = {}, {}
        for coin in b_spot.COINS:
            eur[coin] = bitvavo_closed(f"{coin}-EUR")
            warm = pit.load(self.data_dir, binance_vision.SOURCE, f"{coin}USDT", end=eurconv.ECB_FROM)
            u[coin] = eurconv.eurconv(eur[coin], ecb_fixes, usdc_eur, warm)
        tables = [t for t in (*eur.values(), *u.values()) if t.num_rows]
        first = min(int(pc.min(t.column("ts")).as_py()) for t in tables)
        last = max(int(pc.max(t.column("ts")).as_py()) for t in tables)
        ts = np.arange(first, last + pit.DAY_MS, pit.DAY_MS, dtype=np.int64)
        if not first < OOS_START_MS <= last:
            raise PreconditionError("real bars do not span the OOS start 2019-08-17")
        index = {int(t): n for n, t in enumerate(ts)}
        shape = (len(ts), len(b_spot.COINS))
        cols = {k: np.full(shape, np.nan) for k in ("open", "high", "low", "close", "u")}
        for i, coin in enumerate(b_spot.COINS):
            for row in eur[coin].select(["ts", "open", "high", "low", "close"]).to_pylist():
                for k in ("open", "high", "low", "close"):
                    cols[k][index[row["ts"]], i] = row[k]
            for row in u[coin].select(["ts", "close"]).to_pylist():
                cols["u"][index[row["ts"]], i] = row["close"]
        return Bars(
            ts, cols["open"], cols["high"], cols["low"], cols["close"], cols["u"], index[OOS_START_MS]
        )


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_prereg(prereg: Path) -> None:
    """The file, its .sha256 record and the compiled constant must all agree."""
    digest = file_sha256(prereg)
    record = prereg.with_suffix(".sha256")
    recorded = record.read_text(encoding="utf-8").split()[0] if record.exists() else ""
    if digest != PREREG_SHA256 or recorded != PREREG_SHA256:
        raise PreconditionError(
            f"rules-file hash mismatch: file {digest}, record {recorded or 'missing'}"
        )


def check_loader_lock(ts_ms: Sequence[int] | np.ndarray, unlocked: bool) -> None:
    """Any bar at or after the OOS start is refused unless m1-oos verified the hash on real data."""
    if not unlocked and np.any(np.asarray(ts_ms) >= OOS_START_MS):
        raise PreconditionError("loader lock: bars at or after 2019-08-17 outside a verified m1-oos run")


def missing_tests(root: Path) -> list[str]:
    return [f"required test file missing: {t}" for t in REQUIRED_TESTS if not (root / t).exists()]


def repo_preconditions(root: Path) -> tuple[list[str], str]:
    """(failures, commit) for the real run: clean tree and the reference tests green (git is read only)."""
    failures = missing_tests(root)
    status = subprocess.run(  # noqa: S603
        ["git", "status", "--porcelain"],  # noqa: S607
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if status.returncode != 0 or status.stdout.strip():
        failures.append("working tree is not clean")
    head = subprocess.run(  # noqa: S603
        ["git", "rev-parse", "HEAD"],  # noqa: S607
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    tests = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pytest", "-q", "-W", "error", "-o", "addopts=", *REQUIRED_TESTS],
        cwd=root,
        check=False,
    )
    if tests.returncode != 0:
        failures.append("reference tests are not green")
    return failures, head.stdout.strip()


def _jsonable(x: Any) -> Any:
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, list | tuple):
        return [_jsonable(v) for v in x]
    if isinstance(x, np.generic):
        x = x.item()
    if isinstance(x, float) and not math.isfinite(x):
        return None
    return x


def result_hash(obj: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(_jsonable(obj), sort_keys=True).encode()).hexdigest()


def input_hash(bars: Bars) -> str:
    h = hashlib.sha256()
    for arr in (bars.ts, bars.open, bars.high, bars.low, bars.close, bars.u):
        h.update(np.ascontiguousarray(arr).tobytes())
    return h.hexdigest()


def _path(
    bars: Bars, params: Params, variant: str = b_spot.TSMOM, vol: float = b_spot.VOL_TARGET
) -> RunResult:
    tau, sigma = strategy_inputs(bars, variant, vol)
    return run_path(bars, tau, sigma, params, start=bars.oos_start - 1)


def oos_records(bars: Bars) -> montecarlo.Records:
    """The bootstrap's daily records: every OOS day from 2019-08-17, each relative to the day before."""
    return montecarlo.records(bars, *strategy_inputs(bars), bars.oos_start - 1)


def _ms(day: str) -> int:
    return int(datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp() * 1000)


def evaluate(
    bars: Bars, n_trials: int, var_trials: float, seeds: Sequence[int], mc_paths: int
) -> dict[str, Any]:
    """Every gate and reported number of the frozen rules for one bar set."""
    base = Params()
    a = _path(bars, base)
    r = a.returns
    sr, skew, kurt = stats.sharpe_moments(r)
    t_obs = len(r)
    psr_own, psr_lib = stats.psr(sr, t_obs, skew, kurt), purgedcv.probabilistic_sharpe_ratio(r, 0.0)
    if abs(psr_own - psr_lib) > PSR_AGREE:
        raise CodeDefectError(f"PSR own {psr_own} vs purgedcv {psr_lib}: fix the code, then log T5-fix")
    v = max(var_trials, stats.sr_variance_floor(sr, t_obs, skew, kurt))
    dsr_own = stats.dsr(sr, t_obs, skew, kurt, n_trials, v)
    dsr_lib = purgedcv.deflated_sharpe_ratio(r, n_trials=n_trials, var_sharpe=v)
    grid = [(vt, bd, k) for vt in GRID_VOL for bd in GRID_BAND for k in GRID_K]
    matrix = np.column_stack([_path(bars, Params(k_stop=k, band=bd), vol=vt).returns for vt, bd, k in grid])
    pbo = stats.cscv_pbo(matrix, PBO_BLOCKS)
    trimmed = matrix[len(matrix) % PBO_BLOCKS :]
    pbo_lib = purgedcv.probability_of_backtest_overfitting(trimmed.T, n_splits=PBO_BLOCKS).pbo
    table = exit_table(a.episodes)
    mc = {s: montecarlo.simulate(oos_records(bars), s, mc_paths) for s in seeds}
    t6 = _path(bars, base, b_spot.DONCHIAN)
    t6_sr = stats.sharpe_moments(t6.returns)[0]
    gates = gate_table(
        (psr_own, psr_lib),
        (dsr_own, dsr_lib),
        n_trials,
        v,
        (pbo.pbo, pbo.pbo_strict, pbo_lib, pbo.n_combos),
        len(a.episodes),
        mc,
    )
    verdict = verdict_of(sr, gates)
    oos_ts = bars.ts[bars.oos_start - 1 :][1:]
    windows = {}
    for lo, hi in SUB_WINDOWS:
        sel = (oos_ts >= _ms(lo)) & (oos_ts <= _ms(hi))
        windows[f"{lo}..{hi}"] = stats.sharpe(r[sel]) if sel.sum() > 30 else None
    reported = {
        "daily_sharpe": sr,
        "skew": skew,
        "kurtosis": kurt,
        "oos_days": t_obs,
        "annual_sharpe": sr * math.sqrt(b_spot.ANN),
        "max_drawdown": a.max_drawdown(),
        "freezes": a.freezes,
        "strategy_layer_sharpe": stats.sharpe_moments(_path(bars, Params(account=False)).returns)[0],
        "cost_1x_sharpe": stats.sharpe_moments(
            _path(bars, Params(cost_side=0.002, cost_stop=0.0026)).returns
        )[0],
        "cost_3x_sharpe": stats.sharpe_moments(
            _path(bars, Params(cost_side=0.006, cost_stop=0.0078)).returns
        )[0],
        "t6_daily_sharpe": t6_sr,
        "t6_lookback_dependent": t6_sr < 0.5 * sr,
        "dsr_n35": stats.dsr(sr, t_obs, skew, kurt, N_INFO, v),
        "exit_table": table,
        "episodes_per_coin": [sum(1 for e in a.episodes if e.coin == i) for i in range(len(b_spot.COINS))],
        "sensitivity_map": {
            f"vol{vt}_band{bd}_k{k}": stats.sharpe(matrix[:, n]) for n, (vt, bd, k) in enumerate(grid)
        },
        "cpcv_path_sharpes": stats.cpcv_path_sharpes(matrix).tolist(),
        "sub_windows": windows,
        "mc": {str(s): asdict(m) for s, m in mc.items()},
        "per_coin": per_coin(a.episodes),
        "profit_factor": profit_factor(a.episodes),
        "time_in_market": float(np.mean(np.asarray(a.exposure[1:]) > 0.0)),
        "mean_exposure": float(np.mean(a.exposure[1:])),
        "no_decision_days": [
            int(np.sum(~np.isfinite(col))) for col in strategy_inputs(bars)[0][bars.oos_start - 1 : -1].T
        ],
        "t5_training_window": training_window(bars),
        # the full path A, so any later metric is derived from the record, never from a re-run
        "equity": a.equity.tolist(),
        "fills": [list(f) for f in a.fills],
    }
    return {"gates": gates, "verdict": verdict, "reported": reported}


def gate_table(
    psr: tuple[float, float],
    dsr: tuple[float, float],
    n_trials: int,
    var_sr: float,
    pbo: tuple[float, float, float, int],
    episodes: int,
    mc: dict[int, montecarlo.MCResult],
) -> dict[str, dict[str, Any]]:
    """Gates G-a to G-f of the frozen rules; own vs library: PSR/DSR use the lower, PBO the higher."""
    pbo_le0, pbo_lt0, pbo_lib, combos = pbo
    return {
        "G-a_psr": {"value": min(psr), "own": psr[0], "purgedcv": psr[1], "pass": min(psr) >= PSR_MIN},
        "G-b_dsr": {
            "value": min(dsr),
            "own": dsr[0],
            "purgedcv": dsr[1],
            "n_trials": n_trials,
            "var_sr": var_sr,
            "pass": min(dsr) > DSR_MIN,
        },
        "G-c_pbo": {
            "value": max(pbo_le0, pbo_lib),
            "own_le0": pbo_le0,
            "own_lt0": pbo_lt0,
            "purgedcv": pbo_lib,
            "combos": combos,
            "pass": max(pbo_le0, pbo_lib) <= PBO_MAX,
        },
        "G-d_episodes": {"value": episodes, "pass": episodes >= EPISODES_MIN},
        "G-e_p98_loss_usd": {
            "value": {str(s): m.p98_loss for s, m in mc.items()},
            "pass": all(m.p98_loss <= required("p98_max_usd") for m in mc.values()),
        },
        "G-f_savings": {
            "value": {str(s): m.median_pnl_pct for s, m in mc.items()},
            "pass": all(m.median_pnl_pct >= required("savings_pct") for m in mc.values()),
        },
    }


def verdict_of(daily_sharpe: float, gates: dict[str, dict[str, Any]]) -> str:
    """Verdict: FAIL at Sharpe <= 0 or PSR <= 0.90; PASS when every gate holds; MARGINAL otherwise."""
    if daily_sharpe <= 0.0 or gates["G-a_psr"]["value"] <= PSR_FAIL:
        return "FAIL"
    return "PASS" if all(g["pass"] for g in gates.values()) else "MARGINAL"


def per_coin(episodes: Sequence[Episode]) -> list[dict[str, Any]]:
    """The exit table and the summed PnL (share of C0) per coin, in `b_spot.COINS` order."""
    out = []
    for i, coin in enumerate(b_spot.COINS):
        mine = [e for e in episodes if e.coin == i]
        out.append({"coin": coin, "pnl": sum(e.pnl for e in mine), **exit_table(mine)})
    return out


def profit_factor(episodes: Sequence[Episode]) -> float | None:
    wins = sum(e.pnl for e in episodes if e.pnl > 0.0)
    losses = -sum(e.pnl for e in episodes if e.pnl <= 0.0)
    return wins / losses if losses > 0.0 else None


def training_window(bars: Bars) -> dict[str, Any] | None:
    """T5 on 2019-03-08 to 2019-08-16 only (information): the same rule, flat at the first EUR open.
    None when the bars do not cover that window (synthetic data)."""
    hits = np.flatnonzero(bars.ts == TRAIN_START_MS)
    if not len(hits) or hits[0] < 1 or hits[0] >= bars.oos_start:
        return None
    k, end = int(hits[0]), bars.oos_start
    o, h, lo, c, u = (x[:end] for x in (bars.open, bars.high, bars.low, bars.close, bars.u))
    sub = Bars(bars.ts[:end], o, h, lo, c, u, k)
    tau, sigma = strategy_inputs(sub)
    res = run_path(sub, tau, sigma, Params(), start=k - 1)
    sr = stats.sharpe(res.returns)
    return {
        "days": len(res.returns),
        "daily_sharpe": sr,
        "annual_sharpe": sr * math.sqrt(b_spot.ANN),
        "max_drawdown": res.max_drawdown(),
        "episodes": len(res.episodes),
    }


def run_m1_oos(
    prereg: Path,
    data_dir: Path,
    seeds: Sequence[int],
    out: Path,
    report: Path,
    source: BarSource,
    *,
    preconditions: Callable[[], tuple[list[str], str]] | None = None,
    mc_paths: int = montecarlo.PATHS,
) -> dict[str, Any]:
    verify_prereg(prereg)
    lines = trials.read_trials(data_dir / "trials" / "trials.jsonl")
    try:
        trials.check_registered(lines, PREREG_SHA256)
    except ValueError as exc:
        raise PreconditionError(str(exc)) from exc
    commit = "synthetic"
    if source.real:
        check = preconditions or (lambda: repo_preconditions(prereg.resolve().parents[2]))
        failures, commit = check()
        if failures:
            raise PreconditionError("; ".join(failures))
    bars = source.load()
    check_loader_lock(bars.ts, unlocked=source.real)
    body = {
        "prereg_sha256": PREREG_SHA256,
        "strategy_version": b_spot.STRATEGY_VERSION,
        "data_mode": "real" if source.real else "synthetic",
        "input_sha256": input_hash(bars),
        "commit": commit,
        "seeds": list(seeds),
        **evaluate(bars, trials.n_trials(lines), trials.sharpe_variance(lines), seeds, mc_paths),
    }
    body = _jsonable(body)
    body["result_sha256"] = result_hash(body)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(_report(body), encoding="utf-8")
    return dict(body)


DEV_DAYS, DEV_TRIALS, DEV_SEEDS, DEV_PATHS = 700, 9, (1, 2), 200


def _rounded(x: Any) -> Any:
    """Floats to 9 significant digits, so the dev hash does not depend on the last bits of a platform."""
    if isinstance(x, dict):
        return {k: _rounded(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_rounded(v) for v in x]
    return float(f"{x:.9g}") if isinstance(x, float) and math.isfinite(x) else x


def reproduce_dev(seed: int, out: Path, mc_paths: int = DEV_PATHS) -> dict[str, Any]:
    """The whole M1 path on synthetic bars: rule, backtest, gates, Monte Carlo, result hash.

    Needs no real data, rules file or trial log; the account values come from the environment. The same
    seed and values are meant to give the same `result_sha256` on any machine; the pinned test checks it.
    """
    bars = synthetic_bars(seed, DEV_DAYS)
    body = {
        "data_mode": "synthetic-dev",
        "seed": seed,
        "strategy_version": b_spot.STRATEGY_VERSION,
        "input_sha256": input_hash(bars),
        # the gates only keep pass/fail of the caps, so the values themselves go into the hash
        "account": {k: required(k) for k in ("stake_usd", "p98_max_usd", "savings_pct", "op_events")},
        **evaluate(bars, DEV_TRIALS, 0.0, DEV_SEEDS, mc_paths),
    }
    body = _rounded(_jsonable(body))
    body["result_sha256"] = result_hash(body)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return dict(body)


def _report(body: dict[str, Any]) -> str:
    title = (
        "M1 OOS result" if body["data_mode"] == "real" else "SYNTHETIC development run (not the M1 result)"
    )
    rows = "\n".join(
        f"| {name} | {json.dumps(g['value'])} | {'pass' if g['pass'] else 'FAIL'} |"
        for name, g in body["gates"].items()
    )
    return (
        f"# {title}\n\n- Verdict: **{body['verdict']}**\n- Pre-registration: `{body['prereg_sha256']}`\n"
        f"- Input: `{body['input_sha256']}`\n- Commit: `{body['commit']}`\n"
        f"- Result: `{body['result_sha256']}`\n\n"
        f"| Gate | Value | Result |\n|---|---|---|\n{rows}\n"
    )
