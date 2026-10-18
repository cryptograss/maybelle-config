"""python3 -m unittest hunter/test_container_startup.py

The parts of container startup that decide things, without a container."""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import container_startup  # noqa: E402
from container_startup import (agent_uid, clone_in_background, poller_command, repos_by_when,  # noqa: E402
                               retire_home_claude_md, write_poller_env)

RE = {'name': 're', 'uid': 2001, 'rules': 'workspace/red/RULES.md', 'letter': 'workspace/red/LETTER.md',
      'repos': ['magent-cryptograss/red', 'cryptograss/memory-lane', 'cryptograss/arthel']}


class AgentStartupTest(unittest.TestCase):

    def test_the_repo_its_rules_are_in_comes_first_the_rest_after_its_poller(self):
        first, later = repos_by_when(RE)
        self.assertEqual(first, [('magent-cryptograss/red', Path('/home/re/workspace/red'))])
        self.assertEqual([repo for repo, _ in later], ['cryptograss/memory-lane', 'cryptograss/arthel'])

    def test_a_name_that_only_starts_like_the_rules_repo_is_not_it(self):
        agent = dict(RE, repos=['someone/re', 'someone/red-herring', 'magent-cryptograss/red'])
        first, later = repos_by_when(agent)
        self.assertEqual([repo for repo, _ in first], ['magent-cryptograss/red'])
        self.assertEqual([repo for repo, _ in later], ['someone/re', 'someone/red-herring'])

    def test_strange_repo_names_are_left_out(self):
        agent = dict(RE, repos=['magent-cryptograss/red', "x/y'; rm -rf ~", 'nope', 7])
        first, later = repos_by_when(agent)
        self.assertEqual((len(first), later), (1, []))

    def test_missing_clones_are_cloned_and_present_ones_fetched_after_the_poller(self):
        present = Path('/home/re/workspace/memory-lane')
        with mock.patch.object(container_startup, 'run_command') as run, \
                mock.patch.object(Path, 'exists', lambda p: p == present):
            clone_in_background(RE)
        (command,), options = run.call_args
        self.assertEqual(options, {'user': 're', 'check': False})
        self.assertIn('git -C /home/re/workspace/memory-lane fetch --quiet', command)
        self.assertIn('git clone --quiet https://github.com/cryptograss/arthel.git /home/re/workspace/arthel', command)
        self.assertNotIn('red', command)
        self.assertTrue(command.startswith('nohup ') and command.endswith('&'))
        self.assertNotIn("'", command)  # run_command wraps it in single quotes for su

    def test_nothing_to_clone_starts_nothing(self):
        with mock.patch.object(container_startup, 'run_command') as run:
            clone_in_background(dict(RE, repos=['magent-cryptograss/red']))
        run.assert_not_called()

    def test_an_agent_needs_its_uid_from_the_inventory(self):
        self.assertEqual(agent_uid(RE), 2001)
        for uid in (None, 1001, '2001', True):
            with self.subTest(uid=uid):
                with self.assertRaises(ValueError):
                    agent_uid(dict(RE, uid=uid))



class HomeClaudeMdTest(unittest.TestCase):
    """magent's instructions moved to magenta's agents/magent.md (18 Oct 2026); ~/.claude/CLAUDE.md,
    copied there on deploy, is the one copy, and the old ~/CLAUDE.md link into the workspace goes."""

    def setUp(self):
        import tempfile
        self.home = Path(tempfile.mkdtemp())

    def test_the_old_link_into_the_workspace_goes(self):
        link = self.home / 'CLAUDE.md'
        link.symlink_to('/home/magent/workspace/magenta/CLAUDE.md')
        self.assertTrue(retire_home_claude_md(link))
        self.assertFalse(link.is_symlink() or link.exists())

    def test_a_file_or_another_link_is_left_alone(self):
        mine = self.home / 'CLAUDE.md'
        mine.write_text('my own notes')
        self.assertFalse(retire_home_claude_md(mine))
        self.assertEqual(mine.read_text(), 'my own notes')
        other = self.home / 'OTHER.md'
        other.symlink_to('/somewhere/else.md')
        self.assertFalse(retire_home_claude_md(other))
        self.assertTrue(other.is_symlink())


class PollerSignInTest(unittest.TestCase):
    """magent's poller signs in with the setup-token, and nothing else gets it."""

    def setUp(self):
        import tempfile
        self.path = Path(tempfile.mkdtemp()) / '.config' / 'magenta' / 'poller.env'
        patcher = mock.patch.object(container_startup.shutil, 'chown')
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_token_goes_in_a_file_only_magent_reads(self):
        with mock.patch.dict(os.environ, {'MAGENT_CLAUDE_CODE_OAUTH_TOKEN': ' sk-ant-oat01-abc_DEF-123 \n'}):
            self.assertTrue(write_poller_env(self.path))
        self.assertEqual(self.path.read_text(), 'CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-abc_DEF-123\n')
        self.assertEqual(oct(self.path.stat().st_mode & 0o777), '0o600')

    def test_without_a_token_the_file_goes_and_the_login_is_used(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text('CLAUDE_CODE_OAUTH_TOKEN=old\n')
        with mock.patch.dict(os.environ, {'MAGENT_CLAUDE_CODE_OAUTH_TOKEN': ''}):
            self.assertFalse(write_poller_env(self.path))
        self.assertFalse(self.path.exists())

    def test_only_the_pollers_own_shell_sources_it(self):
        signed = poller_command('--agent magent', True)
        self.assertTrue(signed.startswith('set -a; . ~/.config/magenta/poller.env; set +a; nohup '))
        self.assertTrue(signed.endswith('--agent magent >> /tmp/mood-poller.log 2>&1 &'))
        self.assertNotIn('poller.env', poller_command('--agent magent', False))
        self.assertNotIn("'", signed)  # run_command wraps it in single quotes for su


if __name__ == '__main__':
    unittest.main()
