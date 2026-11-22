#!/usr/bin/env python3
"""Hourly counts from a Caddy access log, and three windows drawn from them.

GoAccess (the /stats/ dashboard beside this) counts everything since it started and cannot be asked
"the last three hours"; its panels also hold only the top fifty of anything, so a wiki's long tail of
pages never shows. And the questions Justin asked in #performance on 22 Nov -- how much of this is
people, which pages they read, which post sent them here, which links they follow -- need the query
string and the referring URL, both of which the dashboard is told to drop.

The log has all of it. This reads the same file, a minute at a time, and keeps its own count:

  - hours.json and salt.json, in a directory of its own that nothing serves: one bucket an hour,
    kept HOURS_KEPT hours, and the day's salt. Neither ever goes near the served directory --
    Caddy hands out any file under it, and the salt with a visitor's number would be an address
    back again.
  - traffic.json, written into the directory Caddy serves at /stats/: the three windows, made from
    those buckets, small enough to hand a browser.

What it will not keep, which is the same line the dashboard holds (and why this is safe to serve in
the open):

  - No addresses. A visitor is a number made from the address, the browser's name and a salt that
    changes every day, so the same person on two days cannot be joined up, and nothing can be turned
    back into an address.
  - A page keeps only the parts of its query string that say which page it is (QUERY_KEPT). An OAuth
    code, a token, a search someone typed: dropped before anything is written.
  - A referring URL keeps its host and path, never its query string: that is where a search engine
    puts what was typed.
  - Paths that carry a live secret never reach the log at all (skip_log, in each site's Caddyfile).

Run as the caddy user, once a minute, by the timer beside this.  Usage:

  traffic-rollup.py --log /var/log/caddy/magenta-access.log --state /var/lib/traffic-rollup/magenta \
                    --out /var/lib/goaccess/magenta/traffic.json --site magenta --host magenta.cryptograss.live
"""

import argparse
import hashlib
import json
import os
import re
import secrets
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

HOURS_KEPT = 24 * 9           # buckets kept: a week's window, with a day or two of slack
WINDOWS = (('3h', 3), ('24h', 24), ('7d', 24 * 7))
VISIT_GAP = 30 * 60           # seconds of quiet that make the next request a new visit
MOST = {'pages': 25, 'from': 15, 'followed': 12, 'missing': 6}   # rows a window shows, per list
KEEP = {'pages': 300, 'from': 120, 'followed': 120, 'missing': 40}  # rows an hour keeps, per list
MAX_LINES = 400_000           # lines read in one run: a minute's worth is ~250, so this is a long catch-up

# What a page's query string may keep: enough to say which page it is, and nothing else. MediaWiki's
# own (title, curid, oldid, diff, action), and the search box's.
QUERY_KEPT = {'title', 'curid', 'oldid', 'diff', 'action', 'search', 'section', 'uselang'}

# Requests that aren't someone reading something: the furniture of a page, and the dashboard itself.
FURNITURE = re.compile(r'\.(?:css|js|mjs|map|png|jpe?g|gif|svg|webp|avif|ico|woff2?|ttf|eot|mp3|m4a|ogg|mp4|webm|ts|m3u8)$'
                       r'|^/(?:load\.php|resources/|skins/|static/|assets/|favicon|apple-touch|robots\.txt|sitemap)'
                       r'|^/stats/')

# Our own machinery, told apart from both people and strangers' crawlers: each emergent's runner, the
# pollers, the walks, anything of ours that speaks for itself in its user agent.
OURS = re.compile(r'magenta|memory-lane|pickipedia-mcp|artificial.?dumb|connectome|rabbithole-check|'
                  r'python-requests|python-urllib|aiohttp|httpx|curl/|wget|node-fetch|got \(|okhttp', re.I)

# Everyone else's robots. The name a crawler gives itself is the only thing a log has to go on: a
# crawler that lies is counted as a person, and nothing here can tell the difference.
CRAWLERS = re.compile(r'bot\b|bot/|spider|crawler|crawling|slurp|facebookexternalhit|feedfetcher|'
                      r'bingpreview|yandex|baidu|duckduck|ahrefs|semrush|mj12|dotbot|petalbot|'
                      r'applebot|gptbot|claudebot|claude-web|ccbot|perplexity|amazonbot|bytespider|'
                      r'headlesschrome|phantomjs|scrapy|python-httpx|archive\.org_bot|ia_archiver|'
                      r'wpbot|zgrab|masscan|censys|internet-?measurement', re.I)

WHO = ('people', 'crawlers', 'ours')


def hour_of(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime('%Y-%m-%dT%H')


def salt_for(day, state_dir):
    """A salt a day, made once and kept owner-only, in the state directory, which nothing serves: the
    day's visitor numbers can't be joined to another day's, and no number can be turned back into an
    address."""
    path = Path(state_dir) / 'salt.json'
    try:
        salts = json.loads(path.read_text())
    except (OSError, ValueError):
        salts = {}
    if day not in salts:
        salts = {d: s for d, s in salts.items() if d >= day_before(day, 9)}
        salts[day] = secrets.token_hex(16)
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(salts))
        os.chmod(tmp, 0o600)
        tmp.replace(path)
    return salts[day]


def day_before(day, days):
    return (datetime.strptime(day, '%Y-%m-%d') - timedelta(days=days)).strftime('%Y-%m-%d')


def who_is_it(agent):
    if not agent:
        return 'crawlers'          # a browser always says; something that doesn't is not a reader
    if OURS.search(agent):
        return 'ours'
    if CRAWLERS.search(agent):
        return 'crawlers'
    return 'people'


def page_of(uri):
    """The page a request asks for: its path, and only those query parts that say which page it is.
    Everything else in the query string is dropped before this returns (QUERY_KEPT)."""
    split = urlsplit(uri)
    path = split.path or '/'
    kept = [(k, v) for k, v in parse_qsl(split.query, keep_blank_values=False) if k in QUERY_KEPT]
    if not kept:
        return path
    # title first, so /index.php?title=Doc_Watson reads as the page it is
    kept.sort(key=lambda kv: (kv[0] != 'title', kv[0]))
    return path + '?' + '&'.join(f'{k}={v}' for k, v in kept[:3])


def referrer_of(url, host):
    """(kind, what): 'from' and a host/path for somewhere else, 'followed' and the path for one of our
    own pages, (None, None) for no referrer. Never the query string: a search engine puts what was
    typed in there."""
    if not url:
        return None, None
    split = urlsplit(url)
    if not split.netloc:
        return None, None
    path = split.path or '/'
    if split.netloc.lower().removeprefix('www.') == host.lower().removeprefix('www.'):
        return 'followed', path
    return 'from', split.netloc + (path if path != '/' else '')


def empty_hour():
    return {'req': dict.fromkeys(WHO, 0), 'visits': dict.fromkeys(WHO, 0), 'bytes': 0,
            'status': {}, 'pages': {}, 'from': {}, 'followed': {}, 'missing': {}}


def trim(bucket):
    """An hour keeps only its busiest rows: a crawler walking a wiki would otherwise write a list as
    long as the wiki."""
    for name, keep in KEEP.items():
        rows = bucket[name]
        if len(rows) > keep:
            bucket[name] = dict(sorted(rows.items(), key=lambda kv: -kv[1])[:keep])


def read_lines(log, state):
    """The lines written since last time. Caddy rolls the log by renaming it and starting a new one
    (lumberjack), so when the file under our path is a different one, what was left in the old one is
    read first, by the name it was rolled to."""
    log = Path(log)
    lines, where = [], dict(state)
    try:
        now = log.stat()
    except FileNotFoundError:
        return lines, where
    rolled = where.get('inode') not in (None, now.st_ino)
    if rolled or now.st_size < where.get('offset', 0):
        if rolled:
            lines += tail_of_rolled(log, where)
        where['offset'] = 0
    with log.open('r', errors='replace') as f:
        f.seek(min(where.get('offset', 0), now.st_size))
        for line in f:
            if not line.endswith('\n'):      # a line still being written: next time
                break
            lines.append(line)
            if len(lines) >= MAX_LINES:
                break
        where['offset'] = f.tell()
    where['inode'] = now.st_ino
    return lines, where


def tail_of_rolled(log, where):
    """What was left unread in the file our path used to point at, found by its inode among the rolled
    ones beside it (caddy writes <name>-<when>.log)."""
    for other in sorted(log.parent.glob(log.stem + '-*' + log.suffix), reverse=True):
        try:
            if other.stat().st_ino != where.get('inode'):
                continue
            with other.open('r', errors='replace') as f:
                f.seek(where.get('offset', 0))
                return f.readlines()
        except OSError:
            return []
    return []


def count(lines, hours, seen, state_dir, host):
    """Each line into its hour's bucket. `seen` is the last time each visitor was heard, so a request
    after VISIT_GAP of quiet counts as a new visit."""
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get('logger') != 'http.log.access' and 'request' not in row:
            continue
        req = row.get('request') or {}
        ts = row.get('ts')
        if not isinstance(ts, (int, float)):
            continue
        headers = req.get('headers') or {}
        agent = (headers.get('User-Agent') or [''])[0]
        group = who_is_it(agent)
        bucket = hours.setdefault(hour_of(ts), empty_hour())
        bucket['req'][group] += 1
        bucket['bytes'] += int(row.get('size') or 0)
        status = str(row.get('status') or 0)
        bucket['status'][status[0] + 'xx'] = bucket['status'].get(status[0] + 'xx', 0) + 1

        day = datetime.fromtimestamp(ts, timezone.utc).strftime('%Y-%m-%d')
        who = hashlib.blake2b(f'{salt_for(day, state_dir)}|{req.get("remote_ip", "")}|{agent}'.encode(),
                              digest_size=8).hexdigest()
        if ts - seen.get(who, 0) > VISIT_GAP:
            bucket['visits'][group] += 1
        seen[who] = ts

        uri = req.get('uri') or '/'
        if FURNITURE.search(uri.split('?')[0]):
            continue
        page = page_of(uri)
        if int(row.get('status') or 0) == 404:
            bucket['missing'][page] = bucket['missing'].get(page, 0) + 1
            continue
        bucket['pages'][page] = bucket['pages'].get(page, 0) + 1
        if group != 'people':      # where a crawler says it came from is no use to anyone
            continue
        kind, what = referrer_of((headers.get('Referer') or [''])[0], host)
        if kind:
            bucket[kind][what] = bucket[kind].get(what, 0) + 1
    for bucket in hours.values():
        trim(bucket)


def window(hours, back):
    """The last `back` hours, added up."""
    first = hour_of(time.time() - back * 3600)
    req, visits, status = dict.fromkeys(WHO, 0), dict.fromkeys(WHO, 0), defaultdict(int)
    lists = {name: defaultdict(int) for name in MOST}
    size = 0
    for hour, bucket in hours.items():
        if hour < first:
            continue
        size += bucket.get('bytes', 0)
        for group in WHO:
            req[group] += bucket['req'].get(group, 0)
            visits[group] += bucket['visits'].get(group, 0)
        for code, n in bucket.get('status', {}).items():
            status[code] += n
        for name in lists:
            for what, n in bucket.get(name, {}).items():
                lists[name][what] += n
    out = {'requests': req, 'visits': visits, 'bytes': size, 'status': dict(sorted(status.items()))}
    for name, rows in lists.items():
        out[name] = [{'what': what, 'n': n} for what, n in sorted(rows.items(), key=lambda kv: -kv[1])[:MOST[name]]]
    return out


def main():
    ask = argparse.ArgumentParser(description=__doc__)
    ask.add_argument('--log', required=True)
    ask.add_argument('--state', required=True, help='hours.json and salt.json: a directory nothing serves')
    ask.add_argument('--out', required=True, help='traffic.json: written where Caddy serves /stats/ from')
    ask.add_argument('--site', required=True)
    ask.add_argument('--host', default='', help='the site\'s own name, to tell a link followed here from a link in')
    args = ask.parse_args()

    store = Path(args.state) / 'hours.json'
    try:
        kept = json.loads(store.read_text())
    except (OSError, ValueError):
        kept = {}
    hours = kept.get('hours') or {}
    seen = kept.get('seen') or {}
    state = kept.get('log') or {}

    lines, state = read_lines(args.log, state)
    count(lines, hours, seen, args.state, args.host or args.site)

    oldest = hour_of(time.time() - HOURS_KEPT * 3600)
    hours = {h: b for h, b in sorted(hours.items()) if h >= oldest}
    stale = time.time() - VISIT_GAP * 2
    seen = {who: when for who, when in seen.items() if when > stale}

    write(store, {'hours': hours, 'seen': seen, 'log': state}, private=True)
    write(Path(args.out), {
        'site': args.site,
        'host': args.host or args.site,
        'updated': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'read': len(lines),
        'windows': {name: window(hours, back) for name, back in WINDOWS},
    })
    print(f'{args.site}: {len(lines)} lines, {len(hours)} hours kept')


def write(path, body, private=False):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(body, separators=(',', ':')))
    os.chmod(tmp, 0o600 if private else 0o644)
    tmp.replace(path)


if __name__ == '__main__':
    sys.exit(main())
