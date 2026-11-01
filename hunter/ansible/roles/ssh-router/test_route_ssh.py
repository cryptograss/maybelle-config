"""route-ssh, rendered from its template against hunter's inventory and run with a stand-in docker.

    python3 -m pytest hunter/ansible/roles/ssh-router/test_route_ssh.py
"""

import os
import subprocess
from pathlib import Path

import jinja2
import pytest
import yaml

HERE = Path(__file__).parent
INVENTORY = HERE.parents[1] / 'inventory.yml'


@pytest.fixture
def route(tmp_path):
    users = yaml.safe_load(INVENTORY.read_text())['all']['vars']['users']
    script = tmp_path / 'route-ssh'
    script.write_text(jinja2.Template((HERE / 'templates' / 'route-ssh.sh.j2').read_text())
                      .render(users=users, base_dir=tmp_path))
    script.chmod(0o755)
    (tmp_path / 'moods' / 'chartifacts').mkdir(parents=True)
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    running = '\\n'.join(f"{u['name']}-arthel" for u in users)
    (bin_dir / 'docker').write_text(f'#!/bin/sh\n[ "$1" = ps ] && printf "{running}\\n" && exit 0\necho "DOCKER: $*"\n')
    (bin_dir / 'docker').chmod(0o755)

    def run(user, command=''):
        env = dict(os.environ, PATH=f'{bin_dir}:{os.environ["PATH"]}', SSH_ORIGINAL_COMMAND=command)
        out = subprocess.run([str(script), user], env=env, capture_output=True, text=True, timeout=30).stdout
        return [line[len('DOCKER: '):] for line in out.splitlines() if line.startswith('DOCKER: ')]
    return run


def test_a_moods_slug_is_a_shell_in_its_directory_in_workbench(route):
    assert route('justin', 'chartifacts') == [
        'exec -it workbench-arthel su - magent -c cd /moods/chartifacts && exec bash -l']


def test_with_tmux_it_is_a_session_named_for_the_mood_there(route):
    assert route('justin', 'chartifacts tmux') == [
        'exec -it workbench-arthel su - magent -c tmux new-session -A -s chartifacts -c /moods/chartifacts']


def test_workbench_by_name(route):
    assert route('justin', 'workbench') == ['exec -it workbench-arthel su - magent']


def test_a_person_with_a_container_of_their_own_still_lands_in_it(route):
    assert route('justin') == ['exec -it justin-arthel su - magent']


def test_a_person_without_one_lands_in_workbench(route):
    assert route('someone-new') == ['exec -it workbench-arthel su - magent']


def test_what_isnt_a_moods_directory_is_a_command_in_your_own_container_as_before(route):
    for command in ('nosuchmood', '../etc', 'chartifacts; rm -rf /'):
        assert route('justin', command) == [f'exec -it justin-arthel su - magent -c {command}']
