"""Tests for the public process-tools kit in `tools/` (synthetic fixtures only)."""

from __future__ import annotations

import ast
import importlib.util
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest

KIT = Path(__file__).resolve().parents[1] / "tools"
STDLIB_OK = {"__future__", "argparse", "calendar", "csv", "dataclasses", "datetime", "json", "pathlib", "re",
             "sys", "time"}


def load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"kit_{name}", KIT / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


thread_lint = load("thread_lint")
run_log = load("run_log")
date_lint = load("date_lint")
stale_number_check = load("stale_number_check")
gap_check = load("gap_check")
doc_lint = load("doc_lint")

NOW = datetime(2030, 1, 2, 12, 0, tzinfo=UTC)


def test_kit_imports_only_allowed_stdlib_modules() -> None:
    for f in sorted(KIT.glob("*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        mods = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        mods |= {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        assert mods <= STDLIB_OK, (f.name, mods - STDLIB_OK)


# --- thread_lint ----------------------------------------------------------------------------------

GOOD = """# Thread
### Ada: first view
run: run-1
I think X.
ASK Ben: is Y measured?

### Ben: first view
run: run-2
Ada is right about X.
ANSWER Ada: yes, Y is measured.

### Cy: checker
run: run-3
Independent method: recomputed from the raw file instead of re-reading.
"""


def test_thread_clean_passes() -> None:
    assert thread_lint.gaps(GOOD, {"run-1", "run-2", "run-3"}) == []


def test_thread_author_who_ignores_earlier_author_is_a_gap() -> None:
    text = GOOD.replace("Ada is right about X.\nANSWER Ada: yes, Y is measured.", "X holds.")
    assert "Ben never replied to: Ada" in thread_lint.gaps(text)


def test_thread_parallel_first_round_owes_nothing() -> None:
    # Ada wrote before Ben, but Ada's only section came first: Ada owes no reply to Ben
    assert not any(g.startswith("Ada never") for g in thread_lint.gaps(GOOD))


def test_thread_unanswered_ask_is_a_gap_unless_carried() -> None:
    text = GOOD.replace("ANSWER Ada: yes, Y is measured.", "")
    assert "Ben has not answered the ASK from Ada" in thread_lint.gaps(text)
    closed = text + "\n### Dee: closed\nCARRY Ada -> Ben: measure Y in the build\n"
    assert "Ben has not answered the ASK from Ada" not in thread_lint.gaps(closed)


def test_thread_checker_needs_independent_method_and_must_exist() -> None:
    no_line = GOOD.replace("Independent method: recomputed from the raw file instead of re-reading.\n", "")
    assert "Cy (checker): checker section has no `Independent method:` line" in thread_lint.gaps(no_line)
    no_checker = GOOD.split("### Cy")[0]
    assert "no checker section yet" in thread_lint.gaps(no_checker)


def test_thread_run_lines_checked_against_log(tmp_path: Path) -> None:
    log = tmp_path / "runs.jsonl"
    log.write_text('{"id": "run-1"}\n{"id": "run-2"}\nnot json\n', encoding="utf-8")
    found = thread_lint.gaps(GOOD.replace("run: run-2\n", ""), thread_lint.logged_ids(log))
    assert "Ben (first view): section has no `run:` line" in found
    assert "Cy (checker): run run-3 is not in the run log" in found


def test_thread_main_exit_codes(tmp_path: Path) -> None:
    f = tmp_path / "t.md"
    f.write_text(GOOD, encoding="utf-8")
    assert thread_lint.main([str(f)]) == 0
    f.write_text(GOOD.split("### Cy")[0], encoding="utf-8")
    assert thread_lint.main([str(f)]) == 1


# --- run_log --------------------------------------------------------------------------------------

T0 = NOW.timestamp()


def test_run_log_add_finish_roundtrip(tmp_path: Path) -> None:
    log = tmp_path / "runs.jsonl"
    first = run_log.add(log, "Reviewer", "checker", "check a claim", T0, thread="t.md")
    second = run_log.add(log, "Builder", "builder", "build a thing", T0)
    assert (first, second) == ("run-20300102-001", "run-20300102-002")
    run_log.finish(log, first, 7, "accepted", "done", T0 + 60)
    rows, bad = run_log.load(log)
    assert bad == []
    assert rows[0]["status"] == "done" and rows[0]["tool_calls"] == 7 and rows[0]["cap"] == 15
    assert rows[1]["status"] == "running" and rows[1]["cap"] == 40
    with pytest.raises(ValueError):
        run_log.finish(log, "run-unknown", 1, None, "done", T0)


def test_run_log_check_flags_stale_bad_verdict_and_over_cap(tmp_path: Path) -> None:
    log = tmp_path / "runs.jsonl"
    rid = run_log.add(log, "Reviewer", "opinion", "one view", T0)
    fails, warns = run_log.problems(run_log.load(log)[0], T0 + 25 * 3600)
    assert fails == [f"{rid}: running for 25 h; finish it or mark it blocked/cancelled"]
    run_log.finish(log, rid, 9, None, "done", T0 + 60)
    rows = run_log.load(log)[0]
    fails, warns = run_log.problems(rows, T0 + 25 * 3600)
    assert fails == []
    assert warns == [f"{rid}: done with no verdict", f"{rid}: 9 tool calls over the 5-call cap"]
    rows[0]["verdict"] = "great"
    expected = f"{rid}: verdict 'great' is not one of accepted/revised/rejected"
    assert run_log.problems(rows, T0)[0] == [expected]


def test_run_log_malformed_line_is_reported_and_blocks_add(tmp_path: Path) -> None:
    log = tmp_path / "runs.jsonl"
    log.write_text('{"id": "x", "status": "done", "verdict": "accepted"}\n[1]\n{oops\n', encoding="utf-8")
    assert run_log.load(log)[1] == ["line 2: not an object", "line 3: not JSON"]
    with pytest.raises(ValueError):
        run_log.add(log, "Reviewer", "opinion", "t", T0)
    assert run_log.main(["--log", str(log), "check"]) == 1


# --- date_lint ------------------------------------------------------------------------------------


def test_date_future_time_and_date() -> None:
    assert date_lint.future("2030-01-02 12:30", NOW)
    assert not date_lint.future("2030-01-02 12:05", NOW)  # inside the clock skew
    assert not date_lint.future("2030-01-01 23:59", NOW)
    assert date_lint.future("2030-01-04", NOW)
    assert not date_lint.future("2030-01-02", NOW)
    assert not date_lint.future("soon", NOW)


def test_date_scan_reads_only_event_fields(tmp_path: Path) -> None:
    f = tmp_path / "log.md"
    rows = "| 2030-01-02 13:00 | started |\n| 2029-12-31 | done |\nDue by 2031-01-01.\n"
    f.write_text(rows, encoding="utf-8")
    assert date_lint.scan([f], date_lint.DEFAULT, NOW, timedelta(minutes=10)) == [f"{f}: 2030-01-02 13:00"]
    assert date_lint.main([str(f), "--now", NOW.isoformat()]) == 1
    assert date_lint.main([str(f), "--now", (NOW + timedelta(hours=2)).isoformat()]) == 0


# --- stale_number_check ---------------------------------------------------------------------------

RULES = [{"pattern": r"hit rate 61 ?%", "now": "54 %", "reason": "recount after a join bug"}]


def test_stale_number_found_and_correction_line_allowed(tmp_path: Path) -> None:
    (tmp_path / "notes").mkdir()
    doc = tmp_path / "notes" / "a.md"
    doc.write_text("The hit rate 61 % holds.\nThe hit rate 61 % was corrected to 54 %.\n", encoding="utf-8")
    hits = stale_number_check.stale(RULES, [tmp_path], [])
    assert hits == [f"{doc}:1: old claim /hit rate 61 ?%/ -> now 54 % (recount after a join bug)"]
    assert stale_number_check.stale(RULES, [tmp_path], [str(tmp_path / "notes")]) == []


def test_stale_number_main(tmp_path: Path) -> None:
    rules = tmp_path / "rules.csv"
    rules.write_text("pattern,now,reason\nhit rate 61 ?%,54 %,recount\n", encoding="utf-8")
    doc = tmp_path / "a.md"
    doc.write_text("hit rate 61%\n", encoding="utf-8")
    assert stale_number_check.main([str(doc), "--rules", str(rules)]) == 1
    doc.write_text("hit rate 54%\n", encoding="utf-8")
    assert stale_number_check.main([str(doc), "--rules", str(rules)]) == 0


# --- gap_check ------------------------------------------------------------------------------------

TODAY = date(2030, 1, 20)


def gap(gid: str, opened: str, state: str = "open", disposition: str = "add",
        plan_id: str = "") -> dict[str, str]:
    return {"id": gid, "opened": opened, "item": "x", "state": state, "disposition": disposition,
            "plan_id": plan_id}


def test_gap_missing_evidence_needs_an_open_row(tmp_path: Path) -> None:
    (tmp_path / "ci.yml").write_text("run: lint\n", encoding="utf-8")
    checklist = [{"item": "lint in CI", "evidence_path": "ci.yml", "evidence_grep": "lint", "plan_id": ""},
                 {"item": "backups", "evidence_path": "backup.md", "evidence_grep": "", "plan_id": "P7"}]
    missing = ["missing with no gap row: backups (backup.md)"]
    assert gap_check.unhandled(checklist, [], tmp_path, TODAY) == missing
    covered = [gap("g1", "2030-01-19", plan_id="P7")]
    assert gap_check.unhandled(checklist, covered, tmp_path, TODAY) == []
    closed = [gap("g1", "2030-01-19", state="closed", plan_id="P7")]
    assert len(gap_check.unhandled(checklist, closed, tmp_path, TODAY)) == 1


def test_gap_old_add_needs_plan_task(tmp_path: Path) -> None:
    gaps = [gap("g1", "2030-01-01"), gap("g2", "2030-01-01", plan_id="P9"), gap("g3", "2030-01-18")]
    assert gap_check.unhandled([], gaps, tmp_path, TODAY) == ["g1: 'add' for 19 days without a plan task"]
    fails = gap_check.unhandled([], gaps, tmp_path, TODAY, plan="- [ ] **P8 other**\n")
    assert "g2: plan task P9 not found in the plan files" in fails
    assert len(gap_check.unhandled([], gaps, tmp_path, TODAY, plan="- [ ] **P9 build it**\n")) == 1


def test_gap_main(tmp_path: Path) -> None:
    cl = tmp_path / "checklist.csv"
    cl.write_text("item,evidence_path,evidence_grep,plan_id\nreadme,README.md,,\n", encoding="utf-8")
    reg = tmp_path / "gaps.csv"
    reg.write_text("id,opened,item,state,disposition,plan_id\n", encoding="utf-8")
    args = ["--checklist", str(cl), "--register", str(reg), "--root", str(tmp_path), "--today", "2030-01-20"]
    assert gap_check.main(args) == 1
    (tmp_path / "README.md").write_text("hello\n", encoding="utf-8")
    assert gap_check.main(args) == 0


# --- doc_lint -------------------------------------------------------------------------------------


def test_doc_words_ignore_code_fences() -> None:
    text = "one two three\n```\n" + "word " * 50 + "\n```\n"
    assert doc_lint.words(text, 4) == []
    assert doc_lint.words(text, 3) == ["3 words, budget 3"]


def test_doc_marked_lines() -> None:
    text = "Fees are 0.2 % (SOURCE NEEDED).\nMargin 30 %.\nNo numbers here.\n"
    assert doc_lint.marked(text, "%", "SOURCE NEEDED") == ["line 2: Margin 30 %."]


def test_doc_ids_unique_contiguous_and_resolved() -> None:
    table = "| Q1 | a |\n| Q2 | b |\n"
    assert doc_lint.ids(table, "Q", "see Q2") == []
    assert doc_lint.ids(table + "| Q2 | c |\n", "Q") == ["duplicate ids: [2]"]
    assert doc_lint.ids("| Q1 | a |\n| Q3 | b |\n", "Q") == ["ids not numbered 1..n: [1, 3]"]
    assert doc_lint.ids(table, "Q", "see Q5") == ["named without a row: ['Q5']"]


def test_doc_main(tmp_path: Path) -> None:
    f = tmp_path / "d.md"
    f.write_text("| Q1 | a |\nsee Q4\n", encoding="utf-8")
    assert doc_lint.main(["ids", str(f), "--prefix", "Q"]) == 1
    assert doc_lint.main(["words", str(f), "--max", "100"]) == 0
