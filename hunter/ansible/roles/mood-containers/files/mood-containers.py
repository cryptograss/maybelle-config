#!/usr/bin/env python3
"""A Mood's own container on hunter, brought up when the Mood asks for one, without a redeploy.

Asked for in magenta (a Mood's "Give it a container", memory-lane services/containers.py); this runs every
minute (systemd: hunter-mood-containers.timer), reads what is wanted (GET /api/containers/, with hunter's
access key, as its SSH router reports with), and for each Mood:

  wanted, not up   -> its directory, home and keys made, its compose file written, `docker compose up -d`;
                      its previews at <slug><n>.hunter.cryptograss.live and code.<slug>.hunter... in Caddy
  up, not wanted   -> `docker compose down`; its Caddy file removed. Its files stay on hunter's disk.

and says how it stands (POST /api/containers/<id>/) whenever that changes. Named mood-<id> after the Mood's
id, which never changes (Justin, emergence, 1 Nov 2026): renaming a Mood only moves its hostnames, the old
ones redirecting. Its threads share it: its poller (--container mood-<id>) answers them too.

Its ports come from its id, so nothing has to remember them:
  ssh 2300+id, code-server 19000+id, previews 20000+id*10 .. +9 (container 4000-4009).
No docker.sock and no person's keys in it: full tools there stay inside it. The secrets every Mood container
shares (magent's GitHub token, its runner key, its subscription) come from the deploy (mood.env, 0600).

    mood-containers.py [--config /opt/magenta/mood-containers/config.json] [--dry-run]
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

SLUG = re.compile(r'^[a-z0-9][a-z0-9-]{0,50}$')
MAX_ID = 690  # ssh ports 2300-2990 (hunter's firewall: 2300:2999)


def ports(mood_id):
    return {'ssh': 2300 + mood_id, 'code': 19000 + mood_id, 'previews': 20000 + mood_id * 10}


def hosts(slug, domain):
    return [f'{slug}{n}.{domain}' for n in range(10)] + [f'code.{slug}.{domain}']


class API:
    def __init__(self, base, key_file):
        self.base, self.key = base.rstrip('/'), Path(key_file).read_text().strip()

    def _call(self, path, body=None):
        req = urllib.request.Request(self.base + path, method='POST' if body is not None else 'GET',
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={'Authorization': f'Bearer {self.key}', 'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)

    def wanted(self):
        return self._call('/api/containers/')['moods']

    def report(self, mood_id, **body):
        return self._call(f'/api/containers/{mood_id}/', body)


def run(*cmd, cwd=None):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=600)


def compose_file(mood, cfg):
    """The compose file for mood-<id>: the Mood containers' shape (hunter's compose template, a Mood's branch)."""
    name, p = f"mood-{mood['id']}", ports(mood['id'])
    base = cfg['base_dir']
    people = ','.join(cfg['people'])
    return f"""# Written by hunter-mood-containers for #{mood['slug']} (Mood {mood['id']}); not by hand.
services:
  {name}-arthel:
    image: {cfg['image']}
    container_name: {name}-arthel
    hostname: {name}@hunter
    mem_limit: {cfg.get('memory_limit', '4g')}
    cpus: {cfg.get('cpus', 2)}
    ports:
      - "{p['ssh']}:22"
      - "127.0.0.1:{p['code']}:8080"
      - "127.0.0.1:{p['previews']}-{p['previews'] + 9}:4000-4009"
    volumes:
      - {base}/{name}/home:/home/magent
      - {base}/{name}/.ssh:/home/magent/.ssh:ro
      - /opt/magenta/pickipedia-backups:/opt/magenta/pickipedia-backups:ro
      - {cfg['memory_lane_dir']}/poller:/opt/mood-poller:ro
    networks:
      - magenta-net
    env_file: {cfg['env_file']}
    environment:
      - DEVELOPER_NAME={name}
      - SSH_PORT={p['ssh']}
      - MOOD_SLUG={mood['slug']}
      - MOOD_CONTAINER={name}
      - MOOD_FULL_TOOLS_FOR={people}
    restart: unless-stopped

networks:
  magenta-net:
    external: true
    name: magenta-net
"""


def caddy_file(mood, cfg, taken):
    """Its previews and code-server in Caddy, by the Mood's slug now; its old slugs redirect there.
    A host the deploy's own Caddyfile already serves is left to it."""
    p, domain = ports(mood['id']), cfg['domain']
    out = [f"# mood-{mood['id']} (#{mood['slug']}): written by hunter-mood-containers"]
    for n in range(10):
        host = f"{mood['slug']}{n}.{domain}"
        if host not in taken:
            out.append(f"{host} {{\n    reverse_proxy localhost:{p['previews'] + n}\n}}")
    host = f"code.{mood['slug']}.{domain}"
    if host not in taken:
        out.append(f"{host} {{\n    reverse_proxy localhost:{p['code']}\n}}")
    for old in mood.get('aliases', []):
        if not SLUG.match(old):
            continue
        for n in range(10):
            host = f'{old}{n}.{domain}'
            if host not in taken:
                out.append(f"{host} {{\n    redir https://{mood['slug']}{n}.{domain}{{uri}}\n}}")
    return '\n\n'.join(out) + '\n'


def prepare(mood, cfg):
    """Its directory, home and keys, as the deploy makes them for a Mood's container."""
    root = Path(cfg['base_dir']) / f"mood-{mood['id']}"
    shared = Path(cfg['shared_dir'])
    made = []
    for d in ('home', 'home/workspace', 'home/.claude', '.ssh'):
        (root / d).mkdir(parents=True, exist_ok=True)
        made.append(root / d)
    for src, dest, mode in ((shared / 'authorized_keys', root / '.ssh/authorized_keys', 0o600),
                            (shared / 'settings.local.json', root / 'home/.claude/settings.local.json', 0o644),
                            (shared / 'gitconfig', root / 'home/.gitconfig', 0o644),
                            (Path(cfg['instructions']), root / 'home/.claude/CLAUDE.md', 0o644)):
        if src.exists():
            shutil.copyfile(src, dest)
            os.chmod(dest, mode)
            made.append(dest)
    os.chmod(root / '.ssh', 0o700)
    for path in made:
        os.lchown(path, 1000, 1000)  # magent, in the container
    return root


def running(name):
    got = run('docker', 'inspect', '-f', '{{.State.Running}}', f'{name}-arthel')
    return got.returncode == 0 and got.stdout.strip() == 'true'


def taken_hosts(caddyfile):
    """Hosts the deploy's own Caddyfile serves."""
    try:
        text = Path(caddyfile).read_text()
    except OSError:
        return set()
    return set(re.findall(r'^([a-z0-9.-]+) \{', text, re.M))


def once(api, cfg, dry_run=False, log=print):
    state_path = Path(cfg['shared_dir']) / 'state.json'
    try:
        said = json.loads(state_path.read_text())
    except (OSError, ValueError):
        said = {}
    caddy_dir = Path(cfg['caddy_dir'])
    taken = taken_hosts(cfg['caddyfile'])
    caddy_changed = False
    for mood in api.wanted():
        mid, slug = mood['id'], mood['slug']
        name = f'mood-{mid}'
        if not isinstance(mid, int) or not 0 < mid <= MAX_ID or not SLUG.match(slug):
            log(f'{name}: not one this can host (id 1-{MAX_ID}, a plain slug); passed by')
            continue
        caddy = caddy_dir / f'{name}.caddy'
        if mood['wanted']:
            if dry_run:
                log(f'{name}: would be up for #{slug}')
                continue
            root = prepare(mood, cfg)
            (root / 'docker-compose.yml').write_text(compose_file(mood, cfg))
            # Every time: compose recreates it only if its file changed (a renamed Mood), else nothing.
            up = run('docker', 'compose', '-p', name, 'up', '-d', cwd=root)
            if up.returncode != 0:
                state = ('failed', up.stderr.strip()[-280:])
            else:
                state = ('running', '') if running(name) else ('starting', '')
            want = caddy_file(mood, cfg, taken)
            if not caddy.exists() or caddy.read_text() != want:
                caddy.write_text(want)
                caddy_changed = True
            body = {'state': state[0], 'detail': state[1], 'ssh_port': ports(mid)['ssh'],
                    'hosts': [h for h in hosts(slug, cfg['domain']) if h not in taken]}
        else:
            if dry_run:
                log(f'{name}: would be stopped')
                continue
            root = Path(cfg['base_dir']) / name
            if (root / 'docker-compose.yml').exists():
                run('docker', 'compose', '-p', name, 'down', cwd=root)
            if caddy.exists():
                caddy.unlink()
                caddy_changed = True
            body = {'state': 'stopped', 'detail': '', 'hosts': [], 'ssh_port': None}
        if said.get(str(mid)) != body:
            api.report(mid, **body)
            said[str(mid)] = body
            log(f"{name} (#{slug}): {body['state']}{' -- ' + body['detail'] if body['detail'] else ''}")
    if caddy_changed:
        run('systemctl', 'reload', 'caddy')
    if not dry_run:
        state_path.write_text(json.dumps(said, indent=1))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--config', default='/opt/magenta/mood-containers/config.json')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    cfg = json.loads(Path(args.config).read_text())
    if not Path(cfg['key_file']).is_file():
        print('no access key on this host: no Mood containers', file=sys.stderr)
        return 0
    once(API(cfg['api'], cfg['key_file']), cfg, dry_run=args.dry_run)
    return 0


if __name__ == '__main__':
    sys.exit(main())
