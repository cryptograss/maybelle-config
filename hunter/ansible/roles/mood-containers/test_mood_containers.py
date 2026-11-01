"""hunter-mood-containers, with magenta and docker stood in for.

    python3 -m pytest hunter/ansible/roles/mood-containers/test_mood_containers.py
"""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location('mood_containers', Path(__file__).parent / 'files' / 'mood-containers.py')
mc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mc)


class FakeAPI:
    def __init__(self, moods):
        self.moods, self.reports = moods, []

    def wanted(self):
        return self.moods

    def report(self, mood_id, **body):
        self.reports.append((mood_id, body))


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    shared = tmp_path / 'opt/mood-containers'
    shared.mkdir(parents=True)
    (shared / 'authorized_keys').write_text('ssh-ed25519 AAAA justin\n')
    (shared / 'settings.local.json').write_text('{}')
    (shared / 'gitconfig').write_text('[user]\n')
    (tmp_path / 'magent.md').write_text('# magent\n')
    caddyfile = tmp_path / 'Caddyfile'
    caddyfile.write_text('justin0.hunter.cryptograss.live {\n    reverse_proxy localhost:14000\n}\n')
    (tmp_path / 'moods.d').mkdir()
    ran = []
    monkeypatch.setattr(mc, 'run', lambda *cmd, cwd=None: ran.append(cmd) or SimpleNamespace(returncode=0, stderr='', stdout=''))
    monkeypatch.setattr(mc, 'running', lambda name: True)
    monkeypatch.setattr(mc.os, 'lchown', lambda *a: None)
    return {'base_dir': str(tmp_path / 'opt'), 'shared_dir': str(shared), 'env_file': str(shared / 'mood.env'),
            'image': 'magenta-arthel:latest', 'memory_lane_dir': '/opt/magenta/magenta-source/memory-lane',
            'instructions': str(tmp_path / 'magent.md'), 'domain': 'hunter.cryptograss.live',
            'caddy_dir': str(tmp_path / 'moods.d'), 'caddyfile': str(caddyfile), 'people': ['justin', 'skyler'],
            'ran': ran}


def mood(**kw):
    return {'id': 17, 'slug': 'chartifacts', 'aliases': [], 'threads': [], 'wanted': True, **kw}


def test_a_wanted_mood_comes_up_with_its_home_ports_and_previews(cfg):
    api = FakeAPI([mood(aliases=['charts'])])
    mc.once(api, cfg, log=lambda *a: None)
    root = Path(cfg['base_dir']) / 'mood-17'
    compose = (root / 'docker-compose.yml').read_text()
    for line in ('container_name: mood-17-arthel', '"2317:22"', '"127.0.0.1:19017:8080"', '"127.0.0.1:20170-20179:4000-4009"',
                 'MOOD_CONTAINER=mood-17', 'MOOD_SLUG=chartifacts', 'MOOD_FULL_TOOLS_FOR=justin,skyler'):
        assert line in compose
    assert 'docker.sock' not in compose
    assert (root / '.ssh/authorized_keys').read_text() == 'ssh-ed25519 AAAA justin\n'
    assert (root / 'home/.claude/CLAUDE.md').exists() and (root / 'home/workspace').is_dir()
    assert ('docker', 'compose', '-p', 'mood-17', 'up', '-d') in cfg['ran']
    caddy = (Path(cfg['caddy_dir']) / 'mood-17.caddy').read_text()
    assert 'chartifacts3.hunter.cryptograss.live {\n    reverse_proxy localhost:20173\n}' in caddy
    assert 'code.chartifacts.hunter.cryptograss.live {\n    reverse_proxy localhost:19017\n}' in caddy
    assert 'charts3.hunter.cryptograss.live {\n    redir https://chartifacts3.hunter.cryptograss.live{uri}\n}' in caddy
    assert ('systemctl', 'reload', 'caddy') in cfg['ran']
    [(mid, body)] = api.reports
    assert (mid, body['state'], body['ssh_port']) == (17, 'running', 2317)
    assert 'chartifacts0.hunter.cryptograss.live' in body['hosts']


def test_it_says_so_only_when_something_changed(cfg):
    api = FakeAPI([mood()])
    mc.once(api, cfg, log=lambda *a: None)
    cfg['ran'].clear()
    mc.once(api, cfg, log=lambda *a: None)
    assert len(api.reports) == 1
    assert ('systemctl', 'reload', 'caddy') not in cfg['ran']  # Caddy's file the same: no reload


def test_let_go_it_comes_down_and_its_files_stay(cfg):
    api = FakeAPI([mood()])
    mc.once(api, cfg, log=lambda *a: None)
    api.moods = [mood(wanted=False)]
    mc.once(api, cfg, log=lambda *a: None)
    assert ('docker', 'compose', '-p', 'mood-17', 'down') in cfg['ran']
    assert not (Path(cfg['caddy_dir']) / 'mood-17.caddy').exists()
    assert (Path(cfg['base_dir']) / 'mood-17/home').is_dir()
    assert api.reports[-1] == (17, {'state': 'stopped', 'detail': '', 'hosts': [], 'ssh_port': None})


def test_a_host_the_deploy_already_serves_is_left_to_it(cfg):
    api = FakeAPI([mood(slug='justin')])
    mc.once(api, cfg, log=lambda *a: None)
    caddy = (Path(cfg['caddy_dir']) / 'mood-17.caddy').read_text()
    assert 'justin0.hunter' not in caddy and 'justin1.hunter.cryptograss.live' in caddy


def test_a_failed_start_is_reported_as_such(cfg, monkeypatch):
    monkeypatch.setattr(mc, 'run', lambda *cmd, cwd=None: SimpleNamespace(
        returncode=1 if 'up' in cmd else 0, stderr='no such image', stdout=''))
    api = FakeAPI([mood()])
    mc.once(api, cfg, log=lambda *a: None)
    assert api.reports[0][1]['state'] == 'failed' and 'no such image' in api.reports[0][1]['detail']


def test_strange_ids_and_slugs_are_passed_by(cfg):
    api = FakeAPI([mood(id=999), mood(id=5, slug='../etc')])
    mc.once(api, cfg, log=lambda *a: None)
    assert api.reports == [] and not list(Path(cfg['base_dir']).glob('mood-[0-9]*'))
