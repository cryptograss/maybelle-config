"""vcs-unpushed.py against real git repositories in a temporary /opt/magenta.

    python3 -m pytest hunter/ansible/roles/housekeeping/test_vcs_unpushed.py
"""

import importlib.util
import json
import os
import shutil
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


def test_a_moods_checkout_and_its_threads_worktrees_are_kept_apart(base, tmp_path):
    """A Mood's directory (Cryptograss:Moods as Directories): its checkouts sit in it, not in a
    workspace/, and each thread has a worktree of the same repo, under threads/<thread>/."""
    repo = base / 'moods/chartifacts/magenta'
    sh(tmp_path, 'clone', str(tmp_path / 'origin.git'), str(repo))
    sh(repo, 'checkout', '-b', 'main', 'origin/arthel')
    for thread in ('axis-labels', 'legend'):
        tree = base / 'moods/chartifacts/threads' / thread / 'magenta'
        tree.parent.mkdir(parents=True)
        sh(repo, 'worktree', 'add', '-b', thread, str(tree))
        (tree / 'a.txt').write_text(f'{thread}, unsaved\n')
    out = base / 'vcs-backup'
    found = vcs.run(base, out)
    assert list(found) == ['moods/chartifacts/magenta']
    # The threads' branches have no commits of their own yet; what's only here is their unsaved edits.
    assert len(found['moods/chartifacts/magenta']['dirty']) == 2
    kept = out / 'moods/chartifacts/magenta'
    for thread in ('axis-labels', 'legend'):
        assert f'{thread}, unsaved' in (kept / f'threads__{thread}__magenta.patch').read_text()


# A checkout is written by whoever works in it, and this runs as root on hunter: nothing a checkout
# says may be run, and what it writes down is read as data (re, docs-tests-qa, 28 Nov 2026).

def a_program(tmp_path, body='exit 0'):
    """A program that leaves a mark when anything runs it, and the mark."""
    mark = tmp_path / 'it-ran'
    program = tmp_path / 'program'
    program.write_text(f'#!/bin/sh\ntouch {mark}\n{body}\n')
    program.chmod(0o755)
    return program, mark


def by_fsmonitor(repo, program):
    """core.fsmonitor: run whenever git reads the index, even in a checkout with nothing to keep."""
    sh(repo, 'config', 'core.fsmonitor', str(program))


def by_external_diff(repo, program):
    sh(repo, 'config', 'diff.external', str(program))
    (repo / 'a.txt').write_text('one, edited\n')


def by_filter(repo, program):
    """A clean filter: named in .gitattributes, which is in the worktree, and run from config."""
    program.write_text(program.read_text().replace('exit 0', 'cat'))
    sh(repo, 'config', 'filter.theirs.clean', str(program))
    (repo / '.gitattributes').write_text('*.txt filter=theirs\n')
    (repo / 'a.txt').write_text('one, edited\n')


def by_hook(repo, program):
    """post-index-change: run when git saves the index, as `git diff` does once it finds a file whose
    time changed and whose content didn't."""
    hook = repo / '.git/hooks/post-index-change'
    hook.parent.mkdir(exist_ok=True)
    hook.write_text(program.read_text())
    hook.chmod(0o755)
    os.utime(repo / 'a.txt', (1, 1))


def by_submodule(repo, program):
    """A checkout inside the checkout, entered in its index: git asks it for its status, under its config."""
    inner = repo / 'inner'
    inner.mkdir()
    sh(inner, 'init', '-b', 'main')
    (inner / 'b.txt').write_text('two\n')
    sh(inner, 'add', '.')
    sh(inner, 'commit', '-m', 'two')
    sh(repo, 'add', 'inner')
    sh(repo, 'commit', '-m', 'with inner')
    sh(repo, 'push', 'origin', 'main:red')
    sh(repo, 'fetch', 'origin')
    sh(inner, 'config', 'core.fsmonitor', str(program))
    (inner / 'b.txt').write_text('two, edited\n')


@pytest.mark.parametrize('plant', [by_fsmonitor, by_external_diff, by_filter, by_hook, by_submodule])
def test_nothing_a_checkout_names_is_run(base, tmp_path, plant):
    repo = base / 'emergent-re/agents/re/workspace/red'
    program, mark = a_program(tmp_path)
    plant(repo, program)
    vcs.run(base, base / 'vcs-backup')
    assert not mark.exists()


def test_an_edit_is_still_kept_from_a_checkout_that_names_programs(base, tmp_path):
    repo = base / 'emergent-re/agents/re/workspace/red'
    program, mark = a_program(tmp_path)
    by_external_diff(repo, program)
    by_fsmonitor(repo, program)
    out = base / 'vcs-backup'
    assert list(vcs.run(base, out)) == ['emergent-re/re/workspace/red']
    assert 'one, edited' in (out / 'emergent-re/re/workspace__red/main.patch').read_text()
    assert not mark.exists()


def test_a_branch_named_like_an_option_is_a_branch(base, tmp_path):
    """Nothing in git's rule for ref names stops one beginning with a dash, and as a bare word on a
    command line `--output=<file>` is an option that empties the file."""
    repo = base / 'justin/home/workspace/arthel'
    precious = tmp_path / 'precious'
    precious.write_text('kept\n')
    sh(repo, 'checkout', '-b', 'only-here')
    (repo / 'b.txt').write_text('two\n')
    sh(repo, 'add', '.')
    sh(repo, 'commit', '-m', 'two')
    at = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=repo, capture_output=True, text=True).stdout
    ref = repo / f'.git/refs/heads/--output={precious}'
    ref.parent.mkdir(parents=True)
    ref.write_text(at)
    out = base / 'vcs-backup'
    found = vcs.run(base, out)
    assert precious.read_text() == 'kept\n'
    assert found == {'justin/magent/workspace/arthel': {'branches': [f'--output={precious}', 'only-here']}}
    heads = subprocess.run(['git', 'bundle', 'list-heads', str(out / 'justin/magent/workspace__arthel/branches.bundle')],
                           cwd=tmp_path, capture_output=True, text=True).stdout
    assert f'refs/heads/--output={precious}' in heads and 'refs/heads/only-here' in heads


def test_nothing_is_written_into_a_checkout(base):
    """git saves the index when it finds a file whose time changed and whose content didn't: not here."""
    repo = base / 'emergent-re/agents/re/workspace/red'
    os.utime(repo / 'a.txt', (1, 1))
    before = {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in repo.rglob('*') if p.is_file()}
    out = base / 'vcs-backup'
    vcs.run(base, out)
    assert {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in repo.rglob('*') if p.is_file()} == before
    assert sorted(p.name for p in out.iterdir()) == ['manifest.json']   # and nothing of ours is left behind


# A container has a Mood's directory at /moods/<slug> and an agent's home at /home/<agent>; hunter has
# them under /opt/magenta. A linked worktree's own files name it as the container saw it.

def as_a_container_wrote_it(repo, name, inside_path):
    (repo / '.git/worktrees' / name / 'gitdir').write_text(f'{inside_path}/.git\n')


def test_a_moods_worktree_is_found_by_the_path_its_container_gave_it(base, tmp_path):
    slug = f'vcs-test-{tmp_path.name}'
    assert not Path('/moods', slug).exists()
    repo = base / 'moods' / slug / 'magenta'
    sh(tmp_path, 'clone', str(tmp_path / 'origin.git'), str(repo))
    sh(repo, 'checkout', '-b', 'main', 'origin/arthel')
    tree = base / 'moods' / slug / 'threads/legend/magenta'
    tree.parent.mkdir(parents=True)
    sh(repo, 'worktree', 'add', '-b', 'legend', str(tree))
    as_a_container_wrote_it(repo, 'magenta', f'/moods/{slug}/threads/legend/magenta')
    (tree / 'a.txt').write_text('legend, unsaved\n')
    out = base / 'vcs-backup'
    found = vcs.run(base, out)
    assert found == {f'moods/{slug}/magenta': {'dirty': [str(tree)]}}
    assert 'legend, unsaved' in (out / 'moods' / slug / 'magenta/threads__legend__magenta.patch').read_text()


def test_an_agents_worktree_is_found_by_the_path_its_container_gave_it(base, tmp_path):
    name = f'vcs-test-{tmp_path.name}'
    assert not Path('/home/re', name).exists()
    repo = base / 'emergent-re/agents/re/workspace/red'
    tree = base / 'emergent-re/agents/re' / name / 'red'
    tree.parent.mkdir(parents=True)
    sh(repo, 'worktree', 'add', '-b', 'aside', str(tree))
    as_a_container_wrote_it(repo, 'red', f'/home/re/{name}/red')
    (tree / 'a.txt').write_text('aside, unsaved\n')
    out = base / 'vcs-backup'
    found = vcs.run(base, out)
    assert found == {'emergent-re/re/workspace/red': {'dirty': [str(tree)]}}
    kept = list((out / 'emergent-re/re/workspace__red').iterdir())
    assert len(kept) == 1 and 'aside, unsaved' in kept[0].read_text()


@pytest.mark.parametrize('written', ['{outside}', '/moods/chartifacts/../../../outside', '/moods/chartifacts/way-out'])
def test_a_worktree_written_down_as_somewhere_else_is_not_read(base, tmp_path, written):
    """What a checkout writes down as its worktree is a claim: one outside the Moods' directories and
    its own container's homes is not read, by any road (a plain path, a .., a symlink)."""
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'a.txt').write_text('not theirs to have\n')
    repo = base / 'moods/chartifacts/magenta'
    sh(tmp_path, 'clone', str(tmp_path / 'origin.git'), str(repo))
    sh(repo, 'checkout', '-b', 'main', 'origin/arthel')
    tree = base / 'moods/chartifacts/threads/legend/magenta'
    tree.parent.mkdir(parents=True)
    sh(repo, 'worktree', 'add', '-b', 'legend', str(tree))
    (base / 'moods/chartifacts/way-out').symlink_to(outside)
    as_a_container_wrote_it(repo, 'magenta', written.format(outside=outside))
    out = base / 'vcs-backup'
    assert vcs.run(base, out) == {}
    assert sorted(p.name for p in out.iterdir()) == ['manifest.json']


def test_an_index_that_is_a_symlink_leads_nowhere(base, tmp_path):
    """git saves a refreshed index through a lock beside wherever its path leads: a checkout whose index
    is a symlink had root write a file of its choosing."""
    repo = base / 'emergent-re/agents/re/workspace/red'
    precious = tmp_path / 'precious'
    (repo / '.git/index').rename(precious)
    (repo / '.git/index').symlink_to(precious)
    os.utime(repo / 'a.txt', (1, 1))
    before = (precious.read_bytes(), precious.stat().st_mtime_ns, precious.stat().st_ino)
    vcs.run(base, base / 'vcs-backup')
    assert (precious.read_bytes(), precious.stat().st_mtime_ns, precious.stat().st_ino) == before


def test_a_split_index_is_read_whole(base):
    repo = base / 'emergent-re/agents/re/workspace/red'
    sh(repo, 'update-index', '--split-index')
    assert list((repo / '.git').glob('sharedindex.*'))
    (repo / 'a.txt').write_text('one, edited\n')
    out = base / 'vcs-backup'
    assert vcs.run(base, out) == {'emergent-re/re/workspace/red': {'dirty': [str(repo)]}}
    assert 'one, edited' in (out / 'emergent-re/re/workspace__red/main.patch').read_text()


def test_a_checkout_with_no_commit_yet_is_passed_over(base):
    repo = base / 'emergent-re/agents/re/workspace/just-begun'
    repo.mkdir()
    sh(repo, 'init', '-b', 'main')
    (repo / 'a.txt').write_text('one\n')
    sh(repo, 'add', '.')
    assert vcs.run(base, base / 'vcs-backup') == {}


def test_refs_that_lead_somewhere_else_are_not_this_checkouts(base, tmp_path):
    repo = base / 'emergent-re/agents/re/workspace/red'
    sh(repo, 'checkout', '-b', 'planted')
    (repo / 'b.txt').write_text('two\n')
    sh(repo, 'add', '.')
    sh(repo, 'commit', '-m', 'two')
    sh(repo, 'checkout', 'main')
    elsewhere = tmp_path / 'elsewhere'
    (repo / '.git/refs/heads').rename(elsewhere)
    (repo / '.git/refs/heads').symlink_to(elsewhere)
    assert vcs.run(base, base / 'vcs-backup') == {}


def test_a_name_that_is_not_utf8_stops_nothing(base):
    """A branch's name is bytes. One that isn't UTF-8 is kept under its name, and the next checkout is still read."""
    first = base / 'emergent-re/agents/re/workspace/red'
    at = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=first, capture_output=True).stdout
    sh(first, 'checkout', '-b', 'only-here')
    (first / 'b.txt').write_text('two\n')
    sh(first, 'add', '.')
    sh(first, 'commit', '-m', 'two')
    tip = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=first, capture_output=True).stdout
    with open(os.fsencode(first / '.git/refs/heads') + b'/caf\xe9', 'wb') as f:
        f.write(tip)
    later = base / 'justin/home/workspace/arthel'
    (later / 'a.txt').write_text('one, edited\n')
    out = base / 'vcs-backup'
    found = vcs.run(base, out)
    assert found['emergent-re/re/workspace/red'] == {'branches': ['caf\udce9', 'only-here']}
    assert found['justin/magent/workspace/arthel'] == {'dirty': [str(later)]}
    heads = subprocess.run(['git', 'bundle', 'list-heads', str(out / 'emergent-re/re/workspace__red/branches.bundle')],
                           cwd=base, capture_output=True).stdout
    assert b'refs/heads/caf\xe9' in heads
    json.loads((out / 'manifest.json').read_text())


def test_a_directory_that_is_no_longer_the_worktree_is_not_compared(base, tmp_path):
    """A worktree's directory removed by hand, and the place since used for something else: its entry
    still names the path. Compared, every file the branch had would read as deleted."""
    repo = base / 'emergent-re/agents/re/workspace/red'
    tree = base / 'emergent-re/agents/re/aside/red'
    tree.parent.mkdir(parents=True)
    sh(repo, 'worktree', 'add', '-b', 'aside', str(tree))
    shutil.rmtree(tree)
    tree.mkdir()
    (tree / 'something-else.txt').write_text('not a.txt\n')
    assert vcs.run(base, base / 'vcs-backup') == {}


def test_refs_this_cannot_read_are_said_not_passed_over(base):
    repo = base / 'emergent-re/agents/re/workspace/as-reftable'
    repo.mkdir()
    made = subprocess.run(['git', 'init', '-q', '-b', 'main', '--ref-format=reftable'], cwd=repo, capture_output=True)
    if made.returncode != 0:
        pytest.skip('this git makes no reftable')
    found = vcs.run(base, base / 'vcs-backup')
    assert list(found) == ['emergent-re/re/workspace/as-reftable']
    assert 'reftable' in found['emergent-re/re/workspace/as-reftable']['error']
