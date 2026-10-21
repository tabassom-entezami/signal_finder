import json
import math
from pathlib import Path

import pytest

from signal_engine.__main__ import main
from signal_engine.validation import m1_oos

# Demo account values for the synthetic run (not anyone's real values); the README command uses the same.
DEMO_ENV = {
    "STAKE_USD": "1000",
    "P98_MAX_USD": "100",
    "SAVINGS_PCT": "0.03",
    "OP_EVENTS": "[[0.01, 0.1, 0.5]]",
}
EXPECTED_SHA256 = "0b87ed0e3d3dffd496dba03cc94feb58d044b44fa8633c2662aceff34df19270"


@pytest.fixture(autouse=True)
def _demo_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in DEMO_ENV.items():
        monkeypatch.setenv(name, value)


def test_the_synthetic_end_to_end_run_gives_the_published_hash(tmp_path: Path) -> None:
    assert main(["reproduce-dev", "--seed", "7", "--out", str(tmp_path / "dev.json")]) == 0
    body = json.loads((tmp_path / "dev.json").read_text())
    assert body["result_sha256"] == EXPECTED_SHA256
    assert body["data_mode"] == "synthetic-dev" and body["verdict"] in {"PASS", "MARGINAL", "FAIL"}
    assert set(body["gates"]) >= {"G-a_psr", "G-b_dsr"}


def test_the_dev_hash_covers_the_account_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("P98_MAX_USD", "101")  # a cap enters the gates only as pass/fail
    assert m1_oos.reproduce_dev(7, tmp_path / "dev.json")["result_sha256"] != EXPECTED_SHA256


def test_the_dev_hash_ignores_the_last_float_bits() -> None:
    assert m1_oos._rounded({"a": [0.1 + 0.2, 1.0]}) == {"a": [0.3, 1.0]}
    kept = m1_oos._rounded({"n": float("nan"), "k": 3})
    assert math.isnan(kept["n"]) and kept["k"] == 3
