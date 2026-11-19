"""maybelle-chapter-1.sh: vault cleanup on abort/ERR, old-session reaping before upload, umask 077, and ssh fallback."""

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

MAYBELLE = Path(__file__).resolve().parent.parent.parent
CHAPTER_1 = MAYBELLE / 'scripts/maybelle-chapter-1.sh'


def make_env(tmp_path, tmux_fails=False, mosh_fails=False):
    remote = tmp_path / 'remote'
    vol = remote / 'mnt/HC_Volume_104062902'
    (remote / 'tmp').mkdir(parents=True)
    vol.mkdir(parents=True)

    work = tmp_path / 'repo/maybelle'
    (work / 'scripts').mkdir(parents=True)
    (tmp_path / 'repo/secrets').mkdir(parents=True)
    (tmp_path / 'repo/secrets/vault.yml').write_text('dummy')
    (work / 'config.yml').write_text(
        'hetzner_volume_id: "104062902"\n'
        'mount_point: "/mnt/persist"\n'
        'host: "maybelle.test"\n'
        'user: "root"\n'
        'vault_password_file: "/mnt/persist/.vault-password"\n'
    )
    script_copy = work / 'scripts/maybelle-chapter-1.sh'
    shutil.copy2(CHAPTER_1, script_copy)
    script_copy.chmod(0o755)

    vault_pw = tmp_path / 'local-vault-pass'
    vault_pw.write_text('hunter2\n')

    calls = tmp_path / 'calls.log'
    modes = tmp_path / 'modes.log'
    fake_bin = tmp_path / 'bin'
    fake_bin.mkdir()

    (fake_bin / 'ansible-vault').write_text('#!/bin/sh\nprintf "secret_one: supersecretvalue\\n"\n')
    (fake_bin / 'mosh').write_text(
        f'#!/bin/sh\n'
        f'echo "mosh $*" >> "{calls}"\n'
        f'exit {"1" if mosh_fails else "0"}\n'
    )
    (fake_bin / 'ssh').write_text(
        f'#!/bin/bash\n'
        f'umask 022\n'
        f'echo "ssh $*" >> "{calls}"\n'
        f'cmd="${{@: -1}}"\n'
        f'case "$cmd" in\n'
        f'  *"pgrep -f \'ansible-playbook"*) exit 1 ;;\n'
        f'  *"tmux kill-session"*) exit 0 ;;\n'
        f'  *"cat > \'/mnt/HC_Volume_104062902/.vault-password\'"*)\n'
        f'    sub="${{cmd///mnt\\/HC_Volume_104062902/{vol}}}"\n'
        f'    bash -c "$sub"\n'
        f'    printf "vault:%s\\n" "$(stat -c %a "{vol}/.vault-password")" >> "{modes}"\n'
        f'    ;;\n'
        f'  *"cat > /tmp/scrubber-secrets.json"*)\n'
        f'    sub="${{cmd///tmp\\/scrubber-secrets.json/{remote}/tmp/scrubber-secrets.json}}"\n'
        f'    bash -c "$sub"\n'
        f'    printf "scrubber:%s\\n" "$(stat -c %a "{remote}/tmp/scrubber-secrets.json")" >> "{modes}"\n'
        f'    ;;\n'
        f'  *"cat > \'/tmp/chapter-1-script.sh\'"*)\n'
        f'    sub="${{cmd///tmp\\/chapter-1-script.sh/{remote}/tmp/chapter-1-script.sh}}"\n'
        f'    bash -c "$sub"\n'
        f'    printf "script:%s\\n" "$(stat -c %a "{remote}/tmp/chapter-1-script.sh")" >> "{modes}"\n'
        f'    ;;\n'
        f'  *"tmux new-session -d"*) exit {"1" if tmux_fails else "0"} ;;\n'
        f'  *"rm -f"*) rm -f "{vol}/.vault-password" "{remote}/tmp/scrubber-secrets.json" "{remote}/tmp/chapter-1-script.sh" "{remote}/tmp/chapter-1.running" ;;\n'
        f'esac\n'
        f'exit 0\n'
    )
    for b in fake_bin.iterdir():
        b.chmod(0o755)

    env = {
        **os.environ,
        'ANSIBLE_VAULT_PASSWORD_FILE': str(vault_pw),
        'PATH': f'{fake_bin}:{os.environ.get("PATH", "")}',
    }
    return script_copy, vol / '.vault-password', remote / 'tmp/scrubber-secrets.json', calls, modes, env


def test_aborted_startup_cleans_up_vault_and_scrubber_files(tmp_path):
    script, remote_pw, remote_scrubber, _, _, env = make_env(tmp_path, tmux_fails=True)
    done = subprocess.run(['bash', str(script)], env=env, capture_output=True, text=True)
    assert done.returncode != 0
    assert not remote_pw.exists()
    assert not remote_scrubber.exists()


def test_blocked_mosh_udp_keeps_armed_files_and_falls_back_to_ssh(tmp_path):
    script, remote_pw, remote_scrubber, calls, _, env = make_env(tmp_path, mosh_fails=True)
    done = subprocess.run(['bash', str(script)], env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert remote_pw.exists() and remote_scrubber.exists()  # owned by the running tmux session's EXIT trap now
    logged = calls.read_text()
    assert 'mosh ' in logged
    assert 'ssh -t root@maybelle.test tmux attach-session' in logged


def test_ssh_flag_skips_mosh_entirely(tmp_path):
    script, remote_pw, _, calls, _, env = make_env(tmp_path, mosh_fails=True)
    done = subprocess.run(['bash', str(script), '--ssh'], env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert remote_pw.exists()
    logged = calls.read_text()
    assert 'mosh ' not in logged
    assert 'ssh -t root@maybelle.test tmux attach-session' in logged


def test_uploaded_files_are_created_private_with_umask_077(tmp_path):
    script, _, _, _, modes, env = make_env(tmp_path)
    done = subprocess.run(['bash', str(script), '--ssh'], env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    recorded = dict(line.split(':') for line in modes.read_text().splitlines())
    assert recorded == {'vault': '600', 'scrubber': '600', 'script': '700'}


@pytest.mark.skipif(shutil.which('tmux') is None or shutil.which('pgrep') is None, reason='needs real tmux and pgrep')
def test_leftover_session_at_press_enter_is_reaped_before_new_upload(tmp_path):
    """Regression for re's 19 Nov catch: an old chapter-1 session waiting at 'Press enter to exit'
    is killed and finishes its EXIT trap BEFORE the new run uploads its files."""
    rdir = tmp_path / 'r'
    vol = rdir / 'vol'
    rdir.mkdir()
    vol.mkdir()
    sock = tmp_path / 'tmux.sock'

    work = tmp_path / 'repo/maybelle'
    (work / 'scripts').mkdir(parents=True)
    (tmp_path / 'repo/secrets').mkdir(parents=True)
    (tmp_path / 'repo/secrets/vault.yml').write_text('dummy')
    (work / 'config.yml').write_text(
        'hetzner_volume_id: "104062902"\n'
        'mount_point: "/mnt/persist"\n'
        'host: "maybelle.test"\n'
        'user: "root"\n'
        'vault_password_file: "/mnt/persist/.vault-password"\n'
    )
    text = CHAPTER_1.read_text()
    text = text.replace('/mnt/HC_Volume_${VOLUME_ID}', str(vol))
    text = text.replace('/mnt/persist/.vault-password', str(vol / '.vault-password'))
    text = text.replace('/tmp/scrubber-secrets.json', str(rdir / 'scrubber-secrets.json'))
    text = text.replace('/tmp/chapter-1-script.sh', str(rdir / 'chapter-1-script.sh'))
    text = text.replace('/tmp/chapter-1.running', str(rdir / 'chapter-1.running'))
    text = text.replace('[/]tmp/chapter-1-script', f'[{str(rdir)[0]}]{str(rdir)[1:]}/chapter-1-script')
    text = text.replace('mountpoint -q', 'true || mountpoint -q')
    script_copy = work / 'scripts/maybelle-chapter-1.sh'

    ran_marker = tmp_path / 'new-run-saw-files'
    old_script = rdir / 'chapter-1-script.sh'

    start = text.index("REMOTE_SCRIPT=$(cat <<'OUTER_EOF'")
    end = text.index('\nOUTER_EOF\n)', start) + len('\nOUTER_EOF\n)')
    stub_remote = (
        "REMOTE_SCRIPT=$(cat <<'OUTER_EOF'\n"
        "#!/usr/bin/env bash\n"
        f"trap 'rm -f \"{vol}/.vault-password\" \"{rdir}/scrubber-secrets.json\" \"{old_script}\"' EXIT\n"
        f"if [ -s \"{vol}/.vault-password\" ] && [ -s \"{rdir}/scrubber-secrets.json\" ] && [ -x \"{old_script}\" ]; then\n"
        f"    echo OK > \"{ran_marker}\"\n"
        "fi\n"
        "read -r _ 2>/dev/null </dev/tty || sleep 5\n"
        "OUTER_EOF\n)"
    )
    script_copy.write_text(text[:start] + stub_remote + text[end:])
    script_copy.chmod(0o755)

    fake_bin = tmp_path / 'bin'
    fake_bin.mkdir()
    (fake_bin / 'ansible-vault').write_text('#!/bin/sh\nprintf "secret_one: supersecretvalue\\n"\n')
    (fake_bin / 'tmux').write_text(f'#!/bin/sh\nexec "{shutil.which("tmux")}" -S "{sock}" "$@"\n')
    (fake_bin / 'ssh').write_text(
        '#!/bin/bash\n'
        'if [ "$1" = "-t" ]; then exit 0; fi\n'
        'cmd="${@: -1}"\n'
        'bash -c "$cmd"\n'
    )
    for b in fake_bin.iterdir():
        b.chmod(0o755)

    (tmp_path / 'local-pw').write_text('hunter2\n')
    env = {
        **os.environ,
        'ANSIBLE_VAULT_PASSWORD_FILE': str(tmp_path / 'local-pw'),
        'PATH': f'{fake_bin}:{os.environ.get("PATH", "")}',
    }

    # Seed an already-finished 'chapter-1' tmux session waiting at "Press enter to exit", with a 0.15s delay
    # in its EXIT trap so killing it *after* upload deletes the new files deterministically.
    old_script.write_text(
        '#!/usr/bin/env bash\n'
        f'trap \'sleep 0.15; rm -f "{vol}/.vault-password" "{rdir}/scrubber-secrets.json" "{old_script}"\' EXIT\n'
        f'echo READY > "{tmp_path}/old-ready"\n'
        'read -r _ 2>/dev/null </dev/tty || sleep 30\n'
    )
    old_script.chmod(0o700)

    try:
        subprocess.run([str(fake_bin / 'tmux'), 'new-session', '-d', '-s', 'chapter-1', str(old_script)], check=True)
        for _ in range(50):
            if (tmp_path / 'old-ready').exists():
                break
            time.sleep(0.05)
        assert (tmp_path / 'old-ready').exists()

        for i in range(5):
            ran_marker.unlink(missing_ok=True)
            done = subprocess.run(['bash', str(script_copy), '--ssh'], env=env, capture_output=True, text=True, timeout=15)
            assert done.returncode == 0, f'run {i}: {done.stderr}'
            for _ in range(50):
                if ran_marker.exists():
                    break
                time.sleep(0.05)
            assert ran_marker.exists() and ran_marker.read_text().strip() == 'OK', f'run {i} lost files to old EXIT trap'
    finally:
        subprocess.run([str(fake_bin / 'tmux'), 'kill-server'], capture_output=True)


@pytest.mark.skipif(shutil.which('tmux') is None, reason='needs real tmux for controlling /dev/tty')
def test_err_trap_deletes_secrets_before_waiting_on_tty(tmp_path):
    """If an early step (apt/git) fails, ERR deletes .vault-password and scrubber-secrets.json
    BEFORE waiting at 'Press enter to exit'."""
    text = CHAPTER_1.read_text()
    start = text.index("#!/usr/bin/env bash\nset -e")
    end = text.index("\nOUTER_EOF\n)", start)
    remote_script = text[start:end]

    vol = tmp_path / 'vol'
    vol.mkdir()
    pw = vol / '.vault-password'
    scrubber = tmp_path / 'scrubber-secrets.json'
    running = tmp_path / 'chapter-1.running'
    self_path = tmp_path / 'chapter-1-script.sh'
    sock = tmp_path / 'err-tmux.sock'
    pw.write_text('hunter2\n')
    scrubber.write_text('["secret"]')

    fake_bin = tmp_path / 'bin'
    fake_bin.mkdir()
    (fake_bin / 'apt-get').write_text('#!/bin/sh\nexit 100\n')
    (fake_bin / 'apt-get').chmod(0o755)

    rendered = (
        remote_script
        .replace('__HETZNER_VOLUME_PATH__', str(vol))
        .replace('__MOUNT_POINT__', str(tmp_path / 'persist-link'))
        .replace('__REPO_DIR__', str(tmp_path / 'repo'))
        .replace('__VAULT_PASSWORD_FILE__', str(pw))
        .replace('__VOLUME_DEVICE__', '/dev/null')
        .replace('__REBUILD_IMAGES__', '')
        .replace('__DEPLOY_BY__', 'tester')
        .replace('/tmp/scrubber-secrets.json', str(scrubber))
        .replace('/tmp/chapter-1.running', str(running))
        .replace('/tmp/chapter-1-script.sh', str(self_path))
        .replace('mountpoint -q "$HETZNER_VOLUME_PATH"', 'true')
        .replace('grep -q "$HETZNER_VOLUME_PATH" /etc/fstab', 'true')
    )
    self_path.write_text(rendered)
    self_path.chmod(0o700)

    tmux = shutil.which('tmux')
    env = {**os.environ, 'PATH': f'{fake_bin}:{os.environ.get("PATH", "")}'}
    try:
        subprocess.run([tmux, '-S', str(sock), 'new-session', '-d', '-s', 'errtest', str(self_path)], env=env, check=True)
        pane = ''
        deadline = time.time() + 10
        while time.time() < deadline:
            cap = subprocess.run([tmux, '-S', str(sock), 'capture-pane', '-pt', 'errtest'], capture_output=True, text=True)
            pane = cap.stdout
            if 'Press enter to exit.' in pane:
                break
            time.sleep(0.1)
        assert 'Press enter to exit.' in pane, pane
        # Detached tmux session is STILL alive and waiting on /dev/tty, yet secrets are already gone:
        has = subprocess.run([tmux, '-S', str(sock), 'has-session', '-t', 'errtest'], capture_output=True)
        assert has.returncode == 0
        assert not pw.exists()
        assert not scrubber.exists()
        assert not running.exists()

        subprocess.run([tmux, '-S', str(sock), 'send-keys', '-t', 'errtest', 'Enter'], check=True)
        for _ in range(50):
            if not self_path.exists():
                break
            time.sleep(0.05)
        assert not self_path.exists()
    finally:
        subprocess.run([tmux, '-S', str(sock), 'kill-server'], capture_output=True)
