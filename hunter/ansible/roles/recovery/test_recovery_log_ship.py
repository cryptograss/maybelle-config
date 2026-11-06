"""recovery-log-ship, with maybelle stood in for: lines go once, and wait while it's away.

    python3 -m pytest hunter/ansible/roles/recovery/test_recovery_log_ship.py
"""

import importlib.machinery
import importlib.util
from pathlib import Path

import pytest

HERE = Path(__file__).parent


@pytest.fixture
def ship(tmp_path):
    loader = importlib.machinery.SourceFileLoader('recovery_log_ship', str(HERE / 'files' / 'recovery-log-ship'))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(module)
    home = tmp_path / 'recovery'
    (home / '.claude' / 'projects' / 'p').mkdir(parents=True)
    module.HOME = str(home)
    module.SOURCES = [str(home / '.claude' / 'projects')]
    module.STATE = str(tmp_path / 'offsets.json')
    module.received, module.up = [], True

    def send(batch):
        if module.up:
            module.received.append(batch)
        return module.up
    module.send = send

    offsets = {}
    module.step = lambda: module.cycle(offsets)
    module.transcript = home / '.claude' / 'projects' / 'p' / 's.jsonl'
    return module


def test_whole_lines_go_once(ship):
    ship.transcript.write_bytes(b'{"a":1}\n{"b":2}\n{"c":')
    ship.step()
    ship.step()
    assert ship.received == [b'.claude/projects/p/s.jsonl\t{"a":1}\n.claude/projects/p/s.jsonl\t{"b":2}\n']


def test_while_maybelle_is_away_lines_wait_then_go(ship):
    ship.transcript.write_bytes(b'{"a":1}\n')
    ship.up = False
    ship.step()
    assert ship.received == []
    ship.up = True
    ship.step()
    assert ship.received == [b'.claude/projects/p/s.jsonl\t{"a":1}\n']


def test_the_part_line_goes_when_it_is_whole(ship):
    ship.transcript.write_bytes(b'{"a":')
    ship.step()
    with open(ship.transcript, 'ab') as f:
        f.write(b'1}\n')
    ship.step()
    assert ship.received == [b'.claude/projects/p/s.jsonl\t{"a":1}\n']
