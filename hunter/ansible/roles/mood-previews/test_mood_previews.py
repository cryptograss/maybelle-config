"""mood-previews.py against a temporary /opt/magenta and /etc/caddy.

    python3 -m pytest hunter/ansible/roles/mood-previews/test_mood_previews.py
"""

import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('mood_previews', Path(__file__).parent / 'files' / 'mood-previews.py')
mp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mp)

DOMAIN = 'hunter.example'


@pytest.fixture
def dirs(tmp_path):
    base, caddy = tmp_path / 'opt', tmp_path / 'caddy'
    (base / 'moods').mkdir(parents=True)
    caddy.mkdir()
    return base, caddy


def mood(base, slug):
    path = base / 'moods' / slug
    path.mkdir()
    return path


def test_each_mood_gets_ten_ports_and_keeps_them(dirs):
    base, caddy = dirs
    mood(base, 'chartifacts')
    mood(base, 'emergence')
    assert mp.run(base, caddy, DOMAIN, address='172.19.0.9') == {'chartifacts': 15000, 'emergence': 15010}
    told = (base / 'moods/emergence/.previews').read_text()
    assert '15013 https://emergence3.hunter.example' in told
    # A new Mood that sorts first takes the next free block, not one already given.
    mood(base, 'aardvark')
    assert mp.run(base, caddy, DOMAIN, address='172.19.0.9') == {
        'aardvark': 15020, 'chartifacts': 15000, 'emergence': 15010}


def test_a_renamed_moods_directory_keeps_its_ports(dirs):
    base, caddy = dirs
    mood(base, 'old-name')
    mp.run(base, caddy, DOMAIN, address='172.19.0.9')
    (base / 'moods/old-name').rename(base / 'moods/new-name')
    assert mp.run(base, caddy, DOMAIN, address='172.19.0.9') == {'new-name': 15000}
    assert 'new-name0.hunter.example' in (caddy / 'moods.caddy').read_text()


def test_a_copied_previews_file_doesnt_share_a_block(dirs):
    base, caddy = dirs
    mood(base, 'a')
    mp.run(base, caddy, DOMAIN, address='172.19.0.9')
    (mood(base, 'b') / '.previews').write_text((base / 'moods/a/.previews').read_text())
    blocks = mp.run(base, caddy, DOMAIN, address='172.19.0.9')
    assert blocks['a'] != blocks['b']


def test_caddy_sends_each_hostname_to_workbench_and_asks_only_for_those(dirs):
    base, caddy = dirs
    mood(base, 'chartifacts')
    reloads = []
    mp.run(base, caddy, DOMAIN, address='172.19.0.9', reload=lambda: reloads.append(1))
    site = (caddy / 'moods.caddy').read_text()
    assert 'chartifacts7.hunter.example {' in site
    assert 'reverse_proxy 172.19.0.9:15007' in site
    assert 'on_demand' in site
    assert sorted(p.name for p in (caddy / 'moods-ask').iterdir()) == [
        f'chartifacts{n}.hunter.example' for n in range(10)]
    assert reloads == [1]
    # Nothing changed: no reload.
    mp.run(base, caddy, DOMAIN, address='172.19.0.9', reload=lambda: reloads.append(1))
    assert reloads == [1]
    # A Mood gone: its hostnames go, and Caddy reloads.
    (base / 'moods/chartifacts/.previews').unlink()
    (base / 'moods/chartifacts').rmdir()
    mp.run(base, caddy, DOMAIN, address='172.19.0.9', reload=lambda: reloads.append(1))
    assert list((caddy / 'moods-ask').iterdir()) == [] and reloads == [1, 1]


def test_workbench_moving_address_moves_the_previews(dirs):
    base, caddy = dirs
    mood(base, 'chartifacts')
    mp.run(base, caddy, DOMAIN, address='172.19.0.9')
    mp.run(base, caddy, DOMAIN, address='172.19.0.44')
    assert 'reverse_proxy 172.19.0.44:15000' in (caddy / 'moods.caddy').read_text()


def test_a_mood_named_like_a_container_waits_for_it_to_retire(dirs):
    base, caddy = dirs
    mood(base, 'uploads-and-embeds')
    (base / 'uploads-and-embeds').mkdir()
    (base / 'uploads-and-embeds/docker-compose.yml').write_text('services: {}\n')
    mp.run(base, caddy, DOMAIN, address='172.19.0.9')
    assert 'uploads-and-embeds0.hunter.example {' not in (caddy / 'moods.caddy').read_text()
    (base / 'uploads-and-embeds/docker-compose.yml').rename(base / 'uploads-and-embeds/docker-compose.retired.yml')
    mp.run(base, caddy, DOMAIN, address='172.19.0.9')
    assert 'uploads-and-embeds0.hunter.example {' in (caddy / 'moods.caddy').read_text()


def test_no_workbench_changes_nothing(dirs, monkeypatch):
    base, caddy = dirs
    mood(base, 'chartifacts')
    mp.run(base, caddy, DOMAIN, address='172.19.0.9')
    before = (caddy / 'moods.caddy').read_text()
    monkeypatch.setattr(mp, 'workbench_address', lambda: None)
    mp.run(base, caddy, DOMAIN)
    assert (caddy / 'moods.caddy').read_text() == before


def test_only_plain_slugs(dirs):
    base, caddy = dirs
    mood(base, 'chartifacts')
    (base / 'moods' / '.hidden').mkdir()
    (base / 'moods' / 'Upper').mkdir()
    assert list(mp.run(base, caddy, DOMAIN, address='172.19.0.9')) == ['chartifacts']
