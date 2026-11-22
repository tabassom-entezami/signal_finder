"""Standing gap review: fail when a known gap is not handled.

Run: python3 tools/gap_check.py --checklist checklist.csv --register gaps.csv [--root .] [--plan PLAN.md ...]
                                [--max-age-days 7] [--today YYYY-MM-DD]
checklist.csv: item, evidence_path, evidence_grep, plan_id
    capabilities you expect to have; each names a file (relative to --root) and a regex that proves it.
gaps.csv: id, opened, item, state, disposition, plan_id
    the gap register; state `closed` ends a row, disposition `add` means "we will build it".
Fails when:
  1. a checklist item has no evidence on disk and no open register row with its plan_id;
  2. an open `add` gap older than --max-age-days has no plan_id, or (with --plan) no plan file holds a
     bold task heading `**<plan_id>**` or `**<plan_id> <title>**`.
Stdlib only, read-only.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import UTC, date, datetime
from pathlib import Path


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def evidence(root: Path, path: str, pattern: str) -> bool:
    p = root / path
    if not p.exists():
        return False
    if p.is_dir():
        return any(p.iterdir())
    return not pattern or re.search(pattern, p.read_text(encoding="utf-8", errors="ignore")) is not None


def has_task(plan: str, plan_id: str) -> bool:
    return re.search(r"\*\*" + re.escape(plan_id) + r"(\*\*|\s)", plan) is not None


def unhandled(checklist: list[dict[str, str]], gaps: list[dict[str, str]], root: Path, today: date,
              plan: str | None = None, max_age: int = 7) -> list[str]:
    fails = []
    open_ids = {g["plan_id"] for g in gaps if g["state"] != "closed" and g["plan_id"]}
    for c in checklist:
        if not evidence(root, c["evidence_path"], c["evidence_grep"]) and c.get("plan_id") not in open_ids:
            fails.append(f"missing with no gap row: {c['item']} ({c['evidence_path']})")
    for g in gaps:
        age = (today - date.fromisoformat(g["opened"])).days
        if g["state"] == "closed" or g["disposition"] != "add" or age <= max_age:
            continue
        if not g["plan_id"]:
            fails.append(f"{g['id']}: 'add' for {age} days without a plan task")
        elif plan is not None and not has_task(plan, g["plan_id"]):
            fails.append(f"{g['id']}: plan task {g['plan_id']} not found in the plan files")
    return fails


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Fail when a known gap is not handled.")
    p.add_argument("--checklist", type=Path, required=True)
    p.add_argument("--register", type=Path, required=True)
    p.add_argument("--root", type=Path, default=Path("."))
    p.add_argument("--plan", type=Path, nargs="*", default=[])
    p.add_argument("--max-age-days", type=int, default=7)
    p.add_argument("--today", type=date.fromisoformat, default=datetime.now(UTC).date())
    a = p.parse_args(argv)
    checklist, gaps = rows(a.checklist), rows(a.register)
    plan = "\n".join(f.read_text(encoding="utf-8") for f in a.plan) if a.plan else None
    fails = unhandled(checklist, gaps, a.root, a.today, plan, a.max_age_days)
    for f in fails:
        print("FAIL", f)
    print(f"{'PASS' if not fails else 'FAIL'} gaps: {len(checklist)} checklist items, "
          f"{len(gaps)} register rows, {len(fails)} unhandled")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
