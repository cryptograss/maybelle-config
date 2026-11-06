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

It only reads the checkouts: `git bundle create` and `git diff` write nothing
into them. maybelle rsyncs <out> (maybelle.yml: hunter VCS backup). Each run
replaces <out> whole, so a branch pushed since drops out of it.

    vcs-unpushed.py [--base-dir /opt/magenta] [--out /opt/magenta/vcs-backup]
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SKIP_DIRS = {'node_modules', '.venv', 'venv', '.cache', '.npm', '.local', 'vendor', '__pycache__'}
MAX_DEPTH = 4  # under a home's workspace/: workspace/<repo>, and a little deeper


def git(repo, *args, out=None):
    return subprocess.run(['git', '-c', 'safe.directory=*', '-C', str(repo), *args],
                          capture_output=out is None, stdout=out, text=out is None, timeout=300)


def homes(base):
    """(container, home name, path, where its checkouts are) for each home: a container's own and
    its agents', whose checkouts are in workspace/; and each Mood's directory, whose are in it."""
    for container in sorted(p for p in base.iterdir() if p.is_dir()):
        if (container / 'home').is_dir():
            yield container.name, 'magent', container / 'home', container / 'home' / 'workspace'
        agents = container / 'agents'
        if agents.is_dir():
            for agent in sorted(p for p in agents.iterdir() if p.is_dir()):
                yield container.name, agent.name, agent, agent / 'workspace'
    moods = base / 'moods'
    if moods.is_dir():
        for mood in sorted(p for p in moods.iterdir() if p.is_dir()):
            yield 'moods', mood.name, mood, mood


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


def unpushed(repo):
    """Branch names whose commits are on no remote."""
    got = git(repo, 'for-each-ref', '--format=%(refname:short)', 'refs/heads')
    if got.returncode != 0:
        return []
    found = []
    for branch in got.stdout.split():
        ahead = git(repo, 'rev-list', '--count', branch, '--not', '--remotes')
        if ahead.returncode == 0 and ahead.stdout.strip() not in ('', '0'):
            found.append(branch)
    return found


def worktrees(repo):
    got = git(repo, 'worktree', 'list', '--porcelain')
    if got.returncode != 0:
        return [repo]
    return [Path(line[len('worktree '):]) for line in got.stdout.splitlines() if line.startswith('worktree ')]


def keep(repo, dest):
    """Write what only this checkout holds into dest; a summary, or None if nothing."""
    summary = {}
    branches = unpushed(repo)
    if branches:
        dest.mkdir(parents=True, exist_ok=True)
        made = git(repo, 'bundle', 'create', str(dest / 'branches.bundle'), *branches, '--not', '--remotes')
        summary['branches'] = branches if made.returncode == 0 else {'error': made.stderr.strip()[-300:]}
    for tree in worktrees(repo):
        if not tree.is_dir():
            continue
        dirty = git(tree, 'diff', 'HEAD', '--quiet')
        if dirty.returncode == 1:
            dest.mkdir(parents=True, exist_ok=True)
            # Named by where it is beside its checkout: a Mood's threads each have a worktree
            # of the same repo (threads/<thread>/<repo>), which tree.name alone would mix up.
            name = 'main' if tree == repo else os.path.relpath(tree, repo.parent).replace('/', '__')
            with open(dest / f'{name}.patch', 'w') as f:
                git(tree, 'diff', 'HEAD', '--binary', out=f)
            summary.setdefault('dirty', []).append(str(tree))
    return summary or None


def run(base, out):
    part = out.with_name(out.name + '.part')
    if part.exists():
        shutil.rmtree(part)
    part.mkdir(parents=True)
    found = {}
    for container, home_name, home, root in homes(base):
        if home.resolve() == out.resolve() or container in (out.name, part.name):
            continue
        for repo in checkouts(root):
            rel = repo.relative_to(home)
            key = f'{container}/{home_name}/{rel}'
            try:
                summary = keep(repo, part / container / home_name / str(rel).replace('/', '__'))
            except (subprocess.TimeoutExpired, OSError) as e:
                summary = {'error': str(e)[:300]}
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
