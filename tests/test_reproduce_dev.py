import json
import math
import platform
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from signal_engine.__main__ import main
from signal_engine.backtest.synthetic import synthetic_bars
from signal_engine.validation import m1_oos

# Demo account values for the synthetic run (not anyone's real values); the README command uses the same.
DEMO_ENV = {
    "STAKE_USD": "1000",
    "P98_MAX_USD": "100",
    "SAVINGS_PCT": "0.03",
    "OP_EVENTS": "[[0.01, 0.1, 0.5]]",
}
# Pinned on macOS arm64 with this uv.lock; other platforms must match the golden body below.
EXPECTED_SHA256 = "89d912618a3cf9ae43a2661249a885a89c152fd74cd16ed8ce327a4d8b43863d"
PINNED_PLATFORM = sys.platform == "darwin" and platform.machine() == "arm64"
# The whole seed-7 body without its hashes; floats match to rel 1e-6, everything else exactly.
GOLDEN = Path(__file__).parent / "golden" / "reproduce_dev_seed7.json"
# PBO counts winning splits out of C(16, 8) = 12,870, so one flipped split moves it by 7.8e-5; a 1-ulp change
# of the raw bars moved it by up to 17 splits (1.3e-3), far from the 0.20 limit.
PBO_ABS = 2e-3


@pytest.fixture(autouse=True)
def _demo_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in DEMO_ENV.items():
        monkeypatch.setenv(name, value)


@pytest.fixture(scope="module")
def body(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    with pytest.MonkeyPatch.context() as mp:
        for name, value in DEMO_ENV.items():
            mp.setenv(name, value)
        out = tmp_path_factory.mktemp("dev") / "dev.json"
        assert main(["reproduce-dev", "--seed", "7", "--out", str(out)]) == 0
    loaded: dict[str, Any] = json.loads(out.read_text())
    return loaded


def diffs(want: Any, got: Any, path: str = "") -> list[str]:
    """Every leaf where `got` differs from `want`, with both values."""
    if isinstance(want, dict) and isinstance(got, dict):
        out = [f"{path}/{k}: missing in {'got' if k in want else 'golden'}" for k in want.keys() ^ got.keys()]
        return out + [d for k in want.keys() & got.keys() for d in diffs(want[k], got[k], f"{path}/{k}")]
    if isinstance(want, list) and isinstance(got, list):
        if len(want) != len(got):
            return [f"{path}: length {len(want)} != {len(got)}"]
        return [d for i, (w, g) in enumerate(zip(want, got, strict=True)) for d in diffs(w, g, f"{path}/{i}")]
    if isinstance(want, float) and isinstance(got, float):
        if math.isnan(want) and math.isnan(got):
            return []
        pbo = path.startswith("/gates/G-c_pbo/")
        near = pytest.approx(want, rel=0.0 if pbo else 1e-6, abs=PBO_ABS if pbo else 0.0)
        return [] if got == near else [f"{path}: golden {want} != {got}"]
    return [] if type(want) is type(got) and want == got else [f"{path}: golden {want!r} != {got!r}"]


def test_the_synthetic_end_to_end_run_matches_the_golden_body(body: dict[str, Any]) -> None:
    want = json.loads(GOLDEN.read_text())
    got = {k: v for k, v in body.items() if k not in {"result_sha256", "input_sha256"}}
    found = diffs(want, got)
    assert not found, f"{len(found)} fields differ from {GOLDEN.name}:\n" + "\n".join(found[:50])


@pytest.mark.xfail(
    not PINNED_PLATFORM, reason="hash pinned on macOS arm64; elsewhere the golden body test", strict=False
)
def test_the_synthetic_end_to_end_run_gives_the_published_hash(body: dict[str, Any]) -> None:
    assert body["result_sha256"] == EXPECTED_SHA256


def test_the_dev_hash_covers_the_account_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base = m1_oos.reproduce_dev(7, tmp_path / "a.json", mc_paths=20)["result_sha256"]
    monkeypatch.setenv("P98_MAX_USD", "101")  # a cap enters the gates only as pass/fail
    assert m1_oos.reproduce_dev(7, tmp_path / "b.json", mc_paths=20)["result_sha256"] != base


def test_the_dev_bars_drop_the_last_float_bits_of_a_platform() -> None:
    bars = synthetic_bars(7, 50)
    nudged = replace(bars, close=np.nextafter(bars.close, np.inf), low=np.nextafter(bars.low, -np.inf))
    a, b = m1_oos._rounded_bars(bars), m1_oos._rounded_bars(nudged)
    assert m1_oos.input_hash(a) == m1_oos.input_hash(b)


def test_the_dev_hash_ignores_the_last_float_bits() -> None:
    assert m1_oos._rounded({"a": [0.1 + 0.2, 1.0]}) == {"a": [0.3, 1.0]}
    kept = m1_oos._rounded({"n": float("nan"), "k": 3})
    assert math.isnan(kept["n"]) and kept["k"] == 3
