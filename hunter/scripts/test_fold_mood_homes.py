"""fold_mood_homes.py against real git checkouts with worktrees, in a temporary /opt/magenta.

    python3 -m pytest hunter/scripts/test_fold_mood_homes.py
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import fold_mood_homes as fmh  # noqa: E402


def git(cwd, *args):
    return subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@t', *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout


def as_container_saw_it(workspace):
    """Rewrite a checkout's worktree paths as they were inside the container: /home/magent/workspace/..."""
    for path in list(workspace.glob('*/.git/worktrees/*/gitdir')) + list(workspace.glob('*/.claude/worktrees/*/.git')):
        path.write_text(path.read_text().replace(str(workspace) + '/', fmh.INSIDE))


@pytest.fixture
def base(tmp_path):
    workspace = tmp_path / 'opt' / 'delivery-kid' / 'home' / 'workspace'
    repo = workspace / 'pickipedia'
    repo.mkdir(parents=True)
    git(repo, 'init', '-b', 'main')
    (repo / 'a.txt').write_text('one\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-m', 'one')
    git(repo, 'worktree', 'add', '-b', 'zine', str(repo / '.claude' / 'worktrees' / 'zine'))
    (repo / '.claude' / 'worktrees' / 'zine' / 'a.txt').write_text('unsaved\n')
    (workspace / 'games').mkdir()
    as_container_saw_it(workspace)
    return tmp_path / 'opt'


def no_group(paths):
    pass


def test_the_workspace_moves_whole_and_its_worktrees_point_at_moods(base):
    moved, left = fmh.fold(base, 'uploads-and-embeds', ['uploads-and-embeds', 'delivery-kid'], give=no_group)
    assert (moved, left) == (['games', 'pickipedia'], [])
    mood = base / 'moods' / 'uploads-and-embeds'
    assert not list((base / 'delivery-kid/home/workspace').iterdir())
    gitdir = (mood / 'pickipedia/.git/worktrees/zine/gitdir').read_text().strip()
    assert gitdir == '/moods/uploads-and-embeds/pickipedia/.claude/worktrees/zine/.git'
    dotgit = (mood / 'pickipedia/.claude/worktrees/zine/.git').read_text().strip()
    assert dotgit == 'gitdir: /moods/uploads-and-embeds/pickipedia/.git/worktrees/zine'
    # As a container sees it, git agrees: put the host's path where /moods/<slug> is, and ask.
    for path in (mood / 'pickipedia/.git/worktrees/zine/gitdir', mood / 'pickipedia/.claude/worktrees/zine/.git'):
        path.write_text(path.read_text().replace('/moods/uploads-and-embeds/', str(mood) + '/'))
    tree = mood / 'pickipedia/.claude/worktrees/zine'
    assert git(tree, 'status', '--porcelain').strip() == 'M a.txt'  # the unsaved edit came along
    assert str(tree) in git(mood / 'pickipedia', 'worktree', 'list')


def test_once_only_and_says_what_came_from_where(base):
    fmh.fold(base, 'uploads-and-embeds', ['uploads-and-embeds', 'delivery-kid'], give=no_group)
    mark = json.loads((base / 'moods/uploads-and-embeds/.folded-from').read_text())
    assert mark['from'].endswith('delivery-kid/home/workspace') and mark['moved'] == ['games', 'pickipedia']
    assert fmh.fold(base, 'uploads-and-embeds', ['delivery-kid'], give=no_group) is None


def test_what_the_mood_already_has_stays_where_it_was(base):
    (base / 'moods' / 'uploads-and-embeds' / 'games').mkdir(parents=True)
    moved, left = fmh.fold(base, 'uploads-and-embeds', ['delivery-kid'], give=no_group)
    assert (moved, left) == (['pickipedia'], ['games'])
    assert (base / 'delivery-kid/home/workspace/games').is_dir()


def test_nothing_to_fold_is_nothing(base):
    assert fmh.fold(base, 'docs-tests-qa', ['docs-tests-qa'], give=no_group) is None
    assert not (base / 'moods' / 'docs-tests-qa').exists()


def test_only_a_moods_slug(base):
    with pytest.raises(ValueError):
        fmh.fold(base, '../etc', ['delivery-kid'], give=no_group)
