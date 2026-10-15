#!/usr/bin/env python3
"""Keep each resident agent's home on hunter's disk, across container recreation.

A Mood's container mounts /opt/magenta/<container>/agents/<agent> at
/home/<agent> (hunter/CONTAINERS.md). That home holds what the agent can't
get back from anywhere else in a hurry: its harness's sessions (Pi's under
~/.local/state/magenta/pi-sessions), its poller's state, and its clones.

Run by the user-instance role before the container is (re)created. For each
agent it makes sure the directory exists, owned by the agent's uid and
readable by nobody else. The first time, if the running container already
has a home for the agent (it lived inside the container until now), that
home is copied out first, so the agent keeps its sessions through the
change.

Prints one JSON line: {"made": [...], "carried": [...]} (empty lists: no change).

    agent_homes.py --base-dir /opt/magenta --container docs-tests-qa \
        --agents '[{"name": "re", "uid": 2001}]'
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

AGENT_NAME = re.compile(r'[a-z][a-z0-9-]{0,30}')  # as container_startup.py
CONTAINER = re.compile(r'[a-z0-9][a-z0-9_.-]*')
# Below 2000 are the host's own users and magent (1000); an agent's files
# on hunter's disk shouldn't look like they belong to one of them.
MIN_UID = 2000


class Invalid(ValueError):
    pass


def checked(agents):
    """The agents as given, each with a usable name and uid, or Invalid."""
    out = []
    for agent in agents:
        name, uid = str(agent.get('name', '')), agent.get('uid')
        if not AGENT_NAME.fullmatch(name) or name == 'magent':
            raise Invalid(f'not an agent name: {name!r}')
        if not isinstance(uid, int) or isinstance(uid, bool) or not MIN_UID <= uid < 60000:
            raise Invalid(f'{name}: uid must be a number from {MIN_UID} (got {uid!r})')
        out.append({'name': name, 'uid': uid})
    uids = [a['uid'] for a in out]
    if len(set(uids)) != len(uids):
        raise Invalid('two agents with one uid')
    return out


def docker(*args):
    return subprocess.run(['docker', *args], capture_output=True, text=True)


# What docker cp says when there's nothing to carry: no such container (a new
# Mood's), or a container without that home (an agent new to it).
NOTHING_THERE = ('No such container', 'Could not find the file')


def carry_out(container, inside, dest, run=docker):
    """Copy `inside` out of `container` to a new directory `dest`: True if
    copied, False if there was nothing there. All or nothing: copied beside
    `dest` first, so a failed copy leaves no half a home behind."""
    part = dest.with_name(dest.name + '.carrying')
    if part.exists():
        shutil.rmtree(part)
    copied = run('cp', '-a', f'{container}:{inside}/.', str(part))
    if copied.returncode == 0:
        part.rename(dest)
        return True
    if part.exists():
        shutil.rmtree(part)
    if any(words in copied.stderr for words in NOTHING_THERE):
        return False
    raise RuntimeError(f'copying {inside} out of {container} failed: {copied.stderr.strip()}')


def chown_tree(path, uid):
    for root, dirs, files in os.walk(path):
        for name in dirs + files:
            os.lchown(os.path.join(root, name), uid, uid)
    os.lchown(path, uid, uid)


def keep(base_dir, container, agents, run=docker):
    made, carried = [], []
    for agent in checked(agents):
        home = Path(base_dir) / container / 'agents' / agent['name']
        if not home.exists():
            home.parent.mkdir(parents=True, exist_ok=True)
            if carry_out(f'{container}-arthel', f'/home/{agent["name"]}', home, run):
                carried.append(agent['name'])
            else:
                home.mkdir(mode=0o750)
            made.append(agent['name'])
        # Its own, and nobody else's: a copy comes out as root, and nothing
        # else should be able to read its keys.
        if home.stat().st_uid != agent['uid'] or agent['name'] in carried:
            chown_tree(home, agent['uid'])
        os.chmod(home, 0o750)
    return {'made': made, 'carried': carried}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--base-dir', required=True)
    parser.add_argument('--container', required=True, help="The container's inventory name (docs-tests-qa)")
    parser.add_argument('--agents', required=True, help='JSON: [{"name": ..., "uid": ...}, ...]')
    args = parser.parse_args(argv)
    if not CONTAINER.fullmatch(args.container):
        parser.error(f'not a container name: {args.container!r}')
    try:
        result = keep(args.base_dir, args.container, json.loads(args.agents))
    except (Invalid, ValueError) as e:
        print(f'agent_homes: {e}', file=sys.stderr)
        return 2
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    sys.exit(main())
