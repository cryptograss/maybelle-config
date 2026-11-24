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


def test_an_hour_nobody_came_is_still_an_hour_counted(tmp_path, now):
    """Seen live on maybelle mid-catch-up: the three hours said "not full" while the day said
    "full", because the hour exactly three back held nothing yet. How far back the count reaches is
    not the same question as which hours hold something."""
    (tmp_path / 'access.log').write_text(line(now - 5 * 3600, '/wiki/Doc_Watson')
                                         + line(now - 60, '/wiki/Merle_Watson'))
    body = run(tmp_path)
    three = body['windows']['3h']
    assert three['full'] is True                 # the count began five hours ago, before this window
    assert three['hours'] == 1                   # and only this hour holds anything
    assert three['since'] == body['windows']['24h']['since'] == body['since']
    assert body['windows']['24h']['full'] is False


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


@pytest.mark.parametrize('name', ['access.log.1.gz',                            # logrotate's
                                  'access-2026-11-21T06-00-00.000-size.log.gz',  # Caddy's own
                                  'access-2026-11-21T06-00-00.000-time.log.gz'])
def test_a_gzipped_rolled_log_is_read_too(tmp_path, now, name):
    """Caddy gzips a rolled log unless roll_uncompressed is set, so on both our boxes every rolled
    file ends .log.gz. A backfill that only looked for .log would find nothing at all."""
    with gzip.open(tmp_path / name, 'wb') as f:
        f.write(''.join(line(now - 5 * 3600, '/wiki/Doc_Watson') for _ in range(3)).encode())
    (tmp_path / 'access.log').write_text(line(now - 60, '/wiki/Merle_Watson'))
    body = run(tmp_path)
    assert body['windows']['24h']['requests']['people'] == 4


def test_the_tail_of_a_log_that_rolled_and_was_gzipped_is_counted_once(tmp_path, now):
    """The gzipped copy is a different file from the one we were reading, so its inode can't find it.
    What we hadn't counted is found by time instead -- and nothing before it is counted twice."""
    first, second = line(now - 600, '/wiki/Doc_Watson'), line(now - 300, '/wiki/Merle_Watson')
    (tmp_path / 'access.log').write_text(first)
    assert run(tmp_path)['read'] == 1

    # Caddy writes the second line, rolls the file away and gzips it, and starts a new one. The new
    # one is made beside the old and moved over it, so it is a different file and not the same inode
    # handed back (which is what Caddy's rename does: the old inode lives on inside the rolled file).
    with gzip.open(tmp_path / 'access-2026-11-22T02-00-00.000-size.log.gz', 'wb') as f:
        f.write((first + second).encode())
    (tmp_path / 'access.log.new').write_text(line(now - 120, '/wiki/Watson_Family'))
    (tmp_path / 'access.log.new').replace(tmp_path / 'access.log')

    body = run(tmp_path)
    assert body['windows']['3h']['requests']['people'] == 3     # not 4: the first line isn't re-counted
    assert [row['what'] for row in body['windows']['3h']['pages']] == [
        '/wiki/Doc_Watson', '/wiki/Merle_Watson', '/wiki/Watson_Family']
    assert run(tmp_path)['windows']['3h']['requests']['people'] == 3


def test_a_half_written_gzip_costs_its_own_lines_and_nothing_else(tmp_path, now):
    """re, 22 Nov: gzip.open raises EOFError on a truncated stream, which isn't an OSError. If that
    ends the run, a rolled file that is broken for good stops every run after it, and the served
    file never changes again. It must cost that one file and no more."""
    whole = ''.join(line(now - 4 * 3600, f'/wiki/Page_{n}') for n in range(200))
    cut = gzip.compress(whole.encode())[:-60]
    (tmp_path / 'access-2026-11-21T22-00-00.000-size.log.gz').write_bytes(cut)
    (tmp_path / 'access.log').write_text(line(now - 60, '/wiki/Doc_Watson'))

    body = run(tmp_path)                       # the run finishes, and the live line is counted
    assert body['windows']['3h']['requests']['people'] == 1
    assert body['catching_up'] == 0            # the broken file is behind us, not tried forever
    assert run(tmp_path)['windows']['24h']['requests']['people'] >= 1


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


def test_a_program_that_doesnt_say_it_is_a_browser_is_not_a_reader(tmp_path, now):
    """What Justin's million api.php asks turned out to be (#performance, 23 Nov): mwclient, and a
    node process, and neither is a person. Both were counted as people until this, which on
    PickiPedia put two million asks a week -- half of everything the box served -- in the readers'
    column."""
    (tmp_path / 'access.log').write_text(
        line(now - 60, '/api.php?action=query', agent='mwclient/0.11.0', ip='10.0.0.4')
        + line(now - 55, '/api.php?action=query', agent='node', ip='10.0.0.5')
        + line(now - 50, '/api.php?action=query', agent='', ip='10.0.0.6')
        + line(now - 45, '/wiki/Doc_Watson'))
    three = run(tmp_path)['windows']['3h']
    assert three['requests'] == {'people': 1, 'crawlers': 3, 'ours': 0}


def test_the_busiest_clients_are_named_and_the_people_are_not(tmp_path, now):
    """"What are these million asks" is a question the page can answer on its own, so each window
    names the programs behind its busiest rows. A person is never named: a browser's user agent next
    to what it asked for is most of a fingerprint, so every browser is 'a browser'."""
    (tmp_path / 'access.log').write_text(
        ''.join(line(now - 100 + i, '/api.php?action=query', agent='mwclient/0.11.0', ip='10.0.0.4')
                for i in range(5))
        + line(now - 60, '/wiki/Doc_Watson', agent=CRAWLER, ip='66.249.66.1')
        + line(now - 50, '/wiki/Doc_Watson')
        + line(now - 40, '/api.php?action=query', agent=OURS, ip='10.0.0.2'))
    body = run(tmp_path)
    clients = {row['what']: row['n'] for row in body['windows']['3h']['clients']}
    assert clients == {'mwclient': 5, 'Googlebot': 1, 'a browser': 1, 'memory-lane': 1}
    assert BROWSER not in (tmp_path / 'served' / 'traffic.json').read_text()


def test_an_hour_counted_before_the_clients_list_existed_is_still_read(tmp_path, now):
    """The buckets on each box were written by the version before this one, and they hold no clients
    list at all. A run that trips over that is a run that stops counting, quietly, on whichever box
    deployed first."""
    (tmp_path / 'access.log').write_text(line(now - 7200, '/wiki/Doc_Watson'))
    run(tmp_path)
    kept = json.loads((tmp_path / 'state' / 'hours.json').read_text())
    for bucket in kept['hours'].values():
        bucket.pop('clients', None)
    (tmp_path / 'state' / 'hours.json').write_text(json.dumps(kept))

    with (tmp_path / 'access.log').open('a') as log:      # as Caddy writes it: the next line, appended
        log.write(line(now - 60, '/wiki/Bill_Monroe'))
    body = run(tmp_path)
    assert body['windows']['3h']['requests']['people'] == 2
    assert {row['what'] for row in body['windows']['3h']['clients']} == {'a browser'}


def test_a_line_landing_in_an_hour_counted_before_the_clients_list_is_still_counted(tmp_path, now):
    """re's reproduction (#performance, 23 Nov 22:58Z), and the path a deploy actually takes: the old
    version wrote the hour we are still in, and the new version's first line lands in that same
    bucket. Counting into a list the bucket doesn't hold killed the run before it saved anything, so
    the state stayed old-format and every run after it died the same way, for good. The served file
    freezes at deploy time, which reads exactly like a site nobody is visiting."""
    hour = (now - 3600) // 3600 * 3600      # an hour wholly past, so both lines share one bucket
    (tmp_path / 'access.log').write_text(line(hour + 10, '/wiki/Doc_Watson'))
    run(tmp_path)
    kept = json.loads((tmp_path / 'state' / 'hours.json').read_text())
    assert len(kept['hours']) == 1
    for bucket in kept['hours'].values():
        bucket.pop('clients', None)
    (tmp_path / 'state' / 'hours.json').write_text(json.dumps(kept))

    with (tmp_path / 'access.log').open('a') as log:
        log.write(line(hour + 20, '/wiki/Bill_Monroe'))
    body = run(tmp_path)
    assert body['windows']['3h']['requests']['people'] == 2
    assert {row['what']: row['n'] for row in body['windows']['3h']['clients']} == {'a browser': 1}
    # and the run saved its state, so the next one carries on rather than dying the same way
    assert 'clients' in next(iter(json.loads((tmp_path / 'state' / 'hours.json').read_text())['hours'].values()))
