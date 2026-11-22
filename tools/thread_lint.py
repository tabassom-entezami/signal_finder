"""Check that the authors of a discussion thread really replied to each other.

A thread is a Markdown file of sections. Each section starts with a heading `### <Author>: <label>`
(two or three `#`). A label containing `checker` marks a checker section; the label `closed` marks
the closing section. Gaps:
  1. an author whose sections never name an author who wrote before its last section;
  2. a line `ASK <Author>: ...` with no later section by that author holding `ANSWER <asker>:`,
     unless the closing section carries it with a line `CARRY <asker> -> <Author>: ...`;
  3. no checker section, or a checker section without an `Independent method:` line
     (how its method differs from the author's: a re-run of the same method proves little);
  4. with `--runs FILE` (a JSON-lines run log, one object with an `id` per line): a section,
     the closing one excepted, without a `run: <id>` line naming a logged id.

Run: python3 tools/thread_lint.py THREAD.md [--runs runs.jsonl]
Exit 0 when there is no gap, 1 otherwise. Stdlib only, read-only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

HEAD = re.compile(r"^#{2,3} ([^:\n]+):[ \t]*([^\n]*)$", re.M)
ASK = re.compile(r"^\s*[-*]*\s*\**ASK ([^:\n]+):", re.M)
RUN = re.compile(r"^\s*run:\s*(\S+)", re.M)
INDEPENDENT = re.compile(r"^\s*[-*]*\s*\**Independent method:\**\s*\S", re.M | re.I)


@dataclass(frozen=True)
class Section:
    author: str
    label: str
    body: str

    @property
    def checker(self) -> bool:
        return "checker" in self.label.lower()

    @property
    def closed(self) -> bool:
        return self.label.strip().lower() == "closed"


def sections(text: str) -> list[Section]:
    marks = list(HEAD.finditer(text))
    out = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        out.append(Section(m.group(1).strip(), m.group(2).strip(), text[m.end() : end]))
    return out


def names(text: str, author: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(author)}(?!\w)", text) is not None


def gaps(text: str, run_ids: set[str] | None = None) -> list[str]:
    secs = sections(text)
    plain = [i for i, s in enumerate(secs) if not s.checker and not s.closed]
    closing = "".join(s.body for s in secs if s.closed)
    found = []
    for author in dict.fromkeys(secs[i].author for i in plain):
        mine = [i for i in plain if secs[i].author == author]
        said = "".join(secs[i].body for i in mine)
        earlier = {secs[i].author for i in plain if i < mine[-1]} - {author}
        missing = sorted(e for e in earlier if not names(said, e))
        if missing:
            found.append(f"{author} never replied to: {', '.join(missing)}")
    for i, s in enumerate(secs):
        for target in (t.strip() for t in ASK.findall(s.body)):
            later = "".join(t.body for t in secs[i + 1 :] if t.author == target)
            carried = rf"CARRY {re.escape(s.author)}\s*(?:->|→)\s*{re.escape(target)}:"
            if f"ANSWER {s.author}:" not in later and not re.search(carried, closing):
                found.append(f"{target} has not answered the ASK from {s.author}")
    if not any(s.checker for s in secs):
        found.append("no checker section yet")
    for s in secs:
        if s.checker and not INDEPENDENT.search(s.body):
            found.append(f"{s.author} ({s.label}): checker section has no `Independent method:` line")
    for s in secs:
        if run_ids is None or s.closed:
            continue
        m = RUN.search(s.body)
        if not m:
            found.append(f"{s.author} ({s.label}): section has no `run:` line")
        elif m.group(1) not in run_ids:
            found.append(f"{s.author} ({s.label}): run {m.group(1)} is not in the run log")
    return found


def logged_ids(path: Path) -> set[str]:
    ids = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and "id" in row:
            ids.add(str(row["id"]))
    return ids


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Check that thread authors replied to each other.")
    p.add_argument("thread", type=Path)
    p.add_argument("--runs", type=Path, help="JSON-lines run log; each section must name a logged run id")
    a = p.parse_args(argv)
    found = gaps(a.thread.read_text(encoding="utf-8"), logged_ids(a.runs) if a.runs else None)
    for g in found:
        print("GAP", g)
    print(f"FAIL {len(found)} gap(s)" if found else "PASS authors replied to each other, checker present")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
