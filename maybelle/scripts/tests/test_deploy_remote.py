"""The remote deploy scripts: every ssh they run keeps the connection alive.

hunter's image build says nothing for ten minutes or more, so without a keepalive the
connection is dropped and the laptop hangs while maybelle finishes (Justin, #ops,
25 Nov 2026). Each script here is run with a fake ssh on PATH, and the argv it recorded
is what the test reads.
"""

import os
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent

# Each script, and what it answers on the way to its ssh (its prompts, in order).
REMOTE_SCRIPTS = [
    ('deploy-hunter-remote.py', 'y\n'),
    ('deploy-delivery-kid-remote.py', 'y\n'),
    ('deploy-pickipedia-remote.py', 'y\n'),
    ('import-pickipedia-remote.py', 'y\nyes\n'),
]


def run_with_fake_ssh(tmp_path, script, answers):
    """Run `script` with a fake ssh, and give back (ssh's argv, the run)."""
    calls = tmp_path / 'ssh-argv.log'
    fake_bin = tmp_path / 'bin'
    fake_bin.mkdir()
    ssh = fake_bin / 'ssh'
    # Reads its stdin to the end, as the real one does with the vault password on it.
    ssh.write_text(f'#!/bin/sh\nfor a in "$@"; do echo "$a" >> "{calls}"; done\ncat > /dev/null\n')
    ssh.chmod(0o755)

    env = dict(os.environ, PATH=f'{fake_bin}:{os.environ["PATH"]}',
               ANSIBLE_VAULT_PASSWORD='hunter2')
    env.pop('ANSIBLE_VAULT_PASSWORD_FILE', None)
    run = subprocess.run(['python3', str(SCRIPTS / script)], input=answers, text=True,
                         capture_output=True, env=env, cwd=tmp_path, timeout=60)
    argv = calls.read_text().splitlines() if calls.exists() else []
    return argv, run


@pytest.mark.parametrize('script,answers', REMOTE_SCRIPTS)
def test_ssh_is_kept_alive(tmp_path, script, answers):
    argv, run = run_with_fake_ssh(tmp_path, script, answers)
    assert argv, f'{script} never ran ssh: {run.stdout[-500:]} {run.stderr[-500:]}'
    assert 'ServerAliveInterval=15' in argv, argv
    assert 'ServerAliveCountMax=20' in argv, argv
    assert 'ConnectTimeout=15' in argv, argv


@pytest.mark.parametrize('script,answers', REMOTE_SCRIPTS)
def test_the_option_comes_before_the_host(tmp_path, script, answers):
    """An -o after the host is the remote command's word, not ssh's own."""
    argv, _ = run_with_fake_ssh(tmp_path, script, answers)
    host = next(i for i, a in enumerate(argv) if a.endswith('@maybelle.cryptograss.live'))
    assert argv.index('ServerAliveInterval=15') < host, argv
    assert argv[host + 1:], f'{script} gave ssh no command to run: {argv}'
