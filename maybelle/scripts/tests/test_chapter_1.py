"""maybelle-chapter-1.sh: vault password cleanup when startup aborts, and ssh fallback when mosh UDP is blocked."""

import os
import shutil
import subprocess
from pathlib import Path

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
    fake_bin = tmp_path / 'bin'
    fake_bin.mkdir()

    (fake_bin / 'ansible-vault').write_text('#!/bin/sh\nprintf "secret_one: supersecretvalue\\n"\n')
    (fake_bin / 'scp').write_text(
        f'#!/bin/sh\n'
        f'cp "$1" "{vol}/.vault-password"\n'
    )
    (fake_bin / 'mosh').write_text(
        f'#!/bin/sh\n'
        f'echo "mosh $*" >> "{calls}"\n'
        f'exit {"1" if mosh_fails else "0"}\n'
    )
    (fake_bin / 'ssh').write_text(
        f'#!/bin/bash\n'
        f'echo "ssh $*" >> "{calls}"\n'
        f'cmd="${{@: -1}}"\n'
        f'case "$cmd" in\n'
        f'  *pgrep*) exit 1 ;;\n'
        f'  *"cat > /tmp/scrubber-secrets.json"*) cat > "{remote}/tmp/scrubber-secrets.json" ;;\n'
        f'  *"cat > /tmp/chapter-1-script.sh"*) cat > "{remote}/tmp/chapter-1-script.sh" ;;\n'
        f'  *"tmux new-session -d"*) exit {"1" if tmux_fails else "0"} ;;\n'
        f'  *"rm -f"*) rm -f "{vol}/.vault-password" "{remote}/tmp/scrubber-secrets.json" "{remote}/tmp/chapter-1-script.sh" ;;\n'
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
    return script_copy, vol / '.vault-password', remote / 'tmp/scrubber-secrets.json', calls, env


def test_aborted_startup_cleans_up_vault_and_scrubber_files(tmp_path):
    script, remote_pw, remote_scrubber, _, env = make_env(tmp_path, tmux_fails=True)
    done = subprocess.run(['bash', str(script)], env=env, capture_output=True, text=True)
    assert done.returncode != 0
    assert not remote_pw.exists()
    assert not remote_scrubber.exists()


def test_blocked_mosh_udp_keeps_armed_files_and_falls_back_to_ssh(tmp_path):
    script, remote_pw, remote_scrubber, calls, env = make_env(tmp_path, mosh_fails=True)
    done = subprocess.run(['bash', str(script)], env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert remote_pw.exists() and remote_scrubber.exists()  # owned by the running tmux session's EXIT trap now
    logged = calls.read_text()
    assert 'mosh ' in logged
    assert 'ssh -t root@maybelle.test tmux attach-session' in logged


def test_ssh_flag_skips_mosh_entirely(tmp_path):
    script, remote_pw, _, calls, env = make_env(tmp_path, mosh_fails=True)
    done = subprocess.run(['bash', str(script), '--ssh'], env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert remote_pw.exists()
    logged = calls.read_text()
    assert 'mosh ' not in logged
    assert 'ssh -t root@maybelle.test tmux attach-session' in logged
