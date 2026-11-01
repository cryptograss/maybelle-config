#!/usr/bin/env python3
"""Fold a retired Mood container's workspace into its Mood's directory, /opt/magenta/moods/<slug>.

Moods own directories, agents own processes (Cryptograss:Moods as Directories, step 5): the containers
that were a Mood's own (uploads-and-embeds, once delivery-kid; docs-tests-qa) are retired, and what was
in their workspace -- the clones, their worktrees, any uncommitted edits and branches on no remote --
moves, whole, to where every container sees it: /moods/<slug>. A move on one disk, so nothing is copied.

Inside the containers the clones were at /home/magent/workspace/<repo>; now they're at /moods/<slug>/<repo>.
A worktree records both ends by absolute path (its .git file, and its gitdir file in the main checkout),
so each one under the moved checkouts is rewritten to the new path, as `git worktree repair` would from
inside a container. Then the group moods is given what it needs: every agent works in these.

Run by the playbook after retired containers are down. Once per Mood: a .folded-from file says it's
done, and what came from where. A name already in the Mood's directory stays where it was, and is said.

Prints one JSON line: {"folded": {slug: [names]}, "left": {slug: [names already there]}}.

    fold_mood_homes.py --base-dir /opt/magenta --folds '[{"mood": "docs-tests-qa", "from": ["docs-tests-qa"]}]'
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

SLUG = re.compile(r'[a-z0-9][a-z0-9_-]*')
INSIDE = '/home/magent/workspace/'  # where a container's clones were, as it saw them
MARK = '.folded-from'


def source_of(base_dir, names):
    """The first of a fold's containers whose home has a workspace: a container may go by an older name."""
    for name in names:
        workspace = base_dir / name / 'home' / 'workspace'
        if SLUG.fullmatch(name) and workspace.is_dir():
            return workspace
    return None


def rewrite(path, old, new):
    try:
        text = path.read_text()
    except OSError:
        return False
    if old not in text:
        return False
    path.write_text(text.replace(old, new))
    return True


def repoint_worktrees(mood_dir, slug):
    """Each moved checkout's worktrees, found by their gitdir files, pointed at their new paths both ways."""
    new = f'/moods/{slug}/'
    host = str(mood_dir) + '/'
    for gitdir in sorted(mood_dir.glob('*/.git/worktrees/*/gitdir')):
        rewrite(gitdir, INSIDE, new)
        # The worktree's own .git file, at the path gitdir now names: /moods/<slug>/... is mood_dir/... here.
        target = gitdir.read_text().strip()
        if target.startswith(new):
            rewrite(Path(host + target[len(new):]), INSIDE, new)


def give_to_group(paths, group='moods'):
    """What moved belongs to the group that works in it, writable by it, and what's made in it too."""
    for path in paths:
        subprocess.run(['chgrp', '-R', group, str(path)], check=False)
        subprocess.run(['setfacl', '-R', '-m', f'g:{group}:rwX', str(path)], check=False)
        subprocess.run(['find', str(path), '-type', 'd', '-exec', 'setfacl', '-m',
                        f'd:u::rwX,d:g::rwX,d:g:{group}:rwX,d:o::rX', '{}', '+'], check=False)


def fold(base_dir, slug, names, give=give_to_group):
    """(names moved, names left because the Mood already had them), or None if done before or nothing there."""
    if not SLUG.fullmatch(slug):
        raise ValueError(f'not a Mood slug: {slug!r}')
    mood_dir = base_dir / 'moods' / slug
    if (mood_dir / MARK).exists():
        return None
    workspace = source_of(base_dir, names)
    if workspace is None:
        return None
    mood_dir.mkdir(parents=True, exist_ok=True)
    moved, left = [], []
    for entry in sorted(workspace.iterdir()):
        if (mood_dir / entry.name).exists():
            left.append(entry.name)
            continue
        shutil.move(str(entry), str(mood_dir / entry.name))
        moved.append(entry.name)
    repoint_worktrees(mood_dir, slug)
    give([mood_dir / name for name in moved])
    (mood_dir / MARK).write_text(json.dumps({'from': str(workspace), 'moved': moved, 'left': left}, indent=1) + '\n')
    return moved, left


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--base-dir', default='/opt/magenta')
    parser.add_argument('--folds', required=True, help='JSON: [{"mood": slug, "from": [container, ...]}]')
    args = parser.parse_args(argv)
    folded, left = {}, {}
    for item in json.loads(args.folds):
        done = fold(Path(args.base_dir), str(item['mood']), [str(n) for n in item.get('from') or [item['mood']]])
        if done:
            folded[item['mood']], left[item['mood']] = done
    print(json.dumps({'folded': folded, 'left': {k: v for k, v in left.items() if v}}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
