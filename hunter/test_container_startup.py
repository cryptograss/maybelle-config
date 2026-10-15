"""python3 -m unittest hunter/test_container_startup.py

The parts of container startup that decide things, without a container."""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import container_startup  # noqa: E402
from container_startup import agent_uid, clone_in_background, repos_by_when  # noqa: E402

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


if __name__ == '__main__':
    unittest.main()
