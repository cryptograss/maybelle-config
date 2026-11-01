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

    mood-previews.py [--base-dir /opt/magenta] [--caddy-dir /etc/caddy] [--domain hunter.cryptograss.live]
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

SLUG = re.compile(r'^[a-z0-9][a-z0-9_-]*$')
FIRST, LAST, EACH = 15000, 19990, 10
WORKBENCH = 'workbench-arthel'
NETWORK = 'magenta-net'


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


def render(blocks, address, domain, skip=()):
    """(moods.caddy, the hostnames it serves)."""
    sites, hosts = ['# Written by hunter-mood-previews each minute: each Mood\'s previews, in workbench.', ''], []
    for slug, base in sorted(blocks.items()):
        if slug in skip:
            sites.append(f'# {slug}: left out while a container of that name has its own {slug}0-9')
            continue
        for n in range(EACH):
            host = f'{slug}{n}.{domain}'
            hosts.append(host)
            sites += [f'{host} {{', '    tls {', '        on_demand', '    }',
                      f'    reverse_proxy {address}:{base + n}', '}', '']
    return '\n'.join(sites), hosts


def workbench_address():
    got = subprocess.run(['docker', 'inspect', '-f', '{{json .NetworkSettings.Networks}}', WORKBENCH],
                         capture_output=True, text=True, timeout=30)
    if got.returncode != 0:
        return None
    return (json.loads(got.stdout or '{}').get(NETWORK) or {}).get('IPAddress') or None


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


def run(base_dir, caddy_dir, domain, address=None, reload=None):
    blocks = allocate(base_dir, domain)
    address = address or workbench_address()
    if address is None:
        print(f'{WORKBENCH} has no address on {NETWORK}: previews left as they were', file=sys.stderr)
        return blocks
    text, hosts = render(blocks, address, domain, skip=containers(base_dir))
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
    args = parser.parse_args(argv)
    blocks = run(Path(args.base_dir), Path(args.caddy_dir), args.domain, reload=reload_caddy)
    print(json.dumps({'moods_with_previews': len(blocks)}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
