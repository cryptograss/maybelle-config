"""offsite-backup.sh sends what a cold start needs to a restic repository off maybelle, and ends every run with one
RESULT line that cron-health reads. Run here against stand-ins for restic, ssh, rsync and docker that write down
what they were asked, over a /mnt/persist of our own."""

import os
import subprocess
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / 'offsite-backup.sh'

STAND_IN = '''#!/bin/bash
echo "$(basename "$0") $*" >> "$FAKE/calls"
case "$(basename "$0") $*" in
    restic*" cat config")  [ -e "$FAKE/repo" ]; exit ;;
    restic*" init")        touch "$FAKE/repo"; exit 0 ;;
    restic*" backup "*)    exit "${FAKE_BACKUP_RC:-0}" ;;
    restic*" forget "*)    exit "${FAKE_FORGET_RC:-0}" ;;
    restic*" check "*)     exit "${FAKE_CHECK_RC:-0}" ;;
    ssh*)                  exit "${FAKE_SSH_RC:-0}" ;;
    rsync*)                exit "${FAKE_RSYNC_RC:-0}" ;;
    docker*)               [ "${FAKE_DOCKER_RC:-0}" = 0 ] && echo dump; exit "${FAKE_DOCKER_RC:-0}" ;;
esac
'''


def aged(path, hours):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('x')
    then = time.time() - hours * 3600
    os.utime(path, (then, then))
    return path


@pytest.fixture
def maybelle(tmp_path):
    fake = tmp_path / 'fake'
    (fake / 'bin').mkdir(parents=True)
    for name in ('restic', 'ssh', 'rsync', 'docker'):
        (fake / 'bin' / name).write_text(STAND_IN)
        (fake / 'bin' / name).chmod(0o755)
    persist = tmp_path / 'persist'
    aged(persist / 'magenta/backups/daily/magenta_memory_100.dump', 30)
    aged(persist / 'magenta/backups/daily/magenta_memory_200.dump', 2)
    aged(persist / 'pickipedia/backups/pickipedia_20261113.sql.gz', 2)
    aged(persist / 'hunter-vcs/latest/manifest.json', 1)
    (persist / 'githouse/git').mkdir(parents=True)
    (tmp_path / 'password').write_text('not-a-real-one\n')
    env = {**os.environ, 'PATH': f"{fake / 'bin'}:{os.environ['PATH']}", 'FAKE': str(fake),
           'OFFSITE_ENV': str(tmp_path / 'no-env-file'), 'OFFSITE_LOG': str(tmp_path / 'log'),
           'OFFSITE_PERSIST': str(persist), 'OFFSITE_PASSWORD_FILE': str(tmp_path / 'password'),
           'OFFSITE_REPO': 'sftp:root@box:/mnt/storage-box/offsite/restic', 'OFFSITE_BOX': 'root@box:/mnt/storage-box'}

    def run(**more):
        done = subprocess.run(['bash', str(SCRIPT)], env={**env, **more}, capture_output=True, text=True)
        calls = (fake / 'calls').read_text().splitlines() if (fake / 'calls').exists() else []
        results = [line for line in (tmp_path / 'log').read_text().splitlines() if ' RESULT ' in line]
        return done.returncode, results[-1].split(' RESULT ', 1)[1], calls

    return run, persist, tmp_path


def the(calls, start):
    return [call for call in calls if call.startswith(start)]


def test_everything_fresh_is_sent_and_ok(maybelle):
    run, persist, _ = maybelle
    code, result, calls = run()
    assert code == 0 and result.startswith('OK 6 parts sent')
    backup = the(calls, 'restic')[2]                       # after cat config and init
    assert ' backup --host maybelle --tag offsite ' in backup
    assert 'magenta_memory_200.dump' in backup and 'magenta_memory_100.dump' not in backup   # the newest only
    for part in ('pickipedia/backups/pickipedia_20261113.sql.gz', 'pickipedia/files', 'offsite-staging/glitchtip.dump', 'githouse/git',
                 'hunter-vcs/latest'):
        assert str(persist / part) in backup
    assert (persist / 'offsite-staging/glitchtip.dump').read_text() == 'dump\n'


def test_the_box_is_asked_first_and_restic_goes_through_it(maybelle):
    run, _, _ = maybelle
    _, _, calls = run()
    assert calls[0] == 'ssh -o BatchMode=yes -o ConnectTimeout=20 root@box mountpoint -q /mnt/storage-box'
    assert all('sftp.command=ssh -o BatchMode=yes -o ConnectTimeout=20 root@box -s sftp' in call
               for call in the(calls, 'restic'))


def test_no_box_named_means_no_mount_check(maybelle):
    run, _, _ = maybelle
    code, _, calls = run(OFFSITE_BOX='', OFFSITE_REPO='b2:bucket:offsite')
    assert code == 0 and not [call for call in the(calls, 'ssh') if 'mountpoint' in call]
    assert not [call for call in the(calls, 'restic') if 'sftp.command' in call]


def test_a_repository_is_made_once(maybelle):
    run, _, _ = maybelle
    _, _, calls = run()
    assert len(the(calls, 'restic')) == 4 and ' init' in the(calls, 'restic')[1]
    _, _, calls = run()
    assert len([call for call in the(calls, 'restic') if call.endswith(' init')]) == 1


def test_old_snapshots_are_trimmed_as_one_group(maybelle):
    run, _, _ = maybelle
    _, _, calls = run()
    forget = [call for call in calls if ' forget ' in call][0]
    assert '--group-by host --keep-daily 7 --keep-weekly 4 --keep-monthly 12 --prune' in forget


def test_no_password_fails_before_anything_is_touched(maybelle):
    run, _, tmp_path = maybelle
    (tmp_path / 'password').unlink()
    code, result, calls = run()
    assert code == 1 and result.startswith('FAILED no restic password') and 'offsite_restic_password' in result
    assert calls == []


def test_box_not_mounted_fails_and_restic_never_runs(maybelle):
    run, _, _ = maybelle
    code, result, calls = run(FAKE_SSH_RC='1')
    assert code == 1 and result.startswith('FAILED /mnt/storage-box is not mounted on root@box')
    assert not the(calls, 'restic') and not the(calls, 'rsync')


def test_a_stale_dump_is_sent_and_said(maybelle):
    run, persist, _ = maybelle
    aged(persist / 'magenta/backups/daily/magenta_memory_200.dump', 40)
    aged(persist / 'magenta/backups/daily/magenta_memory_100.dump', 64)
    code, result, calls = run()
    assert code == 1 and result.startswith('DEGRADED sent to') and 'magenta database: newest is 40 hours old' in result
    assert [call for call in calls if ' backup ' in call]


def test_a_missing_part_does_not_stop_the_rest(maybelle):
    run, persist, _ = maybelle
    (persist / 'pickipedia/backups/pickipedia_20261113.sql.gz').unlink()
    aged(persist / 'hunter-vcs/latest/manifest.json', 9)
    code, result, calls = run(FAKE_RSYNC_RC='12', FAKE_DOCKER_RC='1')
    assert code == 1 and result.startswith('DEGRADED')
    for said in ('PickiPedia database: no pickipedia_*.sql.gz', 'PickiPedia files: the copy from', 'GlitchTip: pg_dump failed',
                 "hunter's unpushed work: last pulled copy is 9 hours old"):
        assert said in result
    backup = [call for call in calls if ' backup ' in call][0]
    assert 'magenta_memory_200.dump' in backup and 'glitchtip.dump' not in backup
    assert not (persist / 'offsite-staging/glitchtip.dump.tmp').exists()


def test_a_failed_backup_is_failed_not_degraded(maybelle):
    run, _, _ = maybelle
    code, result, calls = run(FAKE_BACKUP_RC='1')
    assert code == 1 and result.startswith('FAILED restic backup failed')
    assert not [call for call in calls if ' forget ' in call]


def test_a_failed_trim_or_check_is_degraded(maybelle):
    run, _, _ = maybelle
    code, result, calls = run(FAKE_FORGET_RC='1', FAKE_CHECK_RC='1', OFFSITE_CHECK_NOW='1')
    assert code == 1 and 'restic forget failed' in result and 'restic check failed' in result
    assert [call for call in calls if ' check --read-data-subset=10%' in call]


def test_the_log_trims_itself_in_place(maybelle):
    run, _, tmp_path = maybelle
    log = tmp_path / 'log'
    log.write_text(''.join(f'line {n}\n' for n in range(7000)))
    inode = log.stat().st_ino
    run()
    lines = log.read_text().splitlines()
    assert lines[0] == 'line 3000' and ' RESULT OK ' in lines[-1] and log.stat().st_ino == inode
