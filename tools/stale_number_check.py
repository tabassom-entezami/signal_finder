"""Fail when a corrected number is still cited.

Run: python3 tools/stale_number_check.py --rules superseded.csv PATH... [--skip PREFIX ...]
The rules file is a CSV with the columns `pattern` (a regex for the old claim), `now` (the current
value) and `reason`. Each PATH is a file or a folder (all `*.md` files below it). A line that carries
a correction note ("corrected", "superseded", "was") may still name the old value. Dated records that
nobody may edit afterwards go in `--skip`. Stdlib only, read-only.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

ALLOWED_LINE = re.compile(r"corrected|superseded|\bwas\b", re.I)


def files(paths: list[Path], skip: list[str]) -> list[Path]:
    out = []
    for p in paths:
        for f in sorted(p.rglob("*.md")) if p.is_dir() else [p]:
            if not f.as_posix().startswith(tuple(skip)):
                out.append(f)
    return out


def stale(rules: list[dict[str, str]], paths: list[Path], skip: list[str]) -> list[str]:
    hits = []
    for f in files(paths, skip):
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if ALLOWED_LINE.search(line):
                continue
            for r in rules:
                if re.search(r["pattern"], line):
                    hits.append(f"{f}:{n}: old claim /{r['pattern']}/ -> now {r['now']} ({r['reason']})")
    return hits


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Fail when a corrected number is still cited.")
    p.add_argument("paths", nargs="+", type=Path)
    p.add_argument("--rules", type=Path, required=True, help="CSV with pattern,now,reason")
    p.add_argument("--skip", nargs="*", default=[], help="path prefixes not checked")
    a = p.parse_args(argv)
    with a.rules.open(encoding="utf-8") as fh:
        rules = list(csv.DictReader(fh))
    hits = stale(rules, a.paths, a.skip)
    for h in hits:
        print("FAIL", h)
    print(f"{'PASS' if not hits else 'FAIL'} stale numbers: {len(rules)} rules, {len(hits)} stale citations")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
