"""githouse against real repositories: a stand-in GitHub, githouse's clone of it, and someone's working copy."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import githouse  # noqa: E402


def run(*args, cwd=None, env=None):
    done = subprocess.run(args, cwd=cwd, capture_output=True, text=True, env={**os.environ, **(env or {})})
    return done.returncode, done.stdout + done.stderr


class GithouseTest(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / 'srv'
        self.root.mkdir()
        githouse.ROOT = self.root
        self.env = {'GITHOUSE_ROOT': str(self.root), 'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t',
                    'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@t', 'GIT_CONFIG_GLOBAL': '/dev/null'}
        os.environ.update(self.env)
        # GitHub, played by a bare repository: main and production, and a branch of someone's there too.
        self.github = self.tmp / 'github' / 'cryptograss' / 'magenta.git'
        run('git', 'init', '--quiet', '--bare', '-b', 'main', str(self.github))
        self.upstream = self.clone(self.github, 'upstream')
        self.commit(self.upstream, 'one')
        run('git', 'push', '--quiet', 'origin', 'HEAD:main', 'HEAD:production', cwd=self.upstream)
        githouse.init(['cryptograss/magenta'], url=str(self.tmp / 'github') + '/{}.git')
        self.repo = self.root / 'magenta.git'
        githouse.fetch(self.repo)
        self.work = self.clone(self.repo, 'work')

    def clone(self, source, name):
        path = self.tmp / name
        run('git', 'clone', '--quiet', str(source), str(path))
        return path

    def commit(self, path, words):
        (path / f'{words}.txt').write_text(words)
        run('git', 'add', '.', cwd=path)
        run('git', 'commit', '--quiet', '-m', words, cwd=path)

    def push(self, user, *refspecs):
        env = {'GITHOUSE_USER': user} if user else {}
        return run('git', 'push', 'origin', *refspecs, cwd=self.work, env=env)

    def branches(self):
        return json.loads((self.root / 'branches.json').read_text())['magenta']

    def merge_on_github(self, branch):
        """Someone merges a pushed branch on GitHub, with a merge commit, as our repositories do."""
        run('git', 'fetch', '--quiet', str(self.repo), f'refs/heads/{branch}:refs/remotes/h/{branch}', cwd=self.upstream)
        run('git', 'merge', '--quiet', '--no-ff', '-m', f'Merge {branch}', f'h/{branch}', cwd=self.upstream)
        run('git', 'push', '--quiet', 'origin', 'HEAD:main', cwd=self.upstream)

    # --- GitHub's branches -----------------------------------------------------------

    def test_githubs_branches_are_fetched(self):
        self.assertEqual(set(self.branches()['live']), {'main', 'production'})
        self.commit(self.upstream, 'two')
        run('git', 'push', '--quiet', 'origin', 'HEAD:main', cwd=self.upstream)
        self.assertTrue(githouse.fetch(self.repo))
        self.assertFalse(githouse.fetch(self.repo))  # nothing new: nothing fetched

    def test_the_fetch_leaves_pushed_branches_alone(self):
        self.commit(self.work, 'mine')
        self.assertEqual(self.push('re', 'HEAD:re/foo')[0], 0)
        mine = githouse.refs(self.repo, 'refs/heads/')['re/foo']
        # GitHub has an older re/foo (a forward not landed yet): the fetch doesn't take it.
        run('git', 'push', '--quiet', 'origin', 'HEAD:re/foo', cwd=self.upstream)
        self.commit(self.upstream, 'two')
        run('git', 'push', '--quiet', 'origin', 'HEAD:main', cwd=self.upstream)
        githouse.fetch(self.repo)
        self.assertEqual(githouse.refs(self.repo, 'refs/heads/')['re/foo'], mine)

    def test_a_branch_deleted_on_github_goes(self):
        run('git', 'push', '--quiet', 'origin', 'HEAD:release/1', cwd=self.upstream)
        githouse.fetch(self.repo)
        self.assertIn('release/1', self.branches()['live'])
        run('git', 'push', '--quiet', 'origin', ':release/1', cwd=self.upstream)
        githouse.fetch(self.repo)
        githouse.publish()
        self.assertNotIn('release/1', self.branches()['live'])

    # --- who pushes what -------------------------------------------------------------

    def test_each_pushes_their_own_branches_only(self):
        self.commit(self.work, 'mine')
        self.assertEqual(self.push('re', 'HEAD:re/foo')[0], 0)
        code, said = self.push('re', 'HEAD:magent/foo')
        self.assertNotEqual(code, 0)
        self.assertIn('re pushes re/<name> branches only', said)
        self.assertNotEqual(self.push('re', 'HEAD:main')[0], 0)
        self.assertNotEqual(self.push('re', 'HEAD:re')[0], 0)
        self.assertNotEqual(self.push('', 'HEAD:re/bar')[0], 0)  # not through githouse auth
        run('git', 'tag', 'v1', cwd=self.work)
        code, said = self.push('re', 'v1')
        self.assertIn('no tags', said)
        self.assertEqual(set(self.branches()['live']), {'main', 'production', 're/foo'})

    def test_force_pushing_your_own_branch(self):
        self.commit(self.work, 'mine')
        self.push('re', 'HEAD:re/foo')
        run('git', 'commit', '--quiet', '--amend', '-m', 'mine, again', cwd=self.work)
        self.assertEqual(self.push('re', '+HEAD:re/foo')[0], 0)

    # --- a name is used once ---------------------------------------------------------

    def test_merged_moves_to_refs_merged_and_the_name_is_taken(self):
        self.commit(self.work, 'mine')
        self.push('re', 'HEAD:re/foo')
        self.merge_on_github('re/foo')
        githouse.fetch(self.repo)
        githouse.publish()
        self.assertNotIn('re/foo', self.branches()['live'])
        self.assertIn('re/foo', self.branches()['merged'])
        code, said = self.push('re', 'HEAD:re/foo')
        self.assertNotEqual(code, 0)
        self.assertIn('used already (merged); try re/foo-2', said)
        self.assertEqual(self.push('re', 'HEAD:re/foo-2')[0], 0)

    def test_a_branch_cut_from_main_with_nothing_on_it_isnt_merged(self):
        self.push('re', 'HEAD:re/fresh')
        self.commit(self.upstream, 'two')
        run('git', 'push', '--quiet', 'origin', 'HEAD:main', cwd=self.upstream)
        githouse.fetch(self.repo)
        self.assertIn('re/fresh', githouse.refs(self.repo, 'refs/heads/'))

    def test_deleted_is_closed_and_the_name_is_taken(self):
        self.commit(self.work, 'mine')
        self.push('re', 'HEAD:re/bar')
        self.assertEqual(self.push('re', ':re/bar')[0], 0)
        self.assertIn('re/bar', self.branches()['closed'])
        code, said = self.push('re', 'HEAD:re/bar')
        self.assertIn('used already (closed); try re/bar-2', said)

    # --- on to GitHub ---------------------------------------------------------------

    def test_a_push_goes_on_to_github_when_forwarding(self):
        githouse.git(self.repo, 'config', 'githouse.forward', 'true')
        self.commit(self.work, 'mine')
        self.push('re', 'HEAD:re/foo')
        code, listed = run('git', 'ls-remote', str(self.github), 'refs/heads/re/foo')
        self.assertIn('refs/heads/re/foo', listed)

    def test_a_failed_forward_is_logged_and_the_branch_stays(self):
        githouse.git(self.repo, 'config', 'githouse.forward', 'true')
        githouse.git(self.repo, 'remote', 'set-url', 'github', str(self.tmp / 'nowhere.git'))
        self.commit(self.work, 'mine')
        self.assertEqual(self.push('re', 'HEAD:re/foo')[0], 0)
        self.assertIn('re/foo', githouse.refs(self.repo, 'refs/heads/'))
        self.assertIn("forwarding re's push to GitHub failed", (self.root / 'githouse.log').read_text())

    def test_branches_json_served_and_nothing_else(self):
        import urllib.error
        import urllib.request
        server = githouse.serve(port=0)
        self.addCleanup(server.shutdown)
        base = f'http://127.0.0.1:{server.server_address[1]}'
        listed = json.loads(urllib.request.urlopen(f'{base}/branches.json').read())
        self.assertIn('main', listed['magenta']['live'])
        for path in ('/magenta.git/config', '/', '/../branches.json.tmp'):
            with self.assertRaises(urllib.error.HTTPError, msg=path):
                urllib.request.urlopen(base + path)

    # --- the forced command ---------------------------------------------------------

    def test_auth(self):
        self.assertEqual(githouse.auth('re', "git-receive-pack 'magenta.git'"), ('git-receive-pack', self.repo))
        self.assertEqual(githouse.auth('re', "git-upload-pack '/magenta'"), ('git-upload-pack', self.repo))
        self.assertEqual(githouse.auth('re', "git upload-pack 'magenta.git'"), ('git-upload-pack', self.repo))
        for command in ("git-receive-pack '../etc'", "git-upload-pack 'nothing.git'", 'sh -c id',
                        "git-upload-pack 'magenta.git'; rm -rf /", ''):
            with self.assertRaises(PermissionError, msg=command):
                githouse.auth('re', command)
        with self.assertRaises(PermissionError):
            githouse.auth('Re Bad', "git-upload-pack 'magenta.git'")


if __name__ == '__main__':
    unittest.main()
