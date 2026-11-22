"""Generic checks for a set of Markdown documents.

Run:
  python3 tools/doc_lint.py words FILE --max 900
      the prose outside ``` fences stays under a word budget
  python3 tools/doc_lint.py marked FILE [--chars '€$%'] [--marker 'SOURCE NEEDED|ASSUMPTION']
      every line holding one of the chars (money, percentages) also holds a marker that says where the
      number comes from
  python3 tools/doc_lint.py ids FILE --prefix Q [--refs OTHER.md ...]
      table rows `| Q<n> |` are unique and numbered 1..n, and every `Q<n>` named in FILE or the --refs
      files has a row
Exit 0 when the check passes, 1 otherwise. Stdlib only, read-only.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


def prose(text: str) -> str:
    return re.sub(r"```.*?```", "", text, flags=re.S)


def words(text: str, limit: int) -> list[str]:
    n = len(prose(text).split())
    return [] if n < limit else [f"{n} words, budget {limit}"]


def marked(text: str, chars: str, marker: str) -> list[str]:
    rx = re.compile(marker)
    return [f"line {i}: {line[:90]}" for i, line in enumerate(text.splitlines(), 1)
            if any(c in line for c in chars) and not rx.search(line)]


def ids(text: str, prefix: str, refs: str = "") -> list[str]:
    p = re.escape(prefix)
    rows = [int(x) for x in re.findall(rf"^\|\s*{p}(\d+)\s*\|", text, re.M)]
    bad = []
    if len(rows) != len(set(rows)):
        bad.append(f"duplicate ids: {sorted({r for r in rows if rows.count(r) > 1})}")
    if sorted(set(rows)) != list(range(1, len(set(rows)) + 1)):
        bad.append(f"ids not numbered 1..n: {sorted(set(rows))}")
    named = {int(x) for x in re.findall(rf"\b{p}(\d+)\b", text + "\n" + refs)}
    if named - set(rows):
        bad.append(f"named without a row: {[f'{prefix}{n}' for n in sorted(named - set(rows))]}")
    return bad


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Generic checks for Markdown documents.")
    sub = p.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("words")
    w.add_argument("file", type=Path)
    w.add_argument("--max", type=int, required=True)
    m = sub.add_parser("marked")
    m.add_argument("file", type=Path)
    m.add_argument("--chars", default="€$%")
    m.add_argument("--marker", default="SOURCE NEEDED|ASSUMPTION")
    d = sub.add_parser("ids")
    d.add_argument("file", type=Path)
    d.add_argument("--prefix", required=True)
    d.add_argument("--refs", type=Path, nargs="*", default=[])
    a = p.parse_args(argv)
    text = a.file.read_text(encoding="utf-8")
    if a.cmd == "words":
        bad = words(text, a.max)
    elif a.cmd == "marked":
        bad = marked(text, a.chars, a.marker)
    else:
        bad = ids(text, a.prefix, "\n".join(f.read_text(encoding="utf-8") for f in a.refs))
    for b in bad:
        print("FAIL", b)
    print(f"{'PASS' if not bad else 'FAIL'} {a.cmd}: {a.file}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
