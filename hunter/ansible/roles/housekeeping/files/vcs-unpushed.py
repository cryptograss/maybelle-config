#!/usr/bin/env python3
"""Keep a copy of the git work that exists only on hunter, for maybelle to pull.

hunter is meant to be cheap to lose (Justin, emergence, 1 Nov 2026): the record
lives on maybelle, the code on GitHub, the secrets in the vault. What slipped in
is work in the homes that was never pushed: a branch with commits on no remote,
or edits never committed. This finds both, hourly, in every home under
/opt/magenta (people's, Moods', agents') and in every Mood's directory (/opt/magenta/moods/<slug>,
Cryptograss:Moods as Directories), and writes for each checkout that has any:

    <out>/<container>/<home>/<repo path, / as __>/branches.bundle   the branches with commits on no remote
    <out>/<container>/<home>/<repo path, / as __>/<worktree>.patch  uncommitted changes to tracked files
    <out>/manifest.json                                             what was found, and when

A file git doesn't track is not kept: one never `git add`ed, or one in a directory that is no checkout.
What this could not read is in the manifest too, never passed over: a worktree under "unread" with the
reason, a whole checkout under "error".

No git command here runs inside a checkout, or is handed a path inside one that it could write. This runs as root, and a checkout is written by whoever
works in it, from containers that are meant to have no way to hunter itself (CONTAINERS.md). git runs
the programs that a repository's own config and hooks name (an fsmonitor, an external diff, a filter,
a hook), so `git -C <checkout>` was root running whatever a checkout said (re, docs-tests-qa, 28 Nov
2026). Each checkout is read as data instead: a git directory of our own, made fresh for it, borrows
its objects and copies its refs (borrow()), and every command runs against that one. The checkout's
config and hooks are never opened, and nothing is written into it.

maybelle rsyncs <out> (maybelle.yml: hunter VCS backup). Each run replaces <out> whole, so a branch
pushed since drops out of it.

    vcs-unpushed.py [--base-dir /opt/magenta] [--out /opt/magenta/vcs-backup]
"""

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

SKIP_DIRS = {'node_modules', '.venv', 'venv', '.cache', '.npm', '.local', 'vendor', '__pycache__'}
MAX_DEPTH = 4  # under a home's workspace/: workspace/<repo>, and a little deeper
# Where a container has what hunter keeps under /opt/magenta (user-instance/templates/docker-compose.yml.j2).
# A linked worktree's files name it by the path the container saw, which hunter doesn't have.
INSIDE_MOODS = Path('/moods')  # every Mood's directory, in every container
INSIDE_HOME = Path('/home')    # /home/<agent>; and /home/magent, the container's own home
LINE = 4096                    # HEAD, a loose ref, a worktree's gitdir: one line each
PACKED = 64 * 1024 * 1024      # packed-refs, shallow
INDEX = 256 * 1024 * 1024      # a worktree's index
# A full ref name starts with refs/, so git can't take it for an option, as it would a branch someone named
# --output=/somewhere; and none of what git's own rule for ref names leaves out.
FULL_REF = re.compile(r'refs/[^\s\x00-\x1f\x7f~^:?*\[\\]+')
OBJECT_NAME = re.compile(r'[0-9a-f]{40}(?:[0-9a-f]{24})?')
# A missing object is an error, never a fetch; no lock git can do without is taken; nothing waits to be typed.
GIT_ENV = {'GIT_OPTIONAL_LOCKS': '0', 'GIT_NO_LAZY_FETCH': '1', 'GIT_TERMINAL_PROMPT': '0'}


def git(ours, *args, out=None, work_tree=None, index=None):
    """git against a git directory of our own (borrow()), never against one a checkout holds."""
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_ENV)
    cmd = ['git', '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false', '--git-dir', str(ours)]
    if work_tree is not None:
        cmd += ['--work-tree', str(work_tree)]
        env['GIT_INDEX_FILE'] = str(index)
    return subprocess.run([*cmd, *args], cwd=str(ours), env=env, stdin=subprocess.DEVNULL, timeout=300,
                          capture_output=out is None, stdout=out,
                          # A ref's name is bytes somebody chose: kept whole, to hand back to git as they were.
                          **({'text': True, 'errors': 'surrogateescape'} if out is None else {}))


def read(path, limit):
    """(bytes, stat) of a regular file, or None: never through a symlink, never a FIFO or a device, never
    more than limit."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    with os.fdopen(fd, 'rb') as f:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return None
        data = f.read(limit + 1)
    return (data, st) if len(data) <= limit else None


def line(path):
    got = read(path, LINE)
    return got[0].decode('utf-8', 'replace').strip() if got else None


def copy(src, dest, limit):
    """True if dest is now src's bytes, with src's times."""
    got = read(src, limit)
    if not got:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(got[0])
    os.utime(dest, ns=(got[1].st_atime_ns, got[1].st_mtime_ns))
    return True


def homes(base):
    """(container, home name, path, where its checkouts are, inside) for each home: a container's own and
    its agents', whose checkouts are in workspace/; and each Mood's directory, whose are in it. inside is
    the (path in the container, path on hunter) pairs for what that home's container can reach."""
    moods = base / 'moods'
    shared = [(INSIDE_MOODS, moods)] if moods.is_dir() else []
    for container in sorted(p for p in base.iterdir() if p.is_dir()):
        own = container / 'home'
        agents = container / 'agents'
        agents = sorted(p for p in agents.iterdir() if p.is_dir()) if agents.is_dir() else []
        inside = shared + ([(INSIDE_HOME / 'magent', own)] if own.is_dir() else []) \
            + [(INSIDE_HOME / agent.name, agent) for agent in agents]
        if own.is_dir():
            yield container.name, 'magent', own, own / 'workspace', inside
        for agent in agents:
            yield container.name, agent.name, agent, agent / 'workspace', inside
    if moods.is_dir():
        for mood in sorted(p for p in moods.iterdir() if p.is_dir()):
            yield 'moods', mood.name, mood, mood, shared


def checkouts(root):
    """Each checkout under root with its own .git directory (a worktree's .git is a file:
    its branches are its main checkout's, and its edits are found from there)."""
    if not root.is_dir():
        return
    for dirpath, dirnames, _ in os.walk(root):
        here = Path(dirpath)
        if (here / '.git').is_dir():
            yield here
            dirnames[:] = []
            continue
        depth = len(here.relative_to(root).parts)
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith('.') and depth < MAX_DEPTH]


def borrow(git_dir, scratch):
    """A git directory of our own, in scratch, that reads a checkout's objects and refs as data.

    Its config is the one `git init` wrote and its hooks are nobody's, so nothing the checkout says is
    run. The objects are borrowed in place (alternates), not copied; the branches, the remotes' branches,
    packed-refs and shallow are copied, a few small files. Loose refs under refs/heads and refs/remotes
    only: what's unpushed is asked of those two."""
    made = subprocess.run(['git', 'init', '-q', str(scratch)], cwd='/', stdin=subprocess.DEVNULL,
                          env={k: v for k, v in os.environ.items() if not k.startswith('GIT_')},
                          capture_output=True, text=True, timeout=60)
    if made.returncode != 0:
        raise OSError(f'git init: {made.stderr.strip()[-200:]}')
    ours = scratch / '.git'
    objects = git_dir / 'objects'
    if '\n' in str(objects):
        raise OSError('a newline in the path of its objects')
    if (git_dir / 'reftable').exists():
        raise OSError('its refs are kept as a reftable, which this does not read: nothing of it is kept')
    (ours / 'objects' / 'info').mkdir(parents=True, exist_ok=True)
    (ours / 'objects' / 'info' / 'alternates').write_bytes(os.fsencode(objects) + b'\n')
    for name in ('packed-refs', 'shallow'):
        copy(git_dir / name, ours / name, PACKED)
    for kind in ('heads', 'remotes'):
        top = git_dir / 'refs' / kind
        if not top.resolve().is_relative_to(git_dir.resolve()):
            continue  # a symlink to somewhere else: not this checkout's refs, and not ours to walk
        for dirpath, _, filenames in os.walk(top):
            for name in filenames:
                src = Path(dirpath) / name
                copy(src, ours / 'refs' / kind / src.relative_to(top), LINE)
    return ours


def unpushed(ours):
    """Full names of the branches whose commits are on no remote."""
    got = git(ours, 'for-each-ref', '--format=%(refname)', 'refs/heads')
    if got.returncode != 0:
        return []
    found = []
    for ref in got.stdout.splitlines():
        if not FULL_REF.fullmatch(ref):
            continue
        ahead = git(ours, 'rev-list', '--count', ref, '--not', '--remotes')
        if ahead.returncode == 0 and ahead.stdout.strip() not in ('', '0'):
            found.append(ref)
    return found


def on_hunter(recorded, inside):
    """Where hunter has a directory that a container wrote down, or None if it has it nowhere we may read."""
    if not recorded.is_absolute() or '..' in recorded.parts:
        return None
    for theirs, ours in inside:
        for prefix in (theirs, ours):  # ours: written down from hunter itself, as the tests do
            try:
                found = ours / recorded.relative_to(prefix)
            except ValueError:
                continue
            # Inside what that prefix stands for, after any symlink on the way: never a path of hunter's own.
            if found.is_dir() and found.resolve().is_relative_to(ours.resolve()):
                return found
    return None


def worktrees(repo, inside):
    """(worktree, where its HEAD and index are) for the checkout, then for each linked worktree hunter has.

    Read from the checkout's own files: `git worktree list` would be git run inside it, and it answers
    with the paths a container saw (/moods/<slug>/threads/..., /home/<agent>/...), none of which is a
    directory on hunter, so a linked worktree's edits were never kept."""
    git_dir = repo / '.git'
    yield repo, git_dir
    linked = git_dir / 'worktrees'
    if not linked.is_dir():
        return
    for meta in sorted(p for p in linked.iterdir() if p.is_dir()):
        recorded = line(meta / 'gitdir')  # <the worktree>/.git
        tree = on_hunter(Path(recorded).parent, inside) if recorded else None
        # And the directory says so too (its .git is a file naming this entry): one since given to
        # something else would be compared with an index that was never its own.
        back = (line(tree / '.git') or '') if tree is not None else ''
        if back.startswith('gitdir:') and Path(back[len('gitdir:'):].strip()).parts[-2:] == ('worktrees', meta.name):
            yield tree, meta


def commit_at(ours, head):
    """The commit a worktree is at, read from its HEAD file; None if it has none yet."""
    said = line(head)
    if not said:
        return None
    name = said[len('ref: '):].strip() if said.startswith('ref: ') else said
    if not (FULL_REF.fullmatch(name) or OBJECT_NAME.fullmatch(name)):
        return None
    got = git(ours, 'rev-parse', '--verify', '--quiet', '--end-of-options', name + '^{commit}')
    at = got.stdout.strip() if got.returncode == 0 else ''
    return at if OBJECT_NAME.fullmatch(at) else None


class Unread(Exception):
    """Why a worktree's edits can't be compared, in words for the manifest."""


def index_of(tree, meta, ours, scratch):
    """A copy of a worktree's index for git to read; None if nothing is checked out there, so that it
    has nothing of its own to compare. Unread, saying why, if it has files and no index this will read:
    its edits are then not kept, and that is said, not passed over (magent's review, 28 Nov 2026: a
    checkout whose index was a symlink dropped out of the manifest with its edits).

    A copy, in a place of ours: git saves an index it has refreshed (it does whenever a file's time
    changed and its content didn't, which is also how it knows not to call that file edited), through
    a lock beside wherever the path leads, and a checkout can make its index a symlink to anywhere. The
    copy keeps the file's time, which is how git tells an entry written in the index's own second from
    one that is settled."""
    theirs = meta / 'index'
    if not os.path.lexists(theirs):
        try:
            empty = all(name == '.git' for name in os.listdir(tree))
        except OSError as e:
            raise Unread(f'its directory could not be listed ({e.strerror}), so its edits are not kept')
        if empty:
            return None
        raise Unread('it has files and no index, so its edits are not kept')
    fd, name = tempfile.mkstemp(prefix='index-', dir=scratch)
    os.close(fd)
    index = Path(name)
    if not copy(theirs, index, INDEX):
        raise Unread(f'its index is not a plain file of at most {INDEX // 2**20} MB (a symlink, or '
                     'something else put there), so its edits are not kept')
    for shared in meta.glob('sharedindex.*'):  # a split index keeps most of itself beside it
        copy(shared, ours / shared.name, INDEX)
    return index


def diff(ours, tree, index, at, *how, out=None):
    """The worktree against the commit it is at, as git has it: no program of anyone's between. A
    submodule's own checkout is never asked (git would run `git status` inside it); a commit it was
    moved to still shows."""
    return git(ours, 'diff', '--no-ext-diff', '--no-textconv', '--ignore-submodules=dirty', '--submodule=short',
               *how, at, '--', work_tree=tree, index=index, out=out)


def keep(repo, dest, inside, scratch):
    """Write what only this checkout holds into dest; a summary, or None if nothing."""
    summary = {}
    ours = borrow(repo / '.git', scratch)
    branches = unpushed(ours)
    if branches:
        dest.mkdir(parents=True, exist_ok=True)
        made = git(ours, 'bundle', 'create', str(dest / 'branches.bundle'), *branches, '--not', '--remotes')
        summary['branches'] = [b[len('refs/heads/'):] for b in branches] if made.returncode == 0 \
            else {'error': made.stderr.strip()[-300:]}
    for tree, meta in worktrees(repo, inside):
        at = commit_at(ours, meta / 'HEAD')
        if at is None:
            continue  # no commit yet: nothing to compare with
        try:
            index = index_of(tree, meta, ours, scratch)
        except Unread as why:
            summary.setdefault('unread', []).append(f'{tree}: {why}')
            continue
        if index is None:
            continue
        dirty = diff(ours, tree, index, at, '--quiet')
        if dirty.returncode == 1:
            dest.mkdir(parents=True, exist_ok=True)
            # Named by where it is beside its checkout: a Mood's threads each have a worktree
            # of the same repo (threads/<thread>/<repo>), which tree.name alone would mix up.
            name = 'main' if tree == repo else os.path.relpath(tree, repo.parent).replace('/', '__')
            with open(dest / f'{name}.patch', 'w') as f:
                diff(ours, tree, index, at, '--binary', out=f)
            summary.setdefault('dirty', []).append(str(tree))
        elif dirty.returncode != 0:
            # Said, not skipped: a worktree git couldn't compare is one whose edits aren't kept.
            summary.setdefault('unread', []).append(f'{tree}: git could not compare it: {(dirty.stderr or "").strip()[-200:]}')
    return summary or None


def run(base, out):
    part = out.with_name(out.name + '.part')
    if part.exists():
        shutil.rmtree(part)
    part.mkdir(parents=True)
    found = {}
    for container, home_name, home, root, inside in homes(base):
        if home.resolve() == out.resolve() or container in (out.name, part.name):
            continue
        for repo in checkouts(root):
            rel = repo.relative_to(home)
            key = f'{container}/{home_name}/{rel}'
            scratch = Path(tempfile.mkdtemp(prefix='.scratch-', dir=part))  # ours alone: 0700, in a directory of root's
            try:
                summary = keep(repo, part / container / home_name / str(rel).replace('/', '__'), inside, scratch)
            except Exception as e:  # whatever one checkout does, the rest are still kept, and it is said
                summary = {'error': f'{type(e).__name__}: {e}'[:300]}
            finally:
                shutil.rmtree(scratch, ignore_errors=True)
            if summary:
                found[key] = summary
    (part / 'manifest.json').write_text(json.dumps(
        {'at': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'found': found}, indent=1))
    if out.exists():
        shutil.rmtree(out)
    part.rename(out)
    return found


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--base-dir', default='/opt/magenta')
    parser.add_argument('--out', default='/opt/magenta/vcs-backup')
    args = parser.parse_args(argv)
    found = run(Path(args.base_dir), Path(args.out))
    print(json.dumps({'checkouts_with_unpushed_work': len(found)}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
