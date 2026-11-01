#!/usr/bin/env python3
"""githouse: our canonical clones of the repositories, on hunter, at /srv/git.

The ramp, Phase 1 (Justin, re and magent in #ramp, 31 Oct - 1 Nov 2026):

* A bare repository per repository we work on, /srv/git/<name>.git. Each
  container clones from it (read-only, mounted at /srv/git), not from GitHub.
* GitHub's branches -- main, master, production, release/* -- are GitHub's:
  fetched every 60 seconds, the repositories staggered across the minute,
  forced and pruned to match. Nobody pushes them here.
* Everyone else's are their own: re pushes re/*, magent magent/*, justin
  justin/*, over ssh as the git user, each key's forced command naming who it
  is (`githouse auth <name>`). No other branches, and no tags. The fetch never
  touches these, so a push can't be clobbered by a fetch in flight.
* A branch's name is used once. A mergeable is called <repo>:<branch>
  (maybelle-config:re/fix-mtime-guard) for its whole life; when it merges its
  ref moves to refs/merged/<branch>, when its owner deletes it to
  refs/closed/<branch>, and a push creating either name again is refused,
  with the next free one offered. Merged means: its tip is in a GitHub
  branch's history but not on that branch's own line (first parents) -- a
  merge commit brought it in. So a branch just cut from main, with nothing on
  it yet, isn't merged.
* What's pushed here goes on to GitHub (post-receive), with the deploy key,
  if the vault has one; a forward that fails is logged, never retried by
  deleting anything.
* branches.json, beside the repositories: every repository's branches, live,
  merged and closed, for magenta's autocomplete.

One file, so it can be tested against real repositories (test_githouse.py):
`githouse auth`, `pre-receive`, `post-receive`, `init`, `fetch`, `loop`.
"""

import fnmatch
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(os.environ.get('GITHOUSE_ROOT', '/srv/git'))
OWNED = ('main', 'master', 'production', 'release/*')  # GitHub's branches
NAME = re.compile(r'[a-z][a-z0-9-]{0,31}')
BRANCH = re.compile(r'[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*')
ZERO = '0' * 40
EVERY = 60  # seconds between fetches of any one repository
# With the vault's deploy key (entrypoint.sh puts it here), GitHub is reached over ssh: the fetch,
# and the forward a push's hook makes, whoever's session that is.
if (ROOT / '.deploy_key').exists() and 'GIT_SSH_COMMAND' not in os.environ:
    os.environ['GIT_SSH_COMMAND'] = (f"ssh -i {ROOT / '.deploy_key'} -o StrictHostKeyChecking=accept-new "
                                     f"-o UserKnownHostsFile={ROOT / '.known_hosts'}")


def git(repo, *args, check=True, env=None):
    """Its output; on failure, raise -- or, with check=False, None."""
    done = subprocess.run(['git', '--git-dir', str(repo), *args], capture_output=True, text=True,
                          env={**os.environ, **(env or {})})
    if done.returncode != 0:
        if check:
            raise RuntimeError(f"git {' '.join(args)}: {done.stderr.strip()}")
        return None
    return done.stdout


def owned(branch):
    """Whether a branch is GitHub's (fetched here, never pushed here)."""
    return any(fnmatch.fnmatchcase(branch, pattern) for pattern in OWNED)


def refs(repo, prefix):
    """{name: sha} of the refs under prefix (refs/heads/, refs/merged/ ...), names without it."""
    out = git(repo, 'for-each-ref', '--format=%(objectname) %(refname)', prefix)
    return {line.split(' ', 1)[1][len(prefix):]: line.split(' ', 1)[0] for line in out.splitlines() if line}


def retired(repo, branch):
    """'merged' or 'closed' if the name was used already, or None."""
    for kind in ('merged', 'closed'):
        if (git(repo, 'rev-parse', '--verify', '--quiet', f'refs/{kind}/{branch}', check=False) or '').strip():
            return kind
    return None


def next_free(repo, branch):
    n = 2
    while retired(repo, f'{branch}-{n}') or (git(repo, 'rev-parse', '--verify', '--quiet',
                                                  f'refs/heads/{branch}-{n}', check=False) or '').strip():
        n += 1
    return f'{branch}-{n}'


# --- who's pushing ----------------------------------------------------------------

_COMMAND = re.compile(r"^git[ -](upload-pack|receive-pack) '?/?([A-Za-z0-9._-]+?)(?:\.git)?/?'?$")


def auth(user, command):
    """The forced command: (program, repository path) for a key's ssh command, or raise."""
    if not NAME.fullmatch(user or ''):
        raise PermissionError('this key names nobody')
    found = _COMMAND.match((command or '').strip())
    if not found:
        raise PermissionError('only git fetch and git push here')
    repo = ROOT / f'{found.group(2)}.git'
    if repo.resolve().parent != ROOT.resolve() or not (repo / 'HEAD').exists():
        raise PermissionError(f'no repository {found.group(2)} here')
    return f'git-{found.group(1)}', repo


def check(repo, user, updates):
    """What's refused in a push: [message], empty if it's all accepted."""
    refused = []
    for old, new, ref in updates:
        if not ref.startswith('refs/heads/'):
            refused.append(f'{ref}: only branches are pushed here (no tags)')
            continue
        branch = ref[len('refs/heads/'):]
        if not branch.startswith(f'{user}/') or owned(branch) or not BRANCH.fullmatch(branch):
            refused.append(f'{branch}: {user} pushes {user}/<name> branches only')
            continue
        if old == ZERO and new != ZERO:
            kind = retired(repo, branch)
            if kind:
                refused.append(f'{branch}: that name was used already ({kind}); try {next_free(repo, branch)}')
    return refused


def after(repo, user, updates, forward=None):
    """What a push leaves to do: a deleted branch's name kept as closed, and the push sent on to GitHub."""
    for old, new, ref in updates:
        branch = ref[len('refs/heads/'):]
        if new == ZERO and old != ZERO:
            git(repo, 'update-ref', f'refs/closed/{branch}', old)
    if forward is None:
        forward = bool((git(repo, 'config', '--get', 'githouse.forward', check=False) or '').strip())
    if forward:
        specs = [f':{ref}' if new == ZERO else f'+{ref}:{ref}' for old, new, ref in updates]
        done = subprocess.run(['git', '--git-dir', str(repo), 'push', '--quiet', 'github', *specs],
                              capture_output=True, text=True)
        if done.returncode != 0:
            log(f'{repo.name}: forwarding {user}\'s push to GitHub failed: {done.stderr.strip()}')
    publish()


# --- GitHub's branches -------------------------------------------------------------

def fetch(repo):
    """GitHub's branches and pull requests' heads, as GitHub has them now; then retire what merged.
    True if anything changed."""
    listed = git(repo, 'ls-remote', 'github', 'refs/heads/*', 'refs/pull/*/head')
    there = {}
    for line in listed.splitlines():
        sha, ref = line.split('\t', 1)
        if ref.startswith('refs/pull/') or owned(ref[len('refs/heads/'):]):
            there[ref] = sha
    here = {f'refs/heads/{b}': s for b, s in refs(repo, 'refs/heads/').items() if owned(b)}
    here.update({f'refs/pull/{n}': s for n, s in refs(repo, 'refs/pull/').items()})
    if there == here:
        return False
    wanted = [f'+{ref}:{ref}' for ref, sha in there.items() if here.get(ref) != sha]
    for start in range(0, len(wanted), 200):
        git(repo, 'fetch', '--quiet', '--no-tags', 'github', *wanted[start:start + 200])
    for ref in set(here) - set(there):  # gone from GitHub: GitHub's, so gone here
        git(repo, 'update-ref', '-d', ref)
    # A clone checks out what GitHub would give it: its default branch.
    default = re.search(r'^ref: refs/heads/(\S+)\tHEAD$', git(repo, 'ls-remote', '--symref', 'github', 'HEAD'), re.M)
    if default and owned(default.group(1)):
        git(repo, 'symbolic-ref', 'HEAD', f'refs/heads/{default.group(1)}')
    retire(repo)
    publish()
    return True


def retire(repo):
    """Move every pushed branch that a merge brought into a GitHub branch to refs/merged/."""
    heads = refs(repo, 'refs/heads/')
    bases = {b: s for b, s in heads.items() if owned(b)}
    lines = {b: set(git(repo, 'rev-list', '--first-parent', s).split()) for b, s in bases.items()}
    moved = []
    for branch, tip in heads.items():
        if owned(branch):
            continue
        for base, sha in bases.items():
            if tip not in lines[base] and _ancestor(repo, tip, sha):
                # Only if it still holds that tip: a push landing meanwhile keeps it live, and unretired.
                if git(repo, 'update-ref', '-d', f'refs/heads/{branch}', tip, check=False) is None:
                    break
                git(repo, 'update-ref', f'refs/merged/{branch}', tip)
                moved.append(branch)
                break
    return moved


def _ancestor(repo, a, b):
    return subprocess.run(['git', '--git-dir', str(repo), 'merge-base', '--is-ancestor', a, b]).returncode == 0


# --- setting up, and the loop --------------------------------------------------------

def init(full_names, url='https://github.com/{}.git', forward=False):
    """A bare repository for each owner/name not here yet, its github remote, and its hooks."""
    for full_name in full_names:
        name = full_name.split('/', 1)[1]
        repo = ROOT / f'{name}.git'
        if not (repo / 'HEAD').exists():
            subprocess.run(['git', 'init', '--quiet', '--bare', str(repo)], check=True)
        git(repo, 'remote', 'remove', 'github', check=False)
        git(repo, 'remote', 'add', 'github', url.format(full_name))
        git(repo, 'config', 'githouse.forward', 'true' if forward else '')
        git(repo, 'config', 'uploadpack.allowReachableSHA1InWant', 'true')
        hooks = repo / 'hooks'
        hooks.mkdir(exist_ok=True)
        for hook in ('pre-receive', 'post-receive'):
            path = hooks / hook
            path.write_text(f'#!/bin/sh\nexec {sys.executable} {Path(__file__).resolve()} {hook}\n')
            path.chmod(0o755)
    publish()


def publish():
    """branches.json: every repository's branches, live, merged and closed, for autocomplete."""
    found = {}
    for repo in sorted(ROOT.glob('*.git')):
        found[repo.name[:-4]] = {kind: refs(repo, prefix) for kind, prefix in
                                 (('live', 'refs/heads/'), ('merged', 'refs/merged/'), ('closed', 'refs/closed/'))}
    path = ROOT / 'branches.json'
    temp = path.with_suffix('.json.tmp')
    temp.write_text(json.dumps(found, indent=1, sort_keys=True))
    temp.replace(path)


def log(line):
    with open(ROOT / 'githouse.log', 'a') as f:
        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {line}\n")


def serve(port=8091):
    """branches.json over http, and nothing else: Caddy publishes it at git.<hunter>/branches.json."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Branches(BaseHTTPRequestHandler):
        def do_GET(self):
            path = ROOT / 'branches.json'
            if self.path.split('?')[0] != '/branches.json' or not path.exists():
                self.send_error(404)
                return
            body = path.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('0.0.0.0', port), Branches)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def loop():
    """Fetch every repository once a minute, one after another across the minute."""
    serve()
    while True:
        repos = sorted(ROOT.glob('*.git'))
        for repo in repos:
            started = time.monotonic()
            try:
                fetch(repo)
            except Exception as e:
                log(f'{repo.name}: fetch failed: {e}')
            time.sleep(max(0.0, EVERY / max(len(repos), 1) - (time.monotonic() - started)))


def main(argv):
    command = argv[1] if len(argv) > 1 else ''
    if command == 'auth':  # authorized_keys: command="githouse auth re"
        try:
            program, repo = auth(argv[2] if len(argv) > 2 else '', os.environ.get('SSH_ORIGINAL_COMMAND', ''))
        except PermissionError as e:
            sys.stderr.write(f'githouse: {e}\n')
            return 1
        os.environ['GITHOUSE_USER'] = argv[2]
        os.execvp(program, [program, str(repo)])
    if command in ('pre-receive', 'post-receive'):
        repo = Path(os.environ.get('GIT_DIR', '.')).resolve()
        user = os.environ.get('GITHOUSE_USER', '')
        updates = [tuple(line.split()) for line in sys.stdin.read().splitlines() if line.strip()]
        if command == 'pre-receive':
            refused = check(repo, user, updates) if user else ['pushes come through githouse auth']
            for message in refused:
                sys.stderr.write(f'githouse: {message}\n')
            return 1 if refused else 0
        after(repo, user, updates)
        return 0
    if command == 'init':
        url = os.environ.get('GITHOUSE_URL', 'https://github.com/{}.git')
        init(argv[2:], url=url, forward=os.environ.get('GITHOUSE_FORWARD') == 'true')
        return 0
    if command == 'fetch':
        for repo in sorted(ROOT.glob('*.git')):
            fetch(repo)
        publish()
        return 0
    if command == 'loop':
        loop()
    sys.stderr.write(__doc__)
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv))
