#!/usr/bin/env python3
"""githouse: our canonical copies of the repositories, on maybelle, at /srv/git.

The ramp, Phase 1 (Justin, re and magent in #ramp, 30 Oct - 6 Nov 2026;
the plan: Cryptograss:Moods/ramp/Plan, section 3). maybelle is the
integrator: the copy deploys come from lives there, not on hunter, which is
meant to be cheap to lose.

* A bare repository per repository we work on, /srv/git/<name>.git.
* GitHub's branches -- main, master, production, release/* -- and pull
  requests' heads are GitHub's: fetched every 60 seconds, the repositories
  staggered across the minute, forced and pruned to match. Nobody pushes them
  here. Until the flip, merges still happen on GitHub, and this copy lags by
  up to a minute.
* Everyone else's branches are their own: re pushes re/*, magent magent/*,
  over ssh as the git user, on maybelle's private network only. Each key's
  forced command names who it is (`githouse auth <name>`), and the hook lets
  that name push <name>/* and nothing else: no other branches, no tags. The
  fetch never touches these, so a push can't be clobbered by a fetch in
  flight, and nothing deletes them on its own.
* Keys come from the emergents' containers on hunter, which make their own
  (Justin, 1 Nov: not a vault secret). The public halves are pulled from
  hunter into /etc/githouse/keys/<container>/<name>.pub; pushers.json, from
  maybelle's inventory, says which names each container may vouch for, and
  that is the only trust anchor: hunter is a relay. One key per name; a
  missing file skips the name; nothing is carried forward. hunter's own
  mirror key is fetch-only (`auth --fetch-only`): hunter can't mint itself a
  pushable identity.
* A branch's name is used once. A mergeable is called <repo>:<branch>
  (maybelle-config:re/fix-mtime-guard) for its whole life; when it merges its
  ref moves to refs/merged/<branch>, when its owner deletes it to
  refs/closed/<branch>, and a push creating either name again is refused,
  with the next free one offered. Merged means: its tip is in a GitHub
  branch's history but not on that branch's own line (first parents) -- a
  merge commit brought it in. A branch just cut from main, with nothing on
  it yet, isn't merged. (Squash and rebase merges break this: merge commits
  only, on every repository here.)
* What's pushed here goes on to GitHub (post-receive) with the deploy key,
  so pull requests keep working until the flip; a forward that fails is
  logged, never retried by deleting anything.
* branches.json, beside the repositories: every repository's branches, live,
  merged and closed, for magenta's autocomplete (memory-lane serves it).
* `githouse mirror`, on hunter: a read-only copy of all of it, fetched from
  maybelle every minute and mounted into every container, so clones are
  instant. Nothing on it needs backing up.

One file, so it can be tested against real repositories (test_githouse.py).
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
ETC = Path(os.environ.get('GITHOUSE_ETC', '/etc/githouse'))
OWNED = ('main', 'master', 'production', 'release/*')  # GitHub's branches
NAME = re.compile(r'[a-z][a-z0-9-]{0,31}')
BRANCH = re.compile(r'[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*')
PUBKEY = re.compile(r'^(sk-)?(ssh-ed25519|ecdsa-sha2-nistp\d+|ssh-rsa)(@openssh\.com)? [A-Za-z0-9+/=]+( \S+)?$')
ZERO = '0' * 40
EVERY = 60  # seconds between fetches of any one repository


def git(repo, *args, check=True, env=None):
    """Its output; on failure, raise -- or, with check=False, None."""
    done = subprocess.run(['git', '--git-dir', str(repo), *args], capture_output=True, text=True,
                          env={**os.environ, **(env or {})})
    if done.returncode != 0:
        if check:
            raise RuntimeError(f"git {' '.join(args)}: {done.stderr.strip()}")
        return None
    return done.stdout


def ssh_with(key):
    """GIT_SSH_COMMAND for a key kept beside the repositories (the deploy key, or the mirror's)."""
    return (f"ssh -i {key} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "
            f"-o UserKnownHostsFile={ROOT / '.known_hosts'}")


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


def auth(user, command, fetch_only=False):
    """The forced command: (program, repository path) for a key's ssh command, or raise."""
    if not NAME.fullmatch(user or ''):
        raise PermissionError('this key names nobody')
    found = _COMMAND.match((command or '').strip())
    if not found:
        raise PermissionError('only git fetch and git push here')
    if fetch_only and found.group(1) != 'upload-pack':
        raise PermissionError(f'{user} fetches only')
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


def authorized_keys(keys_dir, pushers):
    """sshd's authorized_keys from the relayed public keys: for each container pushers.json names,
    each name it may vouch for whose <name>.pub is there and is one key. A key may only run
    `githouse auth <name>` (fetch-only where pushers.json says so): no shell, no forwarding."""
    lines = []
    for container, given in sorted(pushers.items()):
        names = given.get('names', []) if isinstance(given, dict) else given
        fetch_only = isinstance(given, dict) and given.get('fetch_only', False)
        for name in names:
            if not NAME.fullmatch(str(name)):
                continue
            path = Path(keys_dir) / str(container) / f'{name}.pub'
            if not path.is_file():
                continue
            key = path.read_text().strip()
            if '\n' in key or not PUBKEY.match(key):
                log(f'{container}/{name}.pub is not one public key; skipped')
                continue
            flag = ' --fetch-only' if fetch_only else ''
            lines.append(f'command="{sys.executable} {Path(__file__).resolve()} auth{flag} {name}",restrict {key}')
    return '\n'.join(lines) + ('\n' if lines else '')


def write_authorized_keys():
    """The keys as relayed now, to the file sshd reads. Nothing kept from before."""
    pushers_file = ETC / 'pushers.json'
    pushers = json.loads(pushers_file.read_text()) if pushers_file.exists() else {}
    text = authorized_keys(ETC / 'keys', pushers)
    path = ROOT / '.authorized_keys'
    if not path.exists() or path.read_text() != text:
        temp = path.with_suffix('.tmp')
        temp.write_text(text)
        temp.chmod(0o644)
        temp.replace(path)
        log(f'authorized_keys: {text.count(chr(10))} key(s)')


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


# --- setting up, and the loops --------------------------------------------------------

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
    temp.chmod(0o644)
    temp.replace(path)


def log(line):
    with open(ROOT / 'githouse.log', 'a') as f:
        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {line}\n")


def paced(step, before=None):
    """Run step(repo) for every repository, one after another across EVERY seconds, forever;
    before() first each round, repositories or none."""
    while True:
        if before:
            try:
                before()
            except Exception as e:
                log(f'{e}')
        repos = sorted(ROOT.glob('*.git'))
        for repo in repos:
            started = time.monotonic()
            try:
                step(repo)
            except Exception as e:
                log(f'{repo.name}: {e}')
            time.sleep(max(0.0, EVERY / max(len(repos), 1) - (time.monotonic() - started)))
        if not repos:
            time.sleep(EVERY)


def loop():
    """On maybelle: the keys as relayed, then GitHub's branches, every minute."""
    paced(fetch, before=write_authorized_keys)


def mirror_init(full_names, upstream):
    """On hunter: a bare repository per name, its remote the copy on maybelle."""
    for full_name in full_names:
        name = full_name.split('/', 1)[1]
        repo = ROOT / f'{name}.git'
        if not (repo / 'HEAD').exists():
            subprocess.run(['git', 'init', '--quiet', '--bare', str(repo)], check=True)
        git(repo, 'remote', 'remove', 'maybelle', check=False)
        git(repo, 'remote', 'add', 'maybelle', f'{upstream.rstrip("/")}/{name}.git')
        git(repo, 'config', 'uploadpack.allowReachableSHA1InWant', 'true')


def mirror(repo):
    """Everything maybelle has, as it has it: every ref forced, the gone ones pruned, HEAD the same."""
    git(repo, 'fetch', '--quiet', '--no-tags', '--prune', 'maybelle', '+refs/*:refs/*')
    default = re.search(r'^ref: (refs/heads/\S+)\tHEAD$', git(repo, 'ls-remote', '--symref', 'maybelle', 'HEAD'), re.M)
    if default:
        git(repo, 'symbolic-ref', 'HEAD', default.group(1))
    publish()


def main(argv):
    args = [a for a in argv[1:] if not a.startswith('--')]
    flags = {a for a in argv[1:] if a.startswith('--')}
    command = args[0] if args else ''
    # Before anything: the hooks run under sshd's forced command, with none of entrypoint.sh's exports,
    # and post-receive's forward to GitHub needs the deploy key too (re's review of #250, 6 Nov).
    key = ROOT / '.deploy_key'
    if key.exists() and 'GIT_SSH_COMMAND' not in os.environ:
        os.environ['GIT_SSH_COMMAND'] = ssh_with(key)
    if command == 'auth':  # authorized_keys: command="githouse auth re", or auth --fetch-only hunter
        try:
            program, repo = auth(args[1] if len(args) > 1 else '', os.environ.get('SSH_ORIGINAL_COMMAND', ''),
                                 fetch_only='--fetch-only' in flags)
        except PermissionError as e:
            sys.stderr.write(f'githouse: {e}\n')
            return 1
        os.environ['GITHOUSE_USER'] = args[1]
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
        init(args[1:], url=url, forward=os.environ.get('GITHOUSE_FORWARD') == 'true')
        return 0
    if command == 'fetch':
        for repo in sorted(ROOT.glob('*.git')):
            fetch(repo)
        publish()
        return 0
    if command == 'loop':
        loop()
    if command == 'mirror':  # mirror <upstream> <owner/name>...: hunter's copy of maybelle's
        mirror_init(args[2:], args[1])
        paced(mirror)
    sys.stderr.write(__doc__)
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv))
