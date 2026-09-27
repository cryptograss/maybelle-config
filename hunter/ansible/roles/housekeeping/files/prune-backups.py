#!/usr/bin/env python3
"""Bound the pickipedia backup mirror on hunter, without flattening it.

maybelle dumps the wiki daily, keeps three days, and rsyncs to hunter. The
prune runs on maybelle's copy and the rsync has no --delete, so hunter kept
every dump ever taken: 272 files, 53GB, back to 2025-12-18, growing ~6GB a
month on a 150GB disk that reached 90% in September 2026.

--delete would be the wrong fix. hunter is deliberately the long-retention
copy — preview environments restore from these (pickipedia#107). It needs a
bound of its own, not maybelle's three days.

So: daily for a fortnight, weekly for two months, monthly for a year. About
34 dumps, ~7GB, with a year of history still recoverable.

Deletes nothing without --apply.
"""

import argparse
import os
import re
import sys
from collections import OrderedDict
from datetime import date, datetime

DEFAULT_DIR = "/opt/magenta/pickipedia-backups"
NAME = re.compile(r"^pickipedia_(\d{4})(\d{2})(\d{2})\.sql\.gz$")

DAILY_DAYS = 14
WEEKLY_WEEKS = 8
MONTHLY_MONTHS = 12


def backups(directory):
    """Every dump in the directory, newest first, as (date, path, bytes)."""
    found = []
    for entry in os.scandir(directory):
        match = NAME.match(entry.name)
        if not match or not entry.is_file():
            continue
        try:
            when = date(*(int(g) for g in match.groups()))
        except ValueError:
            continue          # a filename that looks like a date but isn't one
        found.append((when, entry.path, entry.stat().st_size))
    return sorted(found, reverse=True)


def keepers(items, today):
    """Which dumps to keep, and why — the reason goes in the log.

    Every bucket keeps its NEWEST member, so a restore lands on the freshest
    dump of that week or month rather than an arbitrary one.
    """
    reasons = OrderedDict()
    weeks_seen, months_seen = {}, {}

    for when, path, _size in items:
        age = (today - when).days
        if age < 0:
            reasons[path] = "dated in the future — kept, and worth a look"
        elif age < DAILY_DAYS:
            reasons[path] = f"daily (day {age})"
        elif age < DAILY_DAYS + WEEKLY_WEEKS * 7:
            key = when.isocalendar()[:2]
            if key not in weeks_seen:
                weeks_seen[key] = path
                reasons[path] = f"weekly (week {key[1]} of {key[0]})"
        elif age <= 366:
            key = (when.year, when.month)
            if key not in months_seen:
                months_seen[key] = path
                reasons[path] = f"monthly ({when:%B %Y})"

    # Never leave the directory empty, whatever the arithmetic says.
    if not reasons and items:
        reasons[items[0][1]] = "newest dump — kept unconditionally"
    return reasons


def human(size):
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}GB"


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dir", default=DEFAULT_DIR)
    parser.add_argument("--apply", action="store_true",
                        help="actually delete; without it, only report")
    parser.add_argument("--today", help="pretend today is this date (YYYY-MM-DD), for testing")
    args = parser.parse_args()

    today = (datetime.strptime(args.today, "%Y-%m-%d").date()
             if args.today else date.today())

    if not os.path.isdir(args.dir):
        print(f"no such directory: {args.dir}", file=sys.stderr)
        return 1

    items = backups(args.dir)
    if not items:
        print(f"{args.dir}: no dumps found — nothing to do")
        return 0

    keep = keepers(items, today)
    drop = [(w, p, s) for (w, p, s) in items if p not in keep]
    kept_bytes = sum(s for (_w, p, s) in items if p in keep)
    drop_bytes = sum(s for (_w, _p, s) in drop)

    print(f"{args.dir}: {len(items)} dumps, {human(kept_bytes + drop_bytes)}")
    print(f"  keeping {len(keep)} ({human(kept_bytes)}), "
          f"removing {len(drop)} ({human(drop_bytes)})")

    for when, path, _size in reversed(items):
        if path in keep:
            print(f"  keep   {when}  {keep[path]}")

    for when, path, size in drop:
        if args.apply:
            try:
                os.remove(path)
            except OSError as exc:
                print(f"  FAILED {when}  {exc}", file=sys.stderr)
                continue
        print(f"  {'removed' if args.apply else 'would remove'} {when}  {human(size)}")

    if not args.apply and drop:
        print("\ndry run — pass --apply to delete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
