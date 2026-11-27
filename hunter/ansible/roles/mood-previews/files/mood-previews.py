#!/usr/bin/env python3
"""Each Mood's previews, by its name: <slug>0-9.hunter.cryptograss.live (Cryptograss:Moods as Directories, step 4).

Every Mood with a directory under /opt/magenta/moods gets ten preview ports in workbench, the people's
box, kept in that directory's .previews file the first time it's seen: a block of ten from 15000 on,
never reused while the file is there, and carried along if the directory is renamed. A dev server
started in workbench on one of those ports, bound to 0.0.0.0, is at its hostname.

Run each minute as root. It writes, and reloads Caddy only when they change:
    /etc/caddy/moods.caddy      a site for each hostname, to workbench's address on magenta-net
    /etc/caddy/moods-ask/<host> one empty file for each: what Caddy's on-demand TLS may get a cert for,
                                so a certificate is asked for only when a hostname is first visited
                                (Let's Encrypt allows 50 new ones a week for cryptograss.live)
A Mood whose name is also a container's under /opt/magenta (its old hostnames, <name>0-9, are in the
main Caddyfile) is left out until that container is retired: two sites with one address fail Caddy.

A dev server started by hand dies when its container is recreated, which every deploy does. So a Mood
may have one of its ten served by a container of its own instead, declared in hunter's inventory
(`mood_servers`, written here as /etc/magenta/mood-servers.json). Each minute this writes that
container's compose file under /opt/magenta/mood-servers/<name>/, brings it up if the file changed or
it isn't running, and points that one hostname at it rather than at workbench. The Mood's directory is
mounted read-only, as the group moods, with nothing else: no docker.sock, no secrets. Its command gets
the Mood's own port where it says {port}, so the .previews block stays the one place ports are given.
Declared in the inventory and so merged by a person; nothing inside a Mood's directory can ask for a
container. While such a container has no address, its hostname is left pointing at workbench.

    mood-previews.py [--base-dir /opt/magenta] [--caddy-dir /etc/caddy] [--domain hunter.cryptograss.live]
                     [--servers /etc/magenta/mood-servers.json]
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

SLUG = re.compile(r'^[a-z0-9][a-z0-9_-]*$')
NAME = re.compile(r'^[a-z0-9][a-z0-9_-]*$')
FIRST, LAST, EACH = 15000, 19990, 10
WORKBENCH = 'workbench'
NETWORK = 'magenta-net'
SERVERS = '/etc/magenta/mood-servers.json'
IMAGE = 'python:3.13-slim'
# nobody, in the group that owns every Mood's directory (playbook.yml: group moods, gid 3000).
AS_USER = '65534:3000'


def previews_file(mood):
    return mood / '.previews'


def block_of(mood):
    """The first port of this Mood's block, from its .previews file, or None."""
    try:
        for line in previews_file(mood).read_text().splitlines():
            line = line.strip()
            if line and not line.startswith('#'):
                port = int(line.split()[0])
                return port if FIRST <= port <= LAST and (port - FIRST) % EACH == 0 else None
    except (OSError, ValueError):
        return None
    return None


def write_previews(mood, base, domain):
    lines = [f"# {mood.name}'s previews (Cryptograss:Moods as Directories): start a dev server in workbench on one",
             '# of these ports, bound to 0.0.0.0, and it is at the address beside it. Kept by hunter; the first',
             '# line is what it reads.']
    lines += [f'{base + n} https://{mood.name}{n}.{domain}' for n in range(EACH)]
    previews_file(mood).write_text('\n'.join(lines) + '\n')


def moods(base_dir):
    root = base_dir / 'moods'
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir() and SLUG.match(p.name))


def containers(base_dir):
    """The names of containers that still have hostnames of their own (a compose file under base_dir)."""
    return {p.parent.name for p in base_dir.glob('*/docker-compose.yml')}


def allocate(base_dir, domain):
    """{slug: first port}, giving each Mood without a block the lowest one free."""
    found = {mood: block_of(mood) for mood in moods(base_dir)}
    taken = set()
    for mood, base in found.items():
        if base is not None and base in taken:  # a copied .previews: the second one gets its own
            found[mood] = None
        elif base is not None:
            taken.add(base)
    free = (port for port in range(FIRST, LAST + 1, EACH) if port not in taken)
    blocks = {}
    for mood, base in found.items():
        if base is None:
            base = next(free, None)
            if base is None:
                print(f'{mood.name}: no preview ports left', file=sys.stderr)
                continue
            write_previews(mood, base, domain)
        blocks[mood.name] = base
    return blocks


def render(blocks, address, domain, skip=(), elsewhere=None):
    """(moods.caddy, the hostnames it serves). elsewhere: {(slug, n): (address, port)}, a Mood's own server."""
    elsewhere = elsewhere or {}
    sites, hosts = ['# Written by hunter-mood-previews each minute: each Mood\'s previews, in workbench.', ''], []
    for slug, base in sorted(blocks.items()):
        if slug in skip:
            sites.append(f'# {slug}: left out while a container of that name has its own {slug}0-9')
            continue
        for n in range(EACH):
            host = f'{slug}{n}.{domain}'
            hosts.append(host)
            where, port = elsewhere.get((slug, n), (address, base + n))
            if (slug, n) in elsewhere:
                sites.append(f'# {slug}{n}: a container of its own (mood_servers), not workbench')
            sites += [f'{host} {{', '    tls {', '        on_demand', '    }',
                      f'    reverse_proxy {where}:{port}', '}', '']
    return '\n'.join(sites), hosts


def address_of(container):
    """A container's address on magenta-net, or None if it isn't there."""
    got = subprocess.run(['docker', 'inspect', '-f', '{{json .NetworkSettings.Networks}}', container],
                         capture_output=True, text=True, timeout=30)
    if got.returncode != 0:
        return None
    return (json.loads(got.stdout or '{}').get(NETWORK) or {}).get('IPAddress') or None


def declared(path):
    """The Mood servers hunter's inventory declares, each checked over, or [] if there are none."""
    try:
        told = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return []
    out = []
    for entry in told if isinstance(told, list) else []:
        mood, name = str(entry.get('mood', '')), str(entry.get('name', ''))
        index, command = entry.get('index'), entry.get('command')
        if not (SLUG.match(mood) and NAME.match(name)):
            print(f'mood_servers: {mood or "?"}/{name or "?"} is not a name: left out', file=sys.stderr)
            continue
        if not isinstance(index, int) or not 0 <= index < EACH:
            print(f'mood_servers: {name} wants port {index} of ten: left out', file=sys.stderr)
            continue
        if not (isinstance(command, list) and command and all(isinstance(word, str) for word in command)):
            print(f'mood_servers: {name} has no command: left out', file=sys.stderr)
            continue
        out.append({'mood': mood, 'name': name, 'index': index, 'command': command,
                    'dir': str(entry.get('dir') or '.'), 'image': str(entry.get('image') or IMAGE)})
    return out


def compose_text(server, base_dir, port):
    """The compose file for one Mood's own preview server: its directory read-only, and nothing else."""
    served = (Path(base_dir) / 'moods' / server['mood'] / server['dir']).resolve()
    command = [word.replace('{port}', str(port)) for word in server['command']]
    return '\n'.join([
        f"# Written by hunter-mood-previews: {server['mood']}{server['index']}, from a container of its own",
        '# (hunter/ansible/inventory.yml, mood_servers). Changes belong in the inventory, not here.',
        'services:',
        f"  {server['name']}:",
        f"    image: {server['image']}",
        f"    container_name: {server['name']}",
        '    restart: unless-stopped',
        f'    user: "{AS_USER}"',
        '    working_dir: /serve',
        '    read_only: true',
        '    command: ' + json.dumps(command),
        '    volumes:',
        f'      - {served}:/serve:ro',
        '    networks: [' + NETWORK + ']',
        f'    expose: ["{port}"]',
        'networks:',
        f'  {NETWORK}:',
        '    external: true',
        ''])


def running(container):
    got = subprocess.run(['docker', 'inspect', '-f', '{{.State.Running}}', container],
                         capture_output=True, text=True, timeout=30)
    return got.returncode == 0 and got.stdout.strip() == 'true'


def bring_up(path):
    got = subprocess.run(['docker', 'compose', '-f', str(path), 'up', '-d'],
                         capture_output=True, text=True, timeout=300)
    if got.returncode != 0:
        print(f'{path}: would not come up:\n{got.stderr[-2000:]}', file=sys.stderr)
    return got.returncode == 0


def serve_moods(servers, base_dir, blocks, up=None, where=None):
    """Keep each declared server's container up, and say where its hostname goes: {(slug, n): (address, port)}."""
    up, where = up or bring_up, where or address_of
    elsewhere = {}
    for server in servers:
        base = blocks.get(server['mood'])
        if base is None:
            print(f"mood_servers: {server['mood']} has no previews of its own yet: {server['name']} left down",
                  file=sys.stderr)
            continue
        port = base + server['index']
        path = Path(base_dir) / 'mood-servers' / server['name'] / 'docker-compose.yml'
        path.parent.mkdir(parents=True, exist_ok=True)
        changed = write_if_changed(path, compose_text(server, base_dir, port))
        if changed or not running(server['name']):
            up(path)
        address = where(server['name'])
        if address is None:
            print(f"mood_servers: {server['name']} has no address on {NETWORK}: "
                  f"{server['mood']}{server['index']} left pointing at {WORKBENCH}", file=sys.stderr)
            continue
        elsewhere[(server['mood'], server['index'])] = (address, port)
    return elsewhere


def write_if_changed(path, text):
    if path.exists() and path.read_text() == text:
        return False
    path.write_text(text)
    return True


def sync_ask(ask_dir, hosts):
    ask_dir.mkdir(parents=True, exist_ok=True)
    changed = False
    for stale in set(p.name for p in ask_dir.iterdir()) - set(hosts):
        (ask_dir / stale).unlink()
        changed = True
    for host in hosts:
        if not (ask_dir / host).exists():
            (ask_dir / host).touch()
            changed = True
    return changed


def run(base_dir, caddy_dir, domain, address=None, reload=None, servers=None, up=None, where=None):
    blocks = allocate(base_dir, domain)
    address = address or address_of(WORKBENCH)
    if address is None:
        print(f'{WORKBENCH} has no address on {NETWORK}: previews left as they were', file=sys.stderr)
        return blocks
    elsewhere = serve_moods(declared(servers) if servers else [], base_dir, blocks, up=up, where=where)
    text, hosts = render(blocks, address, domain, skip=containers(base_dir), elsewhere=elsewhere)
    changed = write_if_changed(caddy_dir / 'moods.caddy', text + '\n')
    changed = sync_ask(caddy_dir / 'moods-ask', hosts) or changed
    if changed and reload:
        reload()
    return blocks


def reload_caddy():
    check = subprocess.run(['caddy', 'validate', '--config', '/etc/caddy/Caddyfile', '--adapter', 'caddyfile'],
                           capture_output=True, text=True, timeout=120)
    if check.returncode != 0:
        print(f'Caddy would not take it; left running as it was:\n{check.stderr[-2000:]}', file=sys.stderr)
        return
    subprocess.run(['systemctl', 'reload', 'caddy'], check=False, timeout=120)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--base-dir', default='/opt/magenta')
    parser.add_argument('--caddy-dir', default='/etc/caddy')
    parser.add_argument('--domain', default='hunter.cryptograss.live')
    parser.add_argument('--servers', default=SERVERS, help="the Mood servers hunter's inventory declares")
    args = parser.parse_args(argv)
    blocks = run(Path(args.base_dir), Path(args.caddy_dir), args.domain, reload=reload_caddy,
                 servers=args.servers)
    print(json.dumps({'moods_with_previews': len(blocks)}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
