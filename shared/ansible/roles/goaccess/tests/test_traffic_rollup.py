"""The traffic rollup, run over logs we write here: what it counts, what it refuses to keep, and how
far back it says it reaches.

It is the thing that decides what the public stats page holds, so the privacy line is tested as
plainly as the arithmetic: an OAuth code, a token and a typed search must not survive into the file
Caddy serves, and an address must not either.
"""

import gzip
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROLLUP = Path(__file__).resolve().parent.parent / 'files' / 'traffic-rollup.py'
BROWSER = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Safari/537.36'
CRAWLER = 'Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)'
OURS = 'memory-lane (magenta)'


def line(ts, uri='/', agent=BROWSER, ip='203.0.113.7', status=200, referer=None, size=1200):
    row = {'level': 'info', 'ts': ts, 'logger': 'http.log.access', 'msg': 'handled request',
           'request': {'remote_ip': ip, 'proto': 'HTTP/2.0', 'method': 'GET', 'host': 'pickipedia.xyz',
                       'uri': uri, 'headers': {'User-Agent': [agent]}},
           'status': status, 'size': size}
    if referer:
        row['request']['headers']['Referer'] = [referer]
    return json.dumps(row) + '\n'


def run(tmp, log='access.log'):
    """One run of the rollup over tmp/<log>, as the timer runs it. Returns what it serves."""
    out = tmp / 'served' / 'traffic.json'
    out.parent.mkdir(exist_ok=True)
    (tmp / 'state').mkdir(exist_ok=True)
    done = subprocess.run([sys.executable, str(ROLLUP), '--log', str(tmp / log),
                           '--state', str(tmp / 'state'), '--out', str(out),
                           '--site', 'pickipedia', '--host', 'pickipedia.xyz'],
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    return json.loads(out.read_text())


@pytest.fixture
def now():
    return time.time()


def test_people_crawlers_and_ours_are_counted_apart(tmp_path, now):
    (tmp_path / 'access.log').write_text(
        line(now - 60, '/index.php?title=Doc_Watson')
        + line(now - 50, '/index.php?title=Doc_Watson', agent=CRAWLER, ip='66.249.66.1')
        + line(now - 40, '/api.php?action=query', agent=OURS, ip='10.0.0.2'))
    body = run(tmp_path)
    three = body['windows']['3h']
    assert three['requests'] == {'people': 1, 'crawlers': 1, 'ours': 1}
    assert three['visits'] == {'people': 1, 'crawlers': 1, 'ours': 1}


def test_a_page_keeps_its_title_and_loses_everything_else(tmp_path, now):
    (tmp_path / 'access.log').write_text(
        line(now - 60, '/index.php?title=Doc_Watson&code=0b4c1e2f3a&access_token=sekrit')
        + line(now - 59, '/index.php?search=what+I+typed&title=Special:Search'))
    served = (tmp_path / 'served' / 'traffic.json')
    body = run(tmp_path)
    pages = [row['what'] for row in body['windows']['3h']['pages']]
    assert '/index.php?title=Doc_Watson' in pages
    raw = served.read_text()
    for secret in ('0b4c1e2f3a', 'sekrit', 'what+I+typed', '203.0.113.7'):
        assert secret not in raw


def test_where_people_came_from_keeps_the_post_and_drops_the_query(tmp_path, now):
    (tmp_path / 'access.log').write_text(
        line(now - 60, '/wiki/Doc_Watson',
             referer='https://www.reddit.com/r/bluegrass/comments/1abc/doc_watson/?utm_source=share')
        + line(now - 50, '/wiki/Merle_Watson', referer='https://pickipedia.xyz/wiki/Doc_Watson'))
    body = run(tmp_path)
    three = body['windows']['3h']
    assert three['from'] == [{'what': 'www.reddit.com/r/bluegrass/comments/1abc/doc_watson/', 'n': 1}]
    assert three['followed'] == [{'what': '/wiki/Doc_Watson', 'n': 1}]
    assert 'utm_source' not in (tmp_path / 'served' / 'traffic.json').read_text()


def test_each_window_says_how_far_back_it_reaches(tmp_path, now):
    """Justin, #performance, 22 Nov: the three windows showed the same numbers, because the live log
    was an hour old. A window that holds an hour must not read like a quiet week."""
    (tmp_path / 'access.log').write_text(line(now - 600, '/wiki/Doc_Watson'))
    body = run(tmp_path)
    for key in ('3h', '24h', '7d'):
        assert body['windows'][key]['hours'] == 1
        assert body['windows'][key]['full'] is False
        assert body['windows'][key]['since'].endswith('+00:00')
    assert body['since'] == body['windows']['7d']['since']


def test_a_window_with_history_behind_it_is_full(tmp_path, now):
    hours = [now - h * 3600 for h in range(0, 5)]
    (tmp_path / 'access.log').write_text(''.join(line(ts, '/wiki/Doc_Watson') for ts in hours))
    body = run(tmp_path)
    assert body['windows']['3h']['full'] is True
    assert body['windows']['24h']['full'] is False
    assert body['windows']['24h']['hours'] == 5


def test_the_first_run_reads_the_rolled_logs_beside_the_live_one(tmp_path, now):
    """The live file is only as old as the last roll. Without the rolled ones, the day and the week
    would take a day and a week to mean anything."""
    (tmp_path / 'access-2026-11-20T06-00-00.000.log').write_text(
        ''.join(line(now - 30 * 3600, f'/wiki/Page_{n}') for n in range(4)))
    (tmp_path / 'access-2026-11-21T06-00-00.000.log').write_text(
        ''.join(line(now - 10 * 3600, f'/wiki/Page_{n}') for n in range(3)))
    (tmp_path / 'access.log').write_text(line(now - 60, '/wiki/Doc_Watson'))

    first = run(tmp_path)
    assert first['catching_up'] == 1          # one rolled log a run, oldest first
    assert first['windows']['7d']['requests']['people'] == 5    # the oldest roll, and the live line
    assert first['windows']['24h']['requests']['people'] == 1

    second = run(tmp_path)
    assert second['catching_up'] == 0
    assert second['windows']['7d']['requests']['people'] == 8
    assert second['windows']['24h']['requests']['people'] == 4
    assert second['windows']['3h']['requests']['people'] == 1

    third = run(tmp_path)                     # nothing is counted twice
    assert third['read'] == 0
    assert third['windows']['7d']['requests']['people'] == 8


def test_a_gzipped_rolled_log_is_read_too(tmp_path, now):
    with gzip.open(tmp_path / 'access.log.1.gz', 'wb') as f:
        f.write(''.join(line(now - 5 * 3600, '/wiki/Doc_Watson') for _ in range(3)).encode())
    (tmp_path / 'access.log').write_text(line(now - 60, '/wiki/Merle_Watson'))
    body = run(tmp_path)
    assert body['windows']['24h']['requests']['people'] == 4


def test_nothing_beside_the_log_is_read_twice_when_the_live_file_rolls(tmp_path, now):
    (tmp_path / 'access.log').write_text(line(now - 600, '/wiki/Doc_Watson'))
    run(tmp_path)
    (tmp_path / 'access.log').write_text(line(now - 600, '/wiki/Doc_Watson')
                                         + line(now - 300, '/wiki/Merle_Watson'))
    body = run(tmp_path)                       # appended: only the new line is read
    assert body['read'] == 1
    assert body['windows']['3h']['requests']['people'] == 2

    (tmp_path / 'access.log').rename(tmp_path / 'access-2026-11-22T02-00-00.000.log')
    (tmp_path / 'access.log').write_text(line(now - 120, '/wiki/Watson_Family'))
    rolled = run(tmp_path)
    assert rolled['windows']['3h']['requests']['people'] == 3


def test_a_line_still_being_written_is_counted_next_time(tmp_path, now):
    whole = line(now - 300, '/wiki/Doc_Watson')
    (tmp_path / 'access.log').write_text(whole + whole[:40])
    first = run(tmp_path)
    assert first['read'] == 1
    (tmp_path / 'access.log').write_text(whole + whole)
    second = run(tmp_path)
    assert second['read'] == 1
    assert second['windows']['3h']['requests']['people'] == 2


def test_nothing_private_reaches_the_served_file(tmp_path, now):
    (tmp_path / 'access.log').write_text(line(now - 60, '/wiki/Doc_Watson', ip='198.51.100.23'))
    run(tmp_path)
    served = (tmp_path / 'served' / 'traffic.json').read_text()
    assert '198.51.100.23' not in served
    salt = json.loads((tmp_path / 'state' / 'salt.json').read_text())
    assert salt and not any(value in served for value in salt.values())
    assert (tmp_path / 'state' / 'salt.json').stat().st_mode & 0o077 == 0
    assert (tmp_path / 'state' / 'hours.json').stat().st_mode & 0o077 == 0
