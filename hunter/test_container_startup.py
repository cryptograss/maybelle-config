"""python3 -m unittest hunter/test_container_startup.py

The parts of container startup that decide things, without a container."""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import container_startup  # noqa: E402
from container_startup import (agent_uid, clone_in_background, memory_server, poller_command,  # noqa: E402
                               repos_by_when, retire_home_claude_md, write_claude_mcp, write_poller_env)

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


class MemoryKeyTest(unittest.TestCase):
    """Each agent's memory server entry carries its runner key, so the server knows who is calling."""

    def setUp(self):
        import tempfile
        self.claude_json = Path(tempfile.mkdtemp()) / '.claude.json'
        patcher = mock.patch.object(container_startup.shutil, 'chown')
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_key_goes_in_a_header(self):
        with mock.patch.dict(os.environ, {'MCP_MEMORY_URL': 'http://10.0.0.2:8000'}):
            self.assertEqual(memory_server('k3y')['headers'], {'Authorization': 'Bearer k3y'})
            self.assertNotIn('headers', memory_server(''))  # no key: magent's memory, as before keys

    def test_claude_code_gets_the_header_and_keeps_what_else_it_had(self):
        import json
        self.claude_json.write_text(json.dumps({'mcpServers': {'playwright': {'type': 'stdio', 'command': 'docker',
                                                                               'args': [], 'env': {}}},
                                                'numStartups': 7}))
        with mock.patch.dict(os.environ, {'MCP_MEMORY_URL': 'http://10.0.0.2:8000'}):
            write_claude_mcp(self.claude_json, {'magenta-memory-v2': memory_server('k3y')}, 'magent')
        state = json.loads(self.claude_json.read_text())
        self.assertEqual(state['mcpServers']['magenta-memory-v2'], {
            'type': 'http', 'url': 'http://10.0.0.2:8000', 'headers': {'Authorization': 'Bearer k3y'}})
        self.assertIn('playwright', state['mcpServers'])
        self.assertEqual(state['numStartups'], 7)
        self.assertEqual(oct(self.claude_json.stat().st_mode & 0o777), '0o600')

    def test_an_agent_has_only_the_servers_it_is_given(self):
        import json
        self.claude_json.write_text(json.dumps({'mcpServers': {'pickipedia': {'type': 'stdio', 'command': 'node',
                                                                              'args': [], 'env': {}}}}))
        write_claude_mcp(self.claude_json, {'magenta-memory-v2': memory_server('k3y')}, 're', only=True,
                         hasCompletedOnboarding=True)
        state = json.loads(self.claude_json.read_text())
        self.assertEqual(list(state['mcpServers']), ['magenta-memory-v2'])
        self.assertTrue(state['hasCompletedOnboarding'])


class AgentContainerTest(unittest.TestCase):
    """An agent's own container (agency-<name>) starts its agents and nothing for people."""

    def test_only_container_kind_agent_is_one(self):
        with mock.patch.dict(os.environ, {'CONTAINER_KIND': 'agent'}):
            self.assertTrue(container_startup.agent_container())
        for kind in ('', 'mood', 'Agent'):
            with mock.patch.dict(os.environ, {'CONTAINER_KIND': kind}):
                self.assertFalse(container_startup.agent_container())

    def test_it_starts_no_code_server_and_no_preview(self):
        ran = []
        with mock.patch.dict(os.environ, {'CONTAINER_KIND': 'agent'}), \
                mock.patch.object(container_startup, 'run_command', side_effect=lambda cmd, **kw: ran.append(cmd)), \
                mock.patch.object(container_startup, 'start_mood_poller'), \
                mock.patch.object(container_startup, 'agents', return_value=[]), \
                mock.patch.object(container_startup, 'start_pickipedia_preview') as preview:
            container_startup.start_services()
        self.assertFalse([c for c in ran if 'code-server' in c])
        preview.assert_not_called()


class AgentWorkspaceTest(unittest.TestCase):
    """An agent's own container gets SSH_PORT= (empty): its startup must not stop there
    (agency-re and agency-blu restarted in a loop on int(''), 2026-10-24)."""

    def test_setup_workspace_runs_with_an_empty_ssh_port(self):
        import tempfile
        home = Path(tempfile.mkdtemp())
        (home / 'workspace' / 'pickipedia').mkdir(parents=True)
        for kind in ('agent', ''):
            with self.subTest(kind=kind), \
                    mock.patch.dict(os.environ, {'SSH_PORT': '', 'CONTAINER_KIND': kind, 'SKIP_ARTHEL': '1'}), \
                    mock.patch.object(container_startup, 'ensure_repo_cloned'), \
                    mock.patch.object(container_startup, 'run_command'), \
                    mock.patch.object(container_startup, 'Path', side_effect=lambda p, *a: Path(str(p).replace('/home/magent', str(home)), *a)):
                container_startup.setup_workspace()
