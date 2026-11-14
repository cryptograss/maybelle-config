"""cron-health's "offsite backup" and "vault password sweep" stages, and the sweep itself. The stages' shell is taken
out of cron-health.groovy, and the sweep out of maybelle.yml, as written there, and run on logs of our own: what
turns each red is what posts to #ops."""

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
import yaml

MAYBELLE = Path(__file__).resolve().parent.parent.parent
GROOVY = (MAYBELLE / 'jobs/cron-health.groovy').read_text()
PLAYBOOK = yaml.safe_load((MAYBELLE / 'ansible/maybelle.yml').read_text())
PLAY = f'maybelle-test-{os.getpid()}'


def iso(hours_ago=0.0):
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(time.time() - hours_ago * 3600))


def stage(name, real_log, tmp_path):
    """Run one stage's shell as Jenkins would (sh, a workspace), reading tmp_path/log; its exit code and last line."""
    start = GROOVY.index('sh """', GROOVY.index(f"stage('{name}')")) + 6
    shell = GROOVY[start:GROOVY.index('"""', start)].replace('\\\\$', '$').replace(real_log, str(tmp_path / 'log'))
    done = subprocess.run(['sh', '-c', shell], env={**os.environ, 'WORKSPACE': str(tmp_path)}, capture_output=True, text=True)
    assert (tmp_path / '.alarm' / name).read_text().strip() == str(done.returncode)    # what report-alarm.sh reads
    return done.returncode, done.stdout.strip().splitlines()[-1]


# --- offsite backup ---

def offsite(tmp_path, *lines):
    (tmp_path / 'log').write_text(''.join(line + '\n' for line in lines))
    return stage('offsite backup', '/var/log/offsite-backup.log', tmp_path)


def test_offsite_green_on_a_fresh_ok(tmp_path):
    assert offsite(tmp_path, f'{iso(30)} RESULT FAILED old', f'{iso(2)} start', f'{iso(2)} RESULT OK 6 parts sent') == (0, 'OK')


def test_offsite_red_when_degraded_or_failed_and_says_why(tmp_path):
    code, _ = offsite(tmp_path, f'{iso(2)} problem: GlitchTip: pg_dump failed', f'{iso(2)} RESULT DEGRADED sent, but')
    assert code == 1
    code, last = offsite(tmp_path, f'{iso(2)} RESULT FAILED no restic password')
    assert code == 1 and 'FAILED' in last


def test_offsite_red_with_no_run_in_26_hours(tmp_path):
    code, last = offsite(tmp_path, f'{iso(27)} RESULT OK 6 parts sent')
    assert code == 1 and 'no run for 27 hours' in last


def test_offsite_pending_is_green_for_a_day_only(tmp_path):
    assert offsite(tmp_path, f'{iso(3)} RESULT PENDING installed')[0] == 0
    assert offsite(tmp_path, f'{iso(27)} RESULT PENDING installed')[0] == 1


def test_offsite_red_when_it_has_never_run(tmp_path):
    code, last = offsite(tmp_path)
    assert code == 1 and 'never run' in last


# --- vault password sweep: the stage ---

def swept(tmp_path, *lines):
    (tmp_path / 'log').write_text(''.join(line + '\n' for line in lines))
    return stage('vault password sweep', '/var/log/vault-password-sweep.log', tmp_path)


def test_sweep_stage_green_when_absent(tmp_path):
    assert swept(tmp_path, f'{iso(0.05)} absent') == (0, 'OK')


def test_sweep_stage_red_for_a_day_after_a_removal(tmp_path):
    code, last = swept(tmp_path, f'{iso(5)} REMOVED: present 40 min, no deploy running', f'{iso(0.05)} absent')
    assert code == 1 and 'REMOVED' in last
    assert swept(tmp_path, f'{iso(25)} REMOVED: present 40 min, no deploy running', f'{iso(0.05)} absent')[0] == 0


def test_sweep_stage_red_when_the_sweep_stops_or_never_ran(tmp_path):
    assert swept(tmp_path, f'{iso(1)} absent')[0] == 1
    code, last = swept(tmp_path)
    assert code == 1 and 'never run' in last


# --- vault password sweep: the script ---

@pytest.fixture
def sweep(tmp_path):
    tasks = [task for play in PLAYBOOK for task in play.get('tasks', [])]
    script = next(task for task in tasks if task.get('name') == 'Vault password sweep script')['copy']['content']
    password, log = tmp_path / 'vault-password', tmp_path / 'log'
    # A playbook name of this test's own: pgrep -f reads every command line on the machine, and a shell that
    # merely mentions ansible-playbook and maybelle.yml would otherwise count as a deploy (it did, once, here).
    assert "'ansible-playbook.*maybelle\\.yml'" in script
    (tmp_path / 'sweep.sh').write_text(script.replace('/mnt/persist/.vault-password', str(password))
                                       .replace('/var/log/vault-password-sweep.log', str(log))
                                       .replace('maybelle\\.yml', f'{PLAY}\\.yml'))

    def run(minutes_old=None):
        if minutes_old is not None:
            password.write_text('x')
            then = time.time() - minutes_old * 60
            os.utime(password, (then, then))
        subprocess.run(['bash', str(tmp_path / 'sweep.sh')])
        return password.exists(), log.read_text().splitlines()[-1]

    return run, log


def test_sweep_logs_absent(sweep):
    run, _ = sweep
    assert run() == (False, run()[1]) and run()[1].endswith(' absent')


def test_sweep_leaves_a_password_just_copied(sweep):
    run, _ = sweep
    there, said = run(minutes_old=5)
    assert there and 'left: copied for a deploy under 30 min ago' in said


def test_sweep_removes_one_with_no_deploy_running(sweep):
    run, _ = sweep
    there, said = run(minutes_old=40)
    assert not there and 'REMOVED: present 40 min, no deploy running' in said


@pytest.mark.skipif(shutil.which('pgrep') is None, reason='no pgrep here')
def test_sweep_waits_for_a_running_deploy_but_not_past_three_hours(sweep):
    run, _ = sweep
    deploy = subprocess.Popen(['bash', '-c', f"exec -a 'ansible-playbook -i localhost, {PLAY}.yml' sleep 30"])
    try:
        time.sleep(0.3)
        there, said = run(minutes_old=40)
        assert there and 'left: a deploy is running' in said
        there, said = run(minutes_old=200)
        assert not there and 'REMOVED: present 200 min, over 3 hours, deploy or not' in said
    finally:
        deploy.kill()


def test_sweep_trims_its_log_in_place(sweep):
    run, log = sweep
    log.write_text(''.join(f'line {n}\n' for n in range(3500)))
    inode = log.stat().st_ino
    run()
    lines = log.read_text().splitlines()
    assert lines[0] == 'line 1500' and lines[-1].endswith(' absent') and log.stat().st_ino == inode
