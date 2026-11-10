"""githouse against real repositories: a stand-in GitHub, githouse's clone of it, and someone's working copy."""

import hashlib
import http.server
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.parse
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import githouse  # noqa: E402


def run(*args, cwd=None, env=None):
    done = subprocess.run(args, cwd=cwd, capture_output=True, text=True, env={**os.environ, **(env or {})})
    return done.returncode, done.stdout + done.stderr


class Repos(unittest.TestCase):
    """A stand-in GitHub, githouse's clone of it, and a working copy: no tests of its own."""

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

    def merge_on_github(self, branch, adopt=True):
        """Someone merges a pushed branch on GitHub, with a merge commit, and root adopts it (since the flip,
        githouse takes nothing from GitHub's deploy branches on its own)."""
        run('git', 'fetch', '--quiet', str(self.repo), f'refs/heads/{branch}:refs/remotes/h/{branch}', cwd=self.upstream)
        run('git', 'merge', '--quiet', '--no-ff', '-m', f'Merge {branch}', f'h/{branch}', cwd=self.upstream)
        run('git', 'push', '--quiet', 'origin', 'HEAD:main', cwd=self.upstream)
        if adopt:
            githouse.adopt(self.repo, 'main')

    def on_github(self, words, branch='main'):
        """Someone commits straight to a deploy branch on GitHub, around githouse."""
        self.commit(self.upstream, words)
        run('git', 'push', '--quiet', 'origin', f'HEAD:{branch}', cwd=self.upstream)
        return run('git', '--git-dir', str(self.github), 'rev-parse', branch)[1].strip()

    def heads(self):
        return githouse.refs(self.repo, 'refs/heads/')

    def differs(self):
        return json.loads((self.root / 'github.json').read_text()).get('magenta', {})


class GithouseTest(Repos):

    # --- GitHub's branches -----------------------------------------------------------

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

    def test_a_new_githouse_takes_githubs_deploy_branches_once(self):
        self.assertEqual(set(self.branches()['live']), {'main', 'production'})
        self.assertEqual(self.differs(), {})
        self.assertFalse(githouse.fetch(self.repo))  # nothing new: nothing fetched, nothing changed

    def test_a_push_to_githubs_deploy_branch_is_not_taken(self):
        # The flip (Plan, section 6): main moves only by a merge here. A commit pushed to GitHub's main,
        # around githouse (a token that can push, a GitHub button), never reaches githouse's, so no deploy.
        ours = self.heads()['main']
        theirs = self.on_github('around githouse')
        githouse.fetch(self.repo)
        self.assertEqual(self.heads()['main'], ours)
        self.assertEqual(self.differs()['main']['state'], 'ahead')
        self.assertEqual(self.differs()['main']['github'], theirs)
        githouse.fetch(self.repo)  # said once in the log, not every minute
        self.assertEqual((self.root / 'githouse.log').read_text().count('main: GitHub ahead'), 1)
        self.assertEqual(self.heads()['main'], ours)

    def test_root_adopts_a_fast_forward_and_only_that(self):
        theirs = self.on_github('a bump, merged on GitHub')
        githouse.fetch(self.repo)
        taken = githouse.adopt(self.repo, 'main')
        self.assertEqual(len(taken), 1)
        self.assertIn('a bump', taken[0])
        self.assertEqual(self.heads()['main'], theirs)
        self.assertEqual(self.differs(), {})
        self.assertEqual(githouse.adopt(self.repo, 'main'), [])
        with self.assertRaises(githouse.Refused):
            githouse.adopt(self.repo, 're/foo')  # a deploy branch, or nothing

    def merged_here(self):
        """githouse's main moved by a merge here (of re/foo), GitHub's not told yet."""
        self.commit(self.work, 'mine')
        self.assertEqual(self.push('re', 'HEAD:re/foo')[0], 0)
        ours = self.heads()['main']
        merged = githouse.git(self.repo, 'commit-tree', 're/foo^{tree}', '-p', ours, '-p', 're/foo',
                              '-m', 'a merge here').strip()
        githouse.git(self.repo, 'update-ref', 'refs/heads/main', merged, ours)
        return merged

    def test_apart_is_said_and_never_adopted(self):
        merged = self.merged_here()
        self.on_github('theirs')
        githouse.fetch(self.repo)
        self.assertEqual(self.heads()['main'], merged)
        self.assertEqual(self.differs()['main']['state'], 'apart')
        with self.assertRaises(githouse.Refused):
            githouse.adopt(self.repo, 'main')
        self.assertEqual(self.heads()['main'], merged)

    def test_github_behind_is_sent_ours_never_forced(self):
        merged = self.merged_here()  # as when the merge's push to GitHub failed
        githouse.fetch(self.repo)
        self.assertEqual(run('git', '--git-dir', str(self.github), 'rev-parse', 'main')[1].strip(), merged)
        self.assertEqual(self.differs(), {})

    def test_a_deploy_branch_deleted_on_github_is_sent_back(self):
        production = self.heads()['production']
        run('git', 'push', '--quiet', 'origin', ':production', cwd=self.upstream)
        githouse.fetch(self.repo)
        self.assertEqual(self.heads()['production'], production)
        self.assertEqual(run('git', '--git-dir', str(self.github), 'rev-parse', 'production')[1].strip(), production)

    def test_githubs_view_is_never_a_live_branch(self):
        self.on_github('around githouse')
        githouse.fetch(self.repo)
        self.assertIn('main', githouse.refs(self.repo, 'refs/github/'))
        self.assertEqual(set(self.branches()['live']), {'main', 'production'})

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

    def test_a_push_landing_while_retiring_keeps_the_branch_live(self):
        self.commit(self.work, 'mine')
        self.push('re', 'HEAD:re/foo')
        merged_tip = githouse.refs(self.repo, 'refs/heads/')['re/foo']
        self.merge_on_github('re/foo', adopt=False)  # main moved, retire() not run yet
        githouse.git(self.repo, 'fetch', '--quiet', 'github', '+refs/heads/main:refs/heads/main')
        self.commit(self.work, 'more')
        self.push('re', 'HEAD:re/foo')  # lands after retire() has read the branches
        real = githouse.refs
        stale = lambda repo, prefix: {**real(repo, prefix), **({'re/foo': merged_tip} if prefix == 'refs/heads/' else {})}
        with unittest.mock.patch.object(githouse, 'refs', stale):
            self.assertEqual(githouse.retire(self.repo), [])
        self.assertNotEqual(githouse.refs(self.repo, 'refs/heads/').get('re/foo'), merged_tip)
        self.assertIn('re/foo', githouse.refs(self.repo, 'refs/heads/'))
        self.assertIsNone(githouse.retired(self.repo, 're/foo'))

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

    # --- the relayed keys -------------------------------------------------------------

    def test_authorized_keys_from_the_relay_as_pushers_json_allows(self):
        keys = self.tmp / 'keys'
        pub = 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGQ6Z3lmZmJhcmJhemJhemJhemJhemJhemJhemJhemJhemJh re@emergent-re'
        for container, name, text in (('emergent-re', 're', pub), ('emergent-re', 'magent', pub),  # re's box vouching for magent: not allowed
                                      ('emergent-blu', 'blu', pub + '\n' + pub),                 # two keys in one file: not one key
                                      ('hunter', 'mirror', pub), ('stranger', 'justin', pub)):    # an unlisted container: nothing
            (keys / container).mkdir(parents=True, exist_ok=True)
            (keys / container / f'{name}.pub').write_text(text + '\n')
        pushers = {'emergent-re': ['re'], 'emergent-blu': ['blu'], 'emergent-magent': ['magent'],
                   'hunter': {'names': ['mirror'], 'fetch_only': True}}
        lines = githouse.authorized_keys(keys, pushers).splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith('command="') and ' auth re",restrict ssh-ed25519 ' in lines[0])
        self.assertIn(' auth --fetch-only mirror",restrict ', lines[1])
        self.assertTrue(all(',restrict ' in line and 'no-pty' not in line for line in lines))
        self.assertIn('emergent-blu/blu.pub is not one public key', (self.root / 'githouse.log').read_text())

    def test_write_authorized_keys_replaces_the_whole_file(self):
        etc = self.tmp / 'etc'
        (etc / 'keys' / 'emergent-re').mkdir(parents=True)
        (etc / 'pushers.json').write_text(json.dumps({'emergent-re': ['re']}))
        githouse.ETC = etc
        (etc / 'keys' / 'emergent-re' / 're.pub').write_text('ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGQ6Z3lmZmJhcmJhemJhemJhemJhemJhemJhemJhemJhemJh\n')
        githouse.write_authorized_keys()
        self.assertIn('auth re"', (self.root / '.authorized_keys').read_text())
        (etc / 'keys' / 'emergent-re' / 're.pub').unlink()  # the relay no longer has it: neither does sshd
        githouse.write_authorized_keys()
        self.assertEqual((self.root / '.authorized_keys').read_text(), '')

    # --- hunter's mirror --------------------------------------------------------------

    def test_the_mirror_has_everything_maybelle_has(self):
        self.commit(self.work, 'mine')
        self.push('re', 'HEAD:re/foo')
        self.push('re', 'HEAD:re/gone')
        self.push('re', ':re/gone')
        mirror_root = self.tmp / 'mirror'
        mirror_root.mkdir()
        maybelle = self.root
        githouse.ROOT = mirror_root
        githouse.mirror_init(['cryptograss/magenta'], str(maybelle))
        githouse.mirror(mirror_root / 'magenta.git')
        githouse.ROOT = maybelle
        copy = mirror_root / 'magenta.git'
        self.assertEqual(githouse.refs(copy, 'refs/heads/'), githouse.refs(self.repo, 'refs/heads/'))
        self.assertEqual(githouse.refs(copy, 'refs/closed/'), githouse.refs(self.repo, 'refs/closed/'))
        self.assertEqual(githouse.git(copy, 'symbolic-ref', 'HEAD').strip(), 'refs/heads/main')
        self.assertIn('re/foo', json.loads((mirror_root / 'branches.json').read_text())['magenta']['live'])
        # Deleted on maybelle: gone from the mirror at the next fetch.
        self.push('re', ':re/foo')
        githouse.ROOT = mirror_root
        githouse.mirror(copy)
        githouse.ROOT = maybelle
        self.assertNotIn('re/foo', githouse.refs(copy, 'refs/heads/'))

    def test_the_hooks_carry_the_deploy_key(self):
        # post-receive runs under sshd's forced command, without entrypoint.sh's exports: main() sets
        # GIT_SSH_COMMAND from the deploy key before dispatching, so the forward to GitHub has it.
        (self.root / '.deploy_key').write_text('not really a key\n')
        env = {k: v for k, v in os.environ.items() if k != 'GIT_SSH_COMMAND'}
        env.update(GIT_DIR=str(self.repo), GITHOUSE_USER='re')
        with unittest.mock.patch.dict(os.environ, env, clear=True), unittest.mock.patch('sys.stdin', io.StringIO('')):
            self.assertEqual(githouse.main(['githouse', 'post-receive']), 0)
            self.assertIn(str(self.root / '.deploy_key'), os.environ.get('GIT_SSH_COMMAND', ''))

    # --- the forced command ---------------------------------------------------------

    def test_auth_fetch_only(self):
        self.assertEqual(githouse.auth('mirror', "git-upload-pack 'magenta.git'", fetch_only=True), ('git-upload-pack', self.repo))
        with self.assertRaises(PermissionError):
            githouse.auth('mirror', "git-receive-pack 'magenta.git'", fetch_only=True)

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



class BranchTestTest(Repos):
    """After a push reaches GitHub, Jenkins is asked to test exactly that commit (Plan, section 11)."""

    def setUp(self):
        super().setUp()
        self.asked = []
        test = self

        class Jenkins(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                assert self.path == '/crumbIssuer/api/json' and self.headers['Authorization'].startswith('Basic ')
                body = json.dumps({'crumbRequestField': 'Jenkins-Crumb', 'crumb': 'c1'}).encode()
                self.send_response(200)
                self.send_header('Set-Cookie', 'JSESSIONID=s1; Path=/')
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                test.asked.append((self.path, self.headers.get('Jenkins-Crumb'), self.headers.get('Cookie'),
                                   self.headers.get('Authorization')))
                self.send_response(201)
                self.end_headers()

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Jenkins)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.etc = self.tmp / 'etc'
        self.etc.mkdir()
        (self.etc / 'jenkins.json').write_text(json.dumps({'url': f'http://127.0.0.1:{server.server_address[1]}/'}))
        (self.etc / 'jenkins_reporter').write_text('pw\n')
        patcher = unittest.mock.patch.object(githouse, 'ETC', self.etc)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_forwarded_push_is_tested_at_that_commit(self):
        self.commit(self.work, 'the fix')
        head = run('git', 'rev-parse', 'HEAD', cwd=self.work)[1].strip()
        self.assertEqual(self.push('re', 'HEAD:re/foo')[0], 0)  # here, not yet forwarded (forwarding is off in Repos)
        self.asked.clear()
        githouse.after(self.repo, 're', [(githouse.ZERO, head, 'refs/heads/re/foo')], forward=True)
        self.assertEqual(len(self.asked), 1)
        path, crumb, cookie, auth = self.asked[0]
        self.assertTrue(path.startswith('/job/branch-tests/buildWithParameters?'))
        self.assertIn(f'COMMIT={head}', path)
        self.assertIn('REPO=magenta', path)
        self.assertIn('BRANCH=re%2Ffoo', path)
        self.assertEqual((crumb, cookie), ('c1', 'JSESSIONID=s1'))

    def test_a_deletion_or_a_failed_forward_is_not(self):
        main = githouse.refs(self.repo, 'refs/heads/')['main']
        githouse.after(self.repo, 're', [(main, githouse.ZERO, 'refs/heads/re/gone')], forward=True)
        self.assertEqual(self.asked, [])
        # A forward GitHub refuses (a ref githouse doesn't have) asks for nothing either.
        githouse.after(self.repo, 're', [(githouse.ZERO, 'b' * 40, 'refs/heads/re/nowhere')], forward=True)
        self.assertEqual(self.asked, [])

    def test_not_set_up_here_or_jenkins_down(self):
        (self.etc / 'jenkins.json').unlink()
        self.assertFalse(githouse.test_branch('magenta', 're/foo', 'a' * 40))
        (self.etc / 'jenkins.json').write_text(json.dumps({'url': 'http://127.0.0.1:9/'}))
        self.assertFalse(githouse.test_branch('magenta', 're/foo', 'a' * 40))  # logged, never raised
        self.assertIn("Jenkins didn't take the branch test", (self.root / 'githouse.log').read_text())


class MergeTest(Repos):
    """githouse merge: a signed order, checked here, merged --no-ff and pushed to GitHub first (Plan, section 8)."""

    def setUp(self):
        super().setUp()
        self.keys = self.tmp / 'keys'
        self.keys.mkdir()
        for who in ('justin', 'skyler'):
            run('ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C', who, '-f', str(self.keys / who))
        # Only justin may merge: the file ansible writes from the inventory and githouse_mergers.
        self.signers = self.tmp / 'merge_signers'
        pub = (self.keys / 'justin.pub').read_text().strip()
        self.signers.write_text(f'justin namespaces="{githouse.MERGE_NAMESPACE}" {pub}\n')
        self.commit(self.work, 'the fix')
        self.assertEqual(self.push('re', 'HEAD:re/foo')[0], 0)
        self.head = githouse.refs(self.repo, 'refs/heads/')['re/foo']
        self.now = 1_800_000_000
        # githouse's /etc, where root's freeze would be, and memory-lane's brakes, played by a small server:
        # it answers for every name asked about, none banned and no AZ5, unless a test says otherwise.
        self.etc = self.tmp / 'etc'
        self.etc.mkdir()
        patcher = unittest.mock.patch.object(githouse, 'ETC', self.etc)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.banned, self.scram, self.answer, self.asked, self.mail = set(), False, None, [], None
        test = self

        class Brakes(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path.startswith('/api/merge-orders/'):
                    return self.reply(test.mail if test.mail is not None else {'orders': []})
                names = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get('name', [])
                test.asked.append(names)
                self.reply(test.answer if test.answer is not None else
                           {'scram': test.scram, 'banned': {name: name in test.banned for name in names}})

            def reply(self, said):
                body = json.dumps(said).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Brakes)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.brakes = f'http://127.0.0.1:{server.server_address[1]}/api/brakes/'
        self.mailbox = f'http://127.0.0.1:{server.server_address[1]}/api/merge-orders/pending/'
        githouse._tried.clear()

    def order(self, **change):
        fields = {'repo': 'magenta', 'branch': 're/foo', 'commit': self.head, 'into': 'main', 'by': 'justin',
                  'nonce': os.urandom(16).hex(), 'expires': str(self.now + 3600), **change}
        keys = githouse.ORDER_FIELDS + ((githouse.FOR_FIELD,) if githouse.FOR_FIELD in fields else ())
        return githouse.ORDER_HEADER + '\n' + ''.join(f'{k}: {fields[k]}\n' for k in keys)

    def pressed(self, person, **change):
        """An order from the merge button: signed with memory-lane's key, for whoever pressed."""
        if not (self.keys / 'memory-lane').exists():
            run('ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C', 'memory-lane-merge', '-f', str(self.keys / 'memory-lane'))
            with self.signers.open('a') as f:
                f.write(f'memory-lane namespaces="{githouse.MERGE_NAMESPACE}" '
                        f'{(self.keys / "memory-lane.pub").read_text().strip()}\n')
        text = self.order(by='memory-lane', **{'for': person}, **change)
        return text, self.sign(text, key='memory-lane')

    def sign(self, text, key='justin', namespace=None):
        path = Path(tempfile.mkdtemp(dir=self.tmp)) / 'order.txt'  # a .sig already there would be asked about
        path.write_text(text)
        run('ssh-keygen', '-Y', 'sign', '-q', '-f', str(self.keys / key), '-n', namespace or githouse.MERGE_NAMESPACE, str(path))
        return path.with_suffix('.txt.sig').read_text()

    def merge(self, text, signature=None, brakes=None):
        return githouse.merge(text, signature if signature is not None else self.sign(text), now=self.now,
                              signers=self.signers, brakes_url=brakes if brakes is not None else self.brakes)

    def github_main(self):
        return run('git', '--git-dir', str(self.github), 'rev-parse', 'main')[1].strip()

    def test_a_signed_order_merges_here_then_on_github(self):
        before = self.github_main()
        text = self.order()
        result = self.merge(text)
        self.assertEqual(result['outcome'], 'merged', result)
        self.assertEqual(self.github_main(), result['merge'])
        parents = run('git', '--git-dir', str(self.github), 'rev-list', '--parents', '-n', '1', 'main')[1].split()
        self.assertEqual(parents[1:], [before, self.head])  # --no-ff: the target first, then exactly the commit signed for
        message = run('git', '--git-dir', str(self.github), 'log', '-1', '--format=%B%n%an', 'main')[1]
        self.assertIn('Merge magenta:re/foo', message)
        self.assertIn('Signed by justin', message)
        self.assertEqual(githouse.refs(self.repo, 'refs/heads/')['main'], result['merge'])
        self.assertIn('re/foo', githouse.refs(self.repo, 'refs/merged/'))  # the name is used, as any merge
        nonce = githouse.parse_order(text)['nonce']
        kept = json.loads((self.root / '.merges' / f'{nonce}.json').read_text())
        self.assertEqual((kept['outcome'], kept['by'], kept['order']), ('merged', 'justin', text))

    def test_a_merger_with_an_rsa_key(self):
        # skyler's key in hunter's inventory is ssh-rsa; githouse_mergers gained skyler on 7 Nov.
        run('ssh-keygen', '-q', '-t', 'rsa', '-b', '3072', '-N', '', '-C', 'skyler', '-f', str(self.keys / 'skyler-rsa'))
        pub = (self.keys / 'skyler-rsa.pub').read_text().strip()
        with self.signers.open('a') as f:
            f.write(f'skyler namespaces="{githouse.MERGE_NAMESPACE}" {pub}\n')
        text = self.order(by='skyler')
        result = self.merge(text, self.sign(text, key='skyler-rsa'))
        self.assertEqual(result['outcome'], 'merged', result)
        self.assertEqual(self.github_main(), result['merge'])

    def test_an_order_is_good_once(self):
        text = self.order()
        signature = self.sign(text)
        self.assertEqual(self.merge(text, signature)['outcome'], 'merged')
        again = self.merge(text, signature)
        self.assertEqual((again['outcome'], again['why']), ('refused', 'that order was used already'))

    def test_a_refused_order_is_spent_too(self):
        text = self.order(into='production', branch='re/nothing')
        signature = self.sign(text)
        self.assertEqual(self.merge(text, signature)['outcome'], 'refused')
        self.assertEqual(self.merge(text, signature)['why'], 'that order was used already')

    def test_only_a_merger_s_signature_in_the_merge_namespace(self):
        as_justin, as_skyler = self.order(), self.order(by='skyler')
        for why, text, signature in (
                ('a sign-in signature (namespace magenta-moods)', as_justin, self.sign(as_justin, namespace='magenta-moods')),
                ('skyler, who is not in merge_signers', as_skyler, self.sign(as_skyler, key='skyler')),
                ("justin's key, on an order that says by: skyler", as_skyler, self.sign(as_skyler, key='justin')),
                ('no signature', as_justin, 'nonsense')):
            with self.subTest(why):
                result = self.merge(text, signature)
                self.assertEqual(result['outcome'], 'refused')
                self.assertIn('not signed by', result['why'])
        self.assertEqual(self.github_main(), run('git', '--git-dir', str(self.repo), 'rev-parse', 'main')[1].strip())

    def test_an_unsigned_order_can_t_take_a_signed_one_s_record(self):
        text = self.order()
        nonce = githouse.parse_order(text)['nonce']
        self.assertEqual(self.merge(text, 'nonsense')['outcome'], 'refused')
        self.assertFalse((self.root / '.merges' / f'{nonce}.json').exists())
        self.assertEqual(self.merge(text)['outcome'], 'merged')
        self.assertEqual(json.loads((self.root / '.merges' / f'{nonce}.json').read_text())['outcome'], 'merged')

    def test_a_changed_word_breaks_the_signature(self):
        text = self.order()
        signature = self.sign(text)
        result = self.merge(text.replace('into: main', 'into: production'), signature)
        self.assertEqual(result['outcome'], 'refused')

    def test_expired_or_too_long(self):
        for expires, why in ((self.now - 1, 'expired'), (self.now + githouse.ORDER_LIFE_MAX + 60, 'at most')):
            result = self.merge(self.order(expires=str(expires)))
            self.assertIn(why, result['why'])

    def test_a_branch_that_moved_after_signing(self):
        text = self.order()
        self.commit(self.work, 'one more')
        self.assertEqual(self.push('re', 'HEAD:re/foo')[0], 0)
        result = self.merge(text)
        self.assertEqual(result['outcome'], 'refused')
        self.assertIn('has moved on githouse', result['why'])

    def test_a_conflict_merges_nothing(self):
        (self.upstream / 'the fix.txt').write_text('something else')
        run('git', 'add', '.', cwd=self.upstream)
        run('git', 'commit', '--quiet', '-m', 'meanwhile', cwd=self.upstream)
        run('git', 'push', '--quiet', 'origin', 'HEAD:main', cwd=self.upstream)
        githouse.adopt(self.repo, 'main')
        before = self.github_main()
        result = self.merge(self.order())
        self.assertIn('conflicts with main', result['why'])
        self.assertEqual(self.github_main(), before)
        self.assertEqual(self.heads()['main'], before)

    def test_the_merge_is_onto_githouses_main_not_githubs(self):
        # Something pushed to GitHub's main around githouse isn't in the merge, and isn't deployed.
        ours = self.heads()['main']
        self.on_github('around githouse')
        result = self.merge(self.order())
        self.assertEqual(result['outcome'], 'merged', result)
        self.assertEqual(result['onto'], ours)
        self.assertEqual(self.heads()['main'], result['merge'])
        # GitHub refused it (its main moved): the merge stands here, and the difference is said.
        self.assertIn('not yet', result['github'])
        githouse.fetch(self.repo)
        self.assertEqual(self.differs()['main']['state'], 'apart')
        self.assertEqual(self.heads()['main'], result['merge'])

    def test_a_branch_only_on_github_merges(self):
        # A branch githouse never had (dependabot's, say), merged by its head on GitHub.
        self.commit(self.upstream, 'a bump')
        run('git', 'push', '--quiet', 'origin', 'HEAD:dependabot/bump', cwd=self.upstream)
        head = run('git', '--git-dir', str(self.github), 'rev-parse', 'dependabot/bump')[1].strip()
        result = self.merge(self.order(branch='dependabot/bump', commit=head))
        self.assertEqual(result['outcome'], 'merged', result)
        self.assertEqual(self.github_main(), result['merge'])
        self.assertEqual(self.heads()['main'], result['merge'])

    def test_only_into_deploy_branches_and_not_twice(self):
        self.assertIn('a merge goes into', self.merge(self.order(into='re/other'))['why'])
        self.assertIn("the deploy branches don't", self.merge(self.order(branch='main'))['why'])
        self.assertEqual(self.merge(self.order())['outcome'], 'merged')
        self.assertIn('no such branch', self.merge(self.order())['why'])  # merged: its name is retired here
        self.assertIn('no repository', self.merge(self.order(repo='nonesuch'))['why'])

    # --- the brakes (Plan, section 10): any of them stops a merge, and none of them can make one

    def assert_braked(self, why, **merge):
        before = self.github_main()
        text = self.order()
        result = self.merge(text, **merge)
        self.assertEqual(result['outcome'], 'refused', result)
        self.assertIn(why, result['why'])
        self.assertEqual(self.github_main(), before)
        # Spent: a braked order can't be played again once the brake is off.
        self.banned, self.scram, self.answer = set(), False, None
        (self.etc / githouse.FREEZE).unlink(missing_ok=True)
        self.assertEqual(self.merge(text, self.sign(text), brakes=self.brakes)['why'], 'that order was used already')

    def test_memory_lane_is_asked_about_the_signer(self):
        self.assertEqual(self.merge(self.order())['outcome'], 'merged')
        self.assertEqual(self.asked, [['justin']])

    def test_a_banned_signer(self):
        self.banned = {'justin'}
        self.assert_braked('justin: banned')

    def test_az5(self):
        self.scram = {'by': 'justin', 'at': '2026-11-08T00:00:00Z'}  # memory-lane says who pulled it: not False
        self.assert_braked('AZ5 is in force')

    def test_root_s_freeze_holds_whatever_memory_lane_says(self):
        (self.etc / githouse.FREEZE).touch()
        self.assert_braked('frozen on maybelle')

    def test_memory_lane_not_answering(self):
        closed = 'http://127.0.0.1:9/api/brakes/'  # the discard port: nothing listens
        self.assert_braked("memory-lane didn't say", brakes=closed)

    def test_an_answer_that_leaves_the_signer_out(self):
        self.answer = {'scram': False, 'banned': {}}
        self.assert_braked('justin: banned')

    def test_an_answer_of_the_wrong_shape(self):
        for answer in ({'ok': True}, {'scram': False, 'banned': ['justin']}, {'scram': False, 'banned': 'no'}, []):
            self.answer = answer
            self.assert_braked("memory-lane didn't say")

    def test_nowhere_to_ask(self):
        with unittest.mock.patch.object(githouse, 'BRAKES_URL', ''):
            self.assert_braked("doesn't know where", brakes='')

    # --- orders by mail (Plan, section 8, 8 Nov): memory-lane carries them, githouse checks them

    def mailed(self, *orders):
        self.mail = {'orders': [{'order': text, 'signature': signature} for text, signature in orders]}
        return [result for _, result in githouse.take_orders(self.mailbox, now=self.now, signers=self.signers,
                                                               brakes_url=self.brakes)]

    def test_a_mailed_order_merges_once(self):
        text = self.order()
        signature = self.sign(text)
        self.assertEqual([r['outcome'] for r in self.mailed((text, signature))], ['merged'])
        self.assertEqual(self.github_main(), githouse.refs(self.repo, 'refs/heads/')['main'])
        self.assertEqual(self.mailed((text, signature)), [])  # still in the mailbox: its nonce is spent, so skipped

    def test_a_forged_order_ahead_of_the_real_one(self):
        # memory-lane, or anyone posting to it, mails an unsigned order with the real one's nonce first.
        text = self.order()
        results = self.mailed((text, 'nonsense'), (text.replace('re/foo', 're/evil'), self.sign(text)),
                              (text, self.sign(text)))
        self.assertEqual([r['outcome'] for r in results], ['refused', 'refused', 'merged'])
        # The forgeries are kept under what was mailed (sha256 of order, NUL, signature), for memory-lane to report.
        seen = hashlib.sha256(f'{text}\0nonsense'.encode()).hexdigest()
        kept = json.loads((self.root / '.merges' / 'mailed' / f'{seen}.json').read_text())
        self.assertIn('not signed by', kept['why'])
        self.assertEqual(self.mailed((text, 'nonsense')), [])  # tried once, not every five seconds

    def test_a_mailed_order_is_braked_like_any(self):
        self.banned = {'justin'}
        text = self.order()
        result, = self.mailed((text, self.sign(text)))
        self.assertIn('justin: banned', result['why'])

    def test_memory_lane_not_answering_is_logged_once(self):
        closed = 'http://127.0.0.1:9/api/merge-orders/pending/'
        self.assertEqual(githouse.take_orders(closed), [])
        self.assertEqual(githouse.take_orders(closed), [])
        lines = (self.root / 'githouse.log').read_text().splitlines()
        self.assertEqual(sum('merge orders: memory-lane did not answer' in line for line in lines), 1)

    def test_nowhere_to_take_orders_from(self):
        self.assertEqual(githouse.take_orders(''), [])

    # --- the merge button (Plan, section 10, 8 Nov): memory-lane's key, a person's press

    def test_a_press_merges_as_the_person_who_pressed(self):
        result = self.merge(*self.pressed('justin'))
        self.assertEqual(result['outcome'], 'merged', result)
        message = run('git', '--git-dir', str(self.github), 'log', '-1', '--format=%B%n%an', 'main')[1]
        self.assertIn('Pressed by justin, signed by memory-lane', message)
        self.assertEqual(message.strip().splitlines()[-1], 'justin')
        self.assertEqual(self.asked, [['justin']])  # the brakes are asked about the person, never the key

    def test_a_press_by_someone_who_may_not_merge(self):
        # skyler has a key here but isn't in this signers file: the button can't do for them what their key can't.
        result = self.merge(*self.pressed('skyler'))
        self.assertEqual((result['outcome'], result['why']), ('refused', 'skyler may not merge'))

    def test_a_braked_presser(self):
        self.banned = {'justin'}
        result = self.merge(*self.pressed('justin'))
        self.assertIn('justin: banned', result['why'])

    def test_whose_press_is_said_by_the_button_s_orders_and_only_theirs(self):
        text, signature = self.pressed('justin')
        no_for = text.replace('for: justin\n', '')
        self.assertIn("says whose press it was", self.merge(no_for, self.sign(no_for, key='memory-lane'))['why'])
        mine = self.order(**{'for': 'skyler'})
        self.assertIn("says whose press it was", self.merge(mine)['why'])
        for_itself = self.order(by='memory-lane', **{'for': 'memory-lane'})
        self.assertIn("isn't a person's name", self.merge(for_itself, self.sign(for_itself, key='memory-lane'))['why'])
        # memory-lane's key can't sign an order as anyone but memory-lane.
        as_justin = self.order()
        self.assertIn('not signed by justin', self.merge(as_justin, self.sign(as_justin, key='memory-lane'))['why'])

    def test_not_an_order(self):
        self.assertEqual(self.merge('merge everything please', 'x')['why'], 'not a merge order')

if __name__ == '__main__':
    unittest.main()
