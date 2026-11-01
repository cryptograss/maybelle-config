"""vcs-unpushed.py against real git repositories in a temporary /opt/magenta.

    python3 -m pytest hunter/ansible/roles/housekeeping/test_vcs_unpushed.py
"""

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('vcs_unpushed', Path(__file__).parent / 'files' / 'vcs-unpushed.py')
vcs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vcs)


def sh(cwd, *args):
    subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@t', *args], cwd=cwd, check=True,
                   capture_output=True)


@pytest.fixture
def base(tmp_path):
    origin = tmp_path / 'origin.git'
    sh(tmp_path, 'init', '--bare', '-b', 'main', str(origin))
    homes = {'justin/home': 'arthel', 'emergent-re/agents/re': 'red'}
    for home, name in homes.items():
        repo = tmp_path / 'opt' / home / 'workspace' / name
        repo.mkdir(parents=True)
        sh(repo, 'init', '-b', 'main')
        (repo / 'a.txt').write_text('one\n')
        sh(repo, 'add', '.')
        sh(repo, 'commit', '-m', 'one')
        sh(repo, 'remote', 'add', 'origin', str(origin))
        sh(repo, 'push', 'origin', f'main:{name}')
        sh(repo, 'fetch', 'origin')
    return tmp_path / 'opt'


def test_nothing_unpushed_writes_only_the_manifest(base):
    out = base / 'vcs-backup'
    assert vcs.run(base, out) == {}
    assert json.loads((out / 'manifest.json').read_text())['found'] == {}
    assert sorted(p.name for p in out.iterdir()) == ['manifest.json']


def test_a_branch_on_no_remote_is_bundled_and_restorable(base, tmp_path):
    repo = base / 'justin/home/workspace/arthel'
    sh(repo, 'checkout', '-b', 'only-here')
    (repo / 'b.txt').write_text('two\n')
    sh(repo, 'add', '.')
    sh(repo, 'commit', '-m', 'two')
    out = base / 'vcs-backup'
    found = vcs.run(base, out)
    assert found == {'justin/magent/workspace/arthel': {'branches': ['only-here']}}
    bundle = out / 'justin/magent/workspace__arthel/branches.bundle'
    # It restores: clone the bundle onto the remote's history.
    restored = tmp_path / 'restored'
    sh(tmp_path, 'clone', str(tmp_path / 'origin.git'), str(restored))
    sh(restored, 'fetch', str(bundle), 'only-here:only-here')
    assert subprocess.run(['git', 'show', 'only-here:b.txt'], cwd=restored, capture_output=True,
                          text=True).stdout == 'two\n'


def test_uncommitted_edits_in_an_agents_home_become_a_patch(base):
    repo = base / 'emergent-re/agents/re/workspace/red'
    (repo / 'a.txt').write_text('one, edited\n')
    out = base / 'vcs-backup'
    found = vcs.run(base, out)
    assert list(found) == ['emergent-re/re/workspace/red']
    assert 'one, edited' in (out / 'emergent-re/re/workspace__red/main.patch').read_text()
    # Read only: the edit is still there, uncommitted.
    assert (repo / 'a.txt').read_text() == 'one, edited\n'


def test_a_pushed_branch_drops_out_on_the_next_run(base):
    repo = base / 'justin/home/workspace/arthel'
    sh(repo, 'checkout', '-b', 'later-pushed')
    (repo / 'c.txt').write_text('three\n')
    sh(repo, 'add', '.')
    sh(repo, 'commit', '-m', 'three')
    out = base / 'vcs-backup'
    assert vcs.run(base, out)
    sh(repo, 'push', 'origin', 'later-pushed')
    assert vcs.run(base, out) == {}
    assert not (out / 'justin').exists()
