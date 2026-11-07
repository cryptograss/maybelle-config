"""githouse against real repositories: a stand-in GitHub, githouse's clone of it, and someone's working copy."""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
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

    def merge_on_github(self, branch):
        """Someone merges a pushed branch on GitHub, with a merge commit, as our repositories do."""
        run('git', 'fetch', '--quiet', str(self.repo), f'refs/heads/{branch}:refs/remotes/h/{branch}', cwd=self.upstream)
        run('git', 'merge', '--quiet', '--no-ff', '-m', f'Merge {branch}', f'h/{branch}', cwd=self.upstream)
        run('git', 'push', '--quiet', 'origin', 'HEAD:main', cwd=self.upstream)


class GithouseTest(Repos):

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

    def test_a_push_landing_while_retiring_keeps_the_branch_live(self):
        self.commit(self.work, 'mine')
        self.push('re', 'HEAD:re/foo')
        merged_tip = githouse.refs(self.repo, 'refs/heads/')['re/foo']
        self.merge_on_github('re/foo')
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

    def order(self, **change):
        fields = {'repo': 'magenta', 'branch': 're/foo', 'commit': self.head, 'into': 'main', 'by': 'justin',
                  'nonce': os.urandom(16).hex(), 'expires': str(self.now + 3600), **change}
        return githouse.ORDER_HEADER + '\n' + ''.join(f'{k}: {fields[k]}\n' for k in githouse.ORDER_FIELDS)

    def sign(self, text, key='justin', namespace=None):
        path = Path(tempfile.mkdtemp(dir=self.tmp)) / 'order.txt'  # a .sig already there would be asked about
        path.write_text(text)
        run('ssh-keygen', '-Y', 'sign', '-q', '-f', str(self.keys / key), '-n', namespace or githouse.MERGE_NAMESPACE, str(path))
        return path.with_suffix('.txt.sig').read_text()

    def merge(self, text, signature=None):
        return githouse.merge(text, signature if signature is not None else self.sign(text), now=self.now,
                              signers=self.signers)

    def github_main(self):
        return run('git', '--git-dir', str(self.github), 'rev-parse', 'main')[1].strip()

    def test_a_signed_order_merges_on_github_first(self):
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
        before = self.github_main()
        result = self.merge(self.order())
        self.assertIn('conflicts with main', result['why'])
        self.assertEqual(self.github_main(), before)

    def test_only_into_githubs_branches_and_not_twice(self):
        self.assertIn('a merge goes into', self.merge(self.order(into='re/other'))['why'])
        self.assertIn("GitHub's own branches don't", self.merge(self.order(branch='main'))['why'])
        self.assertEqual(self.merge(self.order())['outcome'], 'merged')
        self.assertIn('no such branch', self.merge(self.order())['why'])  # merged: its name is retired here
        self.assertIn('no repository', self.merge(self.order(repo='nonesuch'))['why'])

    def test_not_an_order(self):
        self.assertEqual(self.merge('merge everything please', 'x')['why'], 'not a merge order')

if __name__ == '__main__':
    unittest.main()
