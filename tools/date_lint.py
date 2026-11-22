"""Fail any event time written later than now (hand-typed times drift into the future).

Run: python3 tools/date_lint.py FILE... [--pattern REGEX] [--now ISO] [--skew-minutes 10]
     python3 tools/date_lint.py --stamp     prints the current UTC time to paste into a record
Only event-time fields are checked, never deadlines in free text: by default the first cell of a
Markdown table row (`| YYYY-MM-DD HH:MM | ...`). `--pattern` sets another regex; its first group is the
stamp. A stamp is `YYYY-MM-DD` or `YYYY-MM-DD HH:MM` (UTC). A time may be up to the skew ahead (clock
drift); a date may not be later than today (UTC or local). Stdlib only, read-only.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

DEFAULT = r"^\|\s*(\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2})?)\s*\|"
STAMP = re.compile(r"(\d{4}-\d{2}-\d{2})(?:[ T](\d{2}:\d{2}))?")


def future(stamp: str, now: datetime, skew: timedelta = timedelta(minutes=10)) -> bool:
    """True when a `YYYY-MM-DD[ HH:MM]` stamp (UTC) lies after now."""
    m = STAMP.match(stamp.strip())
    if not m:
        return False
    day, hm = m.groups()
    if hm:
        return datetime.fromisoformat(f"{day}T{hm}:00+00:00") > now + skew
    return datetime.fromisoformat(day).date() > max(now.date(), now.astimezone().date())


def scan(paths: list[Path], pattern: str, now: datetime, skew: timedelta) -> list[str]:
    rx = re.compile(pattern, re.M)
    bad = []
    for path in paths:
        for m in rx.finditer(path.read_text(encoding="utf-8")):
            if future(m.group(1), now, skew):
                bad.append(f"{path}: {m.group(1).strip()}")
    return bad


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Fail event times that lie in the future.")
    p.add_argument("files", nargs="*", type=Path)
    p.add_argument("--pattern", default=DEFAULT, help="regex whose first group is the stamp")
    p.add_argument("--now", help="ISO time with offset (default: the clock)")
    p.add_argument("--skew-minutes", type=int, default=10)
    p.add_argument("--stamp", action="store_true")
    a = p.parse_args(argv)
    now = datetime.fromisoformat(a.now) if a.now else datetime.now(UTC)
    if a.stamp:
        print(now.strftime("%Y-%m-%d %H:%M"))
        return 0
    bad = scan(a.files, a.pattern, now, timedelta(minutes=a.skew_minutes))
    for b in bad:
        print("FAIL future event time", b)
    ok = f"PASS no event time after {now:%Y-%m-%d %H:%M} UTC"
    print(f"FAIL {len(bad)} future event time(s)" if bad else ok)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
