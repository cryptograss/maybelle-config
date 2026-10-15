#!/usr/bin/env python3
"""How an image build spent its time, from BuildKit's plain progress log.

The hunter deploy builds magenta-arthel with `--progress plain` into a log
(roles/build-image); this reads it and prints each step's time, slowest
first, and which steps came from the cache. Usage: build_timing.py LOG
"""

import re
import sys

STEP = re.compile(r'^#(\d+) \[([^\]]+)\] (.+)$')
DONE = re.compile(r'^#(\d+) DONE (\d+(?:\.\d+)?)s$')
CACHED = re.compile(r'^#(\d+) CACHED$')
ERROR = re.compile(r'^#(\d+) ERROR')


def steps(lines):
    """[(name, seconds or None, 'ran' | 'cached' | 'error' | 'unfinished')] in build order."""
    named, seconds, state = {}, {}, {}
    for line in lines:
        line = line.rstrip('\n')
        if m := STEP.match(line):
            named.setdefault(m[1], f'[{m[2].strip()}] {m[3]}')
        elif m := DONE.match(line):
            seconds[m[1]], state[m[1]] = float(m[2]), state.get(m[1], 'ran')
        elif m := CACHED.match(line):
            state[m[1]] = 'cached'
        elif m := ERROR.match(line):
            state[m[1]] = 'error'
    return [(name, seconds.get(n), state.get(n, 'unfinished')) for n, name in named.items()]


def report(found, top=12):
    ran = [s for s in found if s[2] == 'ran']
    lines = [f"{len(found)} steps: {len(ran)} ran ({sum(s[1] or 0 for s in ran):.0f}s), "
             f"{sum(s[2] == 'cached' for s in found)} cached"
             + ''.join(f", {sum(s[2] == k for s in found)} {k}" for k in ('error', 'unfinished')
                       if any(s[2] == k for s in found))]
    for name, secs, state in sorted(ran, key=lambda s: -(s[1] or 0))[:top]:
        lines.append(f'{secs:8.1f}s  {name[:110]}')
    lines += [f'   {state:>5}  {name[:110]}' for name, secs, state in found if state in ('error', 'unfinished')]
    first_ran = next((name for name, _, state in found if state == 'ran' and '/' in name), None)
    if first_ran:
        lines.append(f'cache stopped at: {first_ran[:110]}')
    return '\n'.join(lines)


if __name__ == '__main__':
    with open(sys.argv[1], errors='replace') as f:
        print(report(steps(f)))
