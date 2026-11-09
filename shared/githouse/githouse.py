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
* `githouse merge`, on maybelle: a merge without a pull request, ordered by a person and signed with
  their SSH key (Cryptograss:Moods/ramp/Plan, section 8; re's review, 7 Nov). The order names the
  repository, the branch, its head commit and the branch it goes into, with a nonce and an expiry; it
  is signed with `ssh-keygen -Y sign -n magenta-merge`, and githouse checks the signature itself against
  /etc/githouse/merge_signers, which ansible writes with only the people who may merge. memory-lane
  is never in the path. The merge is `--no-ff` of exactly that commit, pushed to GitHub first (until the
  flip GitHub's branches are GitHub's: the minute's fetch brings it back), and each order and what came
  of it are kept in /srv/git/.merges, its nonce spent whether it merged or not.
  Orders arrive by mail: memory-lane keeps them, unread, and githouse fetches them every few seconds and
  checks each itself (8 Nov: only Justin has ssh to maybelle). Brakes stop it (section 10): root's freeze (/etc/githouse/frozen), AZ5, or a ban on the signer, the
  last two asked of memory-lane at every order. Its answer can only stop a merge; no answer stops it too.
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
import hashlib
import tempfile
import threading
import time
import urllib.parse
import urllib.request
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


# --- merging, on a signed order ----------------------------------------------------

MERGE_NAMESPACE = 'magenta-merge'   # never the sign-in's ('magenta-moods'): a sign-in can't be replayed as a merge
ORDER_HEADER = 'githouse merge order v1'
ORDER_FIELDS = ('repo', 'branch', 'commit', 'into', 'by', 'nonce', 'expires')
# The merge button (Plan, section 10, 8 Nov): a phone has no key, so memory-lane signs with its own, and the
# order says whose press it was in a 'for' line. Such an order merges only for a person who may merge in their
# own right, and the brakes are asked about that person, never about the key (re, 8 Nov).
DEVICE_SIGNER = 'memory-lane'
FOR_FIELD = 'for'
ORDER_LIFE_MAX = 2 * 3600   # seconds: an order can't be made to last longer than this
SHA = re.compile(r'[0-9a-f]{40}')
NONCE = re.compile(r'[0-9a-f]{32,64}')


class Refused(Exception):
    """A merge order not carried out, and why."""


# --- the brakes: stopping takes less trust than starting (Plan, section 10) ------------
# A merge stops if root has frozen merging, if an admin has pulled AZ5, or if an admin has banned
# whoever is named in the order. Today, removing someone from the GitHub org stops their merging;
# githouse pushes with its own deploy key, so the org can't stop a merge made here, and these must.
# memory-lane keeps the bans and AZ5 (only MOOD_ADMINS set them) and is asked at every order. Its
# answer can only stop a merge, never make one, and no answer stops it too. The freeze trusts
# nothing but root: a file on maybelle, in githouse's read-only /etc, that nothing here can remove.

BRAKES_URL = os.environ.get('GITHOUSE_BRAKES', '')   # memory-lane's /api/brakes/, on maybelle's private network
FREEZE = 'frozen'                                    # /etc/githouse/frozen: `touch` it as root on maybelle


def brake(names, url=None, etc=None):
    """Why nothing may merge for these names now, or None. Fails closed: an unclear answer is a brake."""
    if (Path(etc or ETC) / FREEZE).exists():
        return 'merging is frozen on maybelle; root lifts it'
    url = url if url is not None else BRAKES_URL
    if not url:
        return "githouse doesn't know where memory-lane's brakes are; nothing merges"
    query = urllib.parse.urlencode([('name', name) for name in names])
    try:
        with urllib.request.urlopen(f'{url}?{query}', timeout=10) as answer:
            said = json.loads(answer.read())
        scram, banned = said['scram'], said['banned']
        held = [name for name in names if banned.get(name) is not False]  # a name it didn't answer for is held
    except Exception:  # down, slow, or an answer of the wrong shape (re: a list for banned): all the same here
        return "memory-lane didn't say whether anyone is banned or AZ5 is on; nothing merges until it does"
    if scram is not False:
        return 'AZ5 is in force; nothing merges until an admin lifts it'
    if held:
        return f"{', '.join(held)}: banned (or memory-lane didn't say otherwise)"
    return None


def parse_order(text):
    """{field: value} of an order's text: its header, then one 'field: value' line for each of ORDER_FIELDS."""
    lines = text.split('\n')
    if lines[0] != ORDER_HEADER:
        raise Refused('not a merge order')
    found = {}
    for line in lines[1:]:
        if not line:
            continue
        key, sep, value = line.partition(': ')
        if not sep or key not in ORDER_FIELDS + (FOR_FIELD,) or key in found:
            raise Refused(f'an order has one line for each of {", ".join(ORDER_FIELDS)}, and nothing else')
        found[key] = value
    if not set(ORDER_FIELDS) <= set(found):
        raise Refused(f'missing: {", ".join(sorted(set(ORDER_FIELDS) - set(found)))}')
    if (found['by'] == DEVICE_SIGNER) != (FOR_FIELD in found):
        raise Refused(f"an order by {DEVICE_SIGNER} says whose press it was ('for'), and only such an order does")
    if FOR_FIELD in found and (not NAME.fullmatch(found[FOR_FIELD]) or found[FOR_FIELD] == DEVICE_SIGNER):
        raise Refused(f"for: {found[FOR_FIELD]!r} isn't a person's name")
    if not NAME.fullmatch(found['by']) or not NONCE.fullmatch(found['nonce']) or not SHA.fullmatch(found['commit']):
        raise Refused('by, nonce or commit is malformed')
    if not BRANCH.fullmatch(found['branch']) or owned(found['branch']):
        raise Refused(f"{found['branch']}: not a branch that merges (GitHub's own branches don't)")
    if not owned(found['into']) or not BRANCH.fullmatch(found['into']):
        raise Refused(f"{found['into']}: a merge goes into one of {', '.join(OWNED)}")
    try:
        found['expires'] = int(found['expires'])
    except ValueError:
        raise Refused('expires is a unix time') from None
    return found


def signed_by(text, signature, by, signers):
    """Whether `by` signed text, in the merge namespace, by a key the signers file lists for them."""
    if not Path(signers).is_file():
        return False
    with tempfile.TemporaryDirectory() as tmp:
        sig = Path(tmp) / 'order.sig'
        sig.write_text(signature)
        done = subprocess.run(['ssh-keygen', '-Y', 'verify', '-f', str(signers), '-I', by, '-n', MERGE_NAMESPACE,
                               '-s', str(sig)], input=text, capture_output=True, text=True)
    return done.returncode == 0


def may_merge(name, signers):
    """Whether the signers file lists `name` in its own right (a person who may sign merge orders)."""
    try:
        lines = Path(signers).read_text().splitlines()
    except OSError:
        return False
    return any(line.split()[0].split(',').count(name) for line in lines if line.strip() and not line.startswith('#'))


def spend(nonce):
    """The nonce, used now: False if it had been already. Kept with the repositories (/mnt/persist on maybelle),
    so a redeploy can't make an unexpired order good again (re)."""
    spent = ROOT / '.merges' / 'nonces'
    spent.mkdir(parents=True, exist_ok=True)
    try:
        os.close(os.open(spent / nonce, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644))
    except FileExistsError:
        return False
    return True


def merge(text, signature, now=None, signers=None, brakes_url=None):
    """Carry out a signed merge order: {'outcome': 'merged', 'merge': sha, ...} or {'outcome': 'refused', 'why'}.
    Every order, refused or not, is kept as .merges/<nonce>.json for memory-lane to tell the Moods."""
    now = int(time.time() if now is None else now)
    signers = Path(signers or ETC / 'merge_signers')
    order, result, signed = {}, {}, False
    try:
        order = parse_order(text)
        if not signed_by(text, signature, order['by'], signers):
            raise Refused(f"not signed by {order['by']}, or {order['by']} may not merge")
        signed = True
        if not spend(order['nonce']):
            raise Refused('that order was used already')
        person = order.get(FOR_FIELD, order['by'])   # whose merge it is: the presser, for the merge button
        if person != order['by'] and not may_merge(person, signers):
            raise Refused(f'{person} may not merge')
        held = brake([person], url=brakes_url)   # after spending: a braked order isn't good again later
        if held:
            raise Refused(held)
        if order['expires'] < now:
            raise Refused('the order has expired; sign another')
        if order['expires'] > now + ORDER_LIFE_MAX:
            raise Refused(f'an order lasts {ORDER_LIFE_MAX // 3600} hours at most')
        repo = ROOT / f"{order['repo']}.git"
        if not NAME.fullmatch(order['repo']) or not (repo / 'HEAD').exists():
            raise Refused(f"no repository {order['repo']} here")
        result = _merge(repo, order)
    except Refused as e:
        result = {'outcome': 'refused', 'why': str(e)}
    except RuntimeError as e:  # git itself failed (GitHub away mid-fetch, say): nothing pushed
        result = {'outcome': 'refused', 'why': str(e)[:300]}
    record = {**{k: v for k, v in order.items()}, **result, 'at': now, 'order': text, 'signature': signature}
    # Only a signed order is kept: an unsigned one naming a nonce mustn't take the record its signer's will need (re).
    if signed:
        path = ROOT / '.merges' / f"{order['nonce']}.json"
        if not path.exists():  # an order used twice keeps its first record; the second is only logged
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(record, indent=1, sort_keys=True))
            path.chmod(0o644)
    log(f"merge {order.get('repo', '?')}:{order.get('branch', '?')} by {order.get('by', '?')}: "
        f"{result.get('outcome')} {result.get('merge') or result.get('why', '')}")
    return result


def _merge(repo, order):
    branch, commit, into = order['branch'], order['commit'], order['into']
    # The branch's head, as GitHub and this copy have it: it must be the commit signed for.
    there = git(repo, 'ls-remote', 'github', f'refs/heads/{branch}', f'refs/heads/{into}', check=False)
    if there is None:
        raise Refused("GitHub didn't answer; nothing merged")
    heads = {ref[len('refs/heads/'):]: sha for sha, ref in (line.split('\t', 1) for line in there.splitlines())}
    here = refs(repo, 'refs/heads/').get(branch)
    if into not in heads:
        raise Refused(f'{into}: no such branch on GitHub')
    for where, sha in (('GitHub', heads.get(branch)), ('githouse', here)):
        if sha is not None and sha != commit:
            raise Refused(f'{branch} has moved on {where} ({sha[:8]}); sign for what it is now')
    if heads.get(branch) is None and here is None:
        raise Refused(f'{branch}: no such branch')
    wanted = [f'+refs/heads/{into}:refs/heads/{into}'] + ([f'refs/heads/{branch}'] if heads.get(branch) else [])
    git(repo, 'fetch', '--quiet', '--no-tags', 'github', *wanted)
    tip = heads[into]
    if git(repo, 'merge-base', '--is-ancestor', commit, tip, check=False) is not None:
        raise Refused(f'{commit[:8]} is in {into} already')
    tree = subprocess.run(['git', '--git-dir', str(repo), 'merge-tree', '--write-tree', '--no-messages', tip, commit],
                          capture_output=True, text=True)
    if tree.returncode == 1:
        raise Refused(f'{branch} conflicts with {into}; merge {into} into it, push, and sign again')
    if tree.returncode != 0:  # git itself failed, not a conflict: say what it said (re)
        raise Refused(f'merge-tree failed: {tree.stderr.strip()[:200]}')
    person = order.get(FOR_FIELD, order['by'])
    signed = (f"Signed by {order['by']}" if person == order['by'] else
              f"Pressed by {person}, signed by {order['by']}")
    message = (f"Merge {order['repo']}:{branch} ({commit[:8]})\n\n"
               f"{signed} (githouse merge order {order['nonce'][:12]}).\n")
    who = {'GIT_AUTHOR_NAME': person, 'GIT_AUTHOR_EMAIL': f"{person}@githouse",
           'GIT_COMMITTER_NAME': 'githouse', 'GIT_COMMITTER_EMAIL': 'githouse@maybelle'}
    merged = git(repo, 'commit-tree', tree.stdout.split()[0], '-p', tip, '-p', commit, '-m', message, env=who).strip()
    # GitHub first: its branches are GitHub's until the flip, and the minute's fetch would overwrite one made here.
    pushed = subprocess.run(['git', '--git-dir', str(repo), 'push', '--quiet', 'github', f'{merged}:refs/heads/{into}'],
                            capture_output=True, text=True)
    if pushed.returncode != 0:
        raise Refused(f'GitHub refused the merge ({into} moved, or the key can\'t push): {pushed.stderr.strip()[:200]}')
    # Merged now, on GitHub. Here it's only bookkeeping: the minute's fetch would bring it anyway.
    try:
        git(repo, 'update-ref', f'refs/heads/{into}', merged, tip)
        retire(repo)
        publish()
    except RuntimeError as e:
        log(f'{repo.name}: merged {merged[:8]} on GitHub; here, {e}')
    return {'outcome': 'merged', 'merge': merged, 'onto': tip}


# --- orders by mail: memory-lane carries them, githouse checks them ------------------------
# Only Justin has ssh to maybelle (as root); skyler doesn't, and no phone does. So memory-lane keeps
# signed orders, unread, and githouse fetches them every few seconds over the private network and
# carries each out through merge(), signature, nonce and brakes as for any other (Plan, section 8,
# 8 Nov). The signature makes the carrier irrelevant (re): a compromised memory-lane can delay or
# drop an order, never forge one, and a nonce can't be spent twice.

ORDERS_URL = os.environ.get('GITHOUSE_ORDERS', '')   # memory-lane's /api/merge-orders/pending/
ORDERS_EVERY = 5                                     # seconds between looks
ORDERS_AT_ONCE = 20
MERGING = threading.Lock()   # a merge and the minute's fetch don't touch one repository at once
_tried = set()               # orders refused before their nonce was spent (unsigned, say): not tried again
_unheard = None              # why memory-lane didn't answer last time, so it's logged once, not every look


def take_orders(url=None, **given):
    """Carry out every order memory-lane holds that hasn't been here yet: [(order text, result)]."""
    global _unheard
    url = url if url is not None else ORDERS_URL
    if not url:
        return []
    try:
        with urllib.request.urlopen(url, timeout=10) as answer:
            orders = json.loads(answer.read())['orders'][:ORDERS_AT_ONCE]
        _unheard = None
    except Exception as e:
        if str(e) != _unheard:
            log(f'merge orders: memory-lane did not answer ({e})')
        _unheard = str(e)
        return []
    done = []
    for each in orders:
        try:
            text, signature = str(each['order']), str(each['signature'])
        except (KeyError, TypeError):
            continue
        nonce = (re.search(r'^nonce: (\S+)$', text, re.M) or [None, ''])[1]
        seen = hashlib.sha256(f'{text}\0{signature}'.encode()).hexdigest()
        if seen in _tried or (NONCE.fullmatch(nonce) and (ROOT / '.merges' / 'nonces' / nonce).exists()):
            continue  # been here already: merged, refused, or never signed
        with MERGING:
            result = merge(text, signature, **given)   # given: tests' signers and brakes
        _tried.add(seen)
        if not (NONCE.fullmatch(nonce) and (ROOT / '.merges' / f'{nonce}.json').exists()):
            # Refused before it counted as signed, so merge() kept no record under its nonce: keep one under
            # what was mailed, so memory-lane can tell the sender why instead of leaving them waiting.
            kept = ROOT / '.merges' / 'mailed' / f'{seen}.json'
            kept.parent.mkdir(parents=True, exist_ok=True)
            kept.write_text(json.dumps({**result, 'order': text}, indent=1, sort_keys=True))
            kept.chmod(0o644)
        done.append((text, result))
    return done


def orders_loop():
    while True:
        try:
            take_orders()
        except Exception as e:
            log(f'merge orders: {e}')
        time.sleep(ORDERS_EVERY)


def fetch_locked(repo):
    with MERGING:
        return fetch(repo)


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
    """On maybelle: the keys as relayed, then GitHub's branches, every minute; merge orders by mail meanwhile."""
    threading.Thread(target=orders_loop, daemon=True).start()
    paced(fetch_locked, before=write_authorized_keys)


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
    if command == 'merge':  # merge: {"order", "signature"} on stdin, from `magenta.sh merge` over ssh to maybelle
        if os.geteuid() == 0:
            sys.stderr.write('githouse: merge as git (docker exec -u git), so what it writes stays git\'s\n')
            return 2
        try:
            body = json.loads(sys.stdin.read())
            result = merge(str(body['order']), str(body['signature']))
        except (ValueError, KeyError, TypeError):
            result = {'outcome': 'refused', 'why': 'expected {"order", "signature"} on stdin'}
        print(json.dumps(result))
        return 0 if result.get('outcome') == 'merged' else 1
    if command == 'loop':
        loop()
    if command == 'mirror':  # mirror <upstream> <owner/name>...: hunter's copy of maybelle's
        mirror_init(args[2:], args[1])
        paced(mirror)
    sys.stderr.write(__doc__)
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv))
