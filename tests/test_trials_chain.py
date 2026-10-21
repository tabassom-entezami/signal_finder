"""Hash chain of the gate trial log: an edit of any earlier line breaks it."""

import json
from pathlib import Path

import pytest

from signal_engine.validation.trials import counted, log_trial, read_trials, verify_chain


def test_new_lines_chain_over_old_unchained_lines(tmp_path: Path) -> None:
    log = tmp_path / "trials.jsonl"
    log.write_text(json.dumps({"id": "T1", "daily_sharpe": 0.02}) + "\n", encoding="utf-8")  # pre-chain line
    log_trial(log, {"id": "T2", "daily_sharpe": 0.01})
    log_trial(log, {"id": "T3", "daily_sharpe": 0.03})
    assert verify_chain(log) is None
    assert [t["id"] for t in counted(read_trials(log))] == ["T1", "T2", "T3"]


def test_editing_an_old_line_or_dropping_a_hash_breaks_the_chain(tmp_path: Path) -> None:
    log = tmp_path / "trials.jsonl"
    log.write_text(json.dumps({"id": "T1", "daily_sharpe": 0.02}) + "\n", encoding="utf-8")
    log_trial(log, {"id": "T2", "daily_sharpe": 0.01})
    good = log.read_text(encoding="utf-8")
    log.write_text(good.replace("0.02", "0.09"), encoding="utf-8")  # rewrite the unchained first line
    assert verify_chain(log) == 2
    log.write_text(good + json.dumps({"id": "T3"}) + "\n", encoding="utf-8")  # appended without a hash
    assert verify_chain(log) == 3


def test_missing_log_is_intact(tmp_path: Path) -> None:
    assert verify_chain(tmp_path / "none.jsonl") is None


def test_a_malformed_line_is_reported_not_raised(tmp_path: Path) -> None:
    log = tmp_path / "trials.jsonl"
    log_trial(log, {"id": "T1"})
    log.write_text(log.read_text(encoding="utf-8") + "{not json\n", encoding="utf-8")
    assert verify_chain(log) == 2


def test_a_log_without_a_final_newline_is_refused(tmp_path: Path) -> None:
    log = tmp_path / "trials.jsonl"
    log.write_text(json.dumps({"id": "T1"}), encoding="utf-8")
    with pytest.raises(ValueError, match="newline"):
        log_trial(log, {"id": "T2"})
