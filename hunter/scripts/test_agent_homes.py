"""python3 -m unittest hunter/scripts/test_agent_homes.py"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import agent_homes  # noqa: E402
from agent_homes import Invalid, checked, keep  # noqa: E402


class FakeDocker:
    """docker cp, as the daemon answers it: `homes` maps container -> {path: {file: text}}."""

    def __init__(self, homes=None, broken=False):
        self.homes, self.broken, self.calls = homes or {}, broken, []

    def __call__(self, *args):
        self.calls.append(args)
        assert args[:2] == ('cp', '-a'), args
        container, path = args[2].split(':', 1)
        dest = Path(args[3])
        if self.broken:
            dest.mkdir()
            (dest / 'half').write_text('x')
            return subprocess.CompletedProcess(args, 1, '', 'Error: disk full')
        if container not in self.homes:
            return subprocess.CompletedProcess(args, 1, '', f'Error response from daemon: No such container: {container}')
        files = self.homes[container].get(path[:-2])  # "/home/re/." -> "/home/re"
        if files is None:
            return subprocess.CompletedProcess(args, 1, '', f'Error response from daemon: Could not find the file {path} in container {container}')
        dest.mkdir()
        for name, text in files.items():
            (dest / name).parent.mkdir(parents=True, exist_ok=True)
            (dest / name).write_text(text)
        return subprocess.CompletedProcess(args, 0, '', '')


class AgentHomesTest(unittest.TestCase):

    def setUp(self):
        self.base = Path(tempfile.mkdtemp())
        # Not root here: record what would be chowned instead of doing it.
        self.owners = {}
        patcher = mock.patch.object(agent_homes.os, 'lchown',
                                    side_effect=lambda path, uid, gid: self.owners.__setitem__(str(path), uid))
        patcher.start()
        self.addCleanup(patcher.stop)

    def home(self, agent='re'):
        return self.base / 'docs-tests-qa' / 'agents' / agent

    def test_a_home_lived_in_inside_the_container_is_carried_out(self):
        docker = FakeDocker({'docs-tests-qa-arthel': {'/home/re': {
            '.local/state/magenta/pi-sessions/s1.jsonl': '{}', 'workspace/red/LETTER.md': 'Dear re'}}})
        got = keep(self.base, 'docs-tests-qa', [{'name': 're', 'uid': 2001}], run=docker)
        self.assertEqual(got, {'made': ['re'], 'carried': ['re']})
        self.assertEqual((self.home() / 'workspace/red/LETTER.md').read_text(), 'Dear re')
        self.assertTrue((self.home() / '.local/state/magenta/pi-sessions/s1.jsonl').exists())
        self.assertEqual(oct(self.home().stat().st_mode & 0o777), '0o750')
        # Everything in it is the agent's, not root's.
        self.assertEqual(self.owners[str(self.home())], 2001)
        self.assertEqual(self.owners[str(self.home() / 'workspace/red/LETTER.md')], 2001)
        self.assertFalse(self.home().with_name('re.carrying').exists())

    def test_the_second_time_nothing_is_copied_again(self):
        docker = FakeDocker({'docs-tests-qa-arthel': {'/home/re': {'a': '1'}}})
        keep(self.base, 'docs-tests-qa', [{'name': 're', 'uid': 2001}], run=docker)
        docker.homes['docs-tests-qa-arthel']['/home/re'] = {'a': 'the container is now mounting it'}
        with mock.patch.object(Path, 'stat', return_value=os.stat_result((0o40750, 0, 0, 0, 2001, 2001, 0, 0, 0, 0))):
            got = keep(self.base, 'docs-tests-qa', [{'name': 're', 'uid': 2001}], run=docker)
        self.assertEqual(got, {'made': [], 'carried': []})
        self.assertEqual(len(docker.calls), 1)
        self.assertEqual((self.home() / 'a').read_text(), '1')

    def test_a_new_mood_or_a_new_agent_gets_an_empty_home(self):
        for homes in ({}, {'docs-tests-qa-arthel': {'/home/magent': {}}}):
            with self.subTest(homes=homes):
                if self.home().exists():
                    self.home().rmdir()
                got = keep(self.base, 'docs-tests-qa', [{'name': 're', 'uid': 2001}], run=FakeDocker(homes))
                self.assertEqual(got, {'made': ['re'], 'carried': []})
                self.assertEqual(list(self.home().iterdir()), [])
                self.assertEqual(self.owners[str(self.home())], 2001)

    def test_a_failed_copy_leaves_nothing_behind_and_fails_the_deploy(self):
        with self.assertRaises(RuntimeError):
            keep(self.base, 'docs-tests-qa', [{'name': 're', 'uid': 2001}], run=FakeDocker(broken=True))
        self.assertFalse(self.home().exists())
        self.assertFalse(self.home().with_name('re.carrying').exists())

    def test_names_and_uids_are_checked_before_anything_is_made(self):
        for agents in ([{'name': 're'}], [{'name': 're', 'uid': 1001}], [{'name': 're', 'uid': '2001'}],
                       [{'name': 're', 'uid': True}], [{'name': '../etc', 'uid': 2001}],
                       [{'name': 'magent', 'uid': 2001}],
                       [{'name': 're', 'uid': 2001}, {'name': 'or', 'uid': 2001}]):
            with self.subTest(agents=agents):
                with self.assertRaises(Invalid):
                    checked(agents)
        self.assertEqual(checked([{'name': 're', 'uid': 2001, 'harness': 'pi'}]), [{'name': 're', 'uid': 2001}])

    def test_main_refuses_a_strange_container_name(self):
        with self.assertRaises(SystemExit):
            agent_homes.main(['--base-dir', str(self.base), '--container', '../x', '--agents', '[]'])
        self.assertEqual(agent_homes.main(['--base-dir', str(self.base), '--container', 'x',
                                           '--agents', '[{"name": "re"}]']), 2)


if __name__ == '__main__':
    unittest.main()
