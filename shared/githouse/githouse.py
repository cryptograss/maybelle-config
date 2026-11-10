#!/usr/bin/env python3
"""githouse: our canonical copies of the repositories, on maybelle, at /srv/git.

The ramp, Phase 1 (Justin, re and magent in #ramp, 30 Oct - 6 Nov 2026;
the plan: Cryptograss:Moods/ramp/Plan, section 3). maybelle is the
integrator: the copy deploys come from lives there, not on hunter, which is
meant to be cheap to lose.

* A bare repository per repository we work on, /srv/git/<name>.git.
* The deploy branches -- main, master, production, release/* -- are
  githouse's since the flip (10 Nov 2026; Plan, section 6): they move only by
  a signed merge here, and GitHub is a mirror they're pushed to. Every 60
  seconds, the repositories staggered across the minute, githouse looks at
  GitHub's (kept as refs/github/<branch>): behind ours, it's sent ours again
  (never forced); ahead of ours or apart from it, someone pushed or merged
  there, around githouse, and it's not taken: it's logged and written to
  github.json, and only root takes it (`githouse adopt <repo> <branch>`, on
  maybelle). A new githouse takes GitHub's deploy branches once; after
  that, a deploy branch's name new on GitHub is said, not taken (adopt). Nobody pushes them here. Pull
  requests' heads are still GitHub's, fetched and pruned to match.
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
  with the next free one offered. Merged means: its tip is in a deploy
  branch's history but not on that branch's own line (first parents) -- a
  merge commit brought it in. A branch just cut from main, with nothing on
  it yet, isn't merged. (Squash and rebase merges break this: merge commits
  only, on every repository here.)
* What's pushed here goes on to GitHub (post-receive) with the deploy key,
  so pull requests and Jenkins keep working; a forward that fails is
  logged, never retried by deleting anything.
* branches.json, beside the repositories: every repository's branches, live,
  merged and closed, for magenta's autocomplete (memory-lane serves it).
* `githouse merge`, on maybelle: a merge without a pull request, ordered by a person and signed with
  their SSH key (Cryptograss:Moods/ramp/Plan, section 8; re's review, 7 Nov). The order names the
  repository, the branch, its head commit and the branch it goes into, with a nonce and an expiry; it
  is signed with `ssh-keygen -Y sign -n magenta-merge`, and githouse checks the signature itself against
  /etc/githouse/merge_signers, which ansible writes with only the people who may merge. memory-lane
  is never in the path. The merge is `--no-ff` of exactly that commit onto githouse's own branch, then
  pushed on to GitHub (a push that fails is sent again each minute; the merge stands here), and each
  order and what came of it are kept in /srv/git/.merges, its nonce spent whether it merged or not.
  Orders arrive by mail: memory-lane keeps them, unread, and githouse fetches them every few seconds and
  checks each itself (8 Nov: only Justin has ssh to maybelle). Brakes stop it (section 10): root's freeze (/etc/githouse/frozen), AZ5, or a ban on the signer, the
  last two asked of memory-lane at every order. Its answer can only stop a merge; no answer stops it too.
* A push that carries a secret is refused, before anything else holds a copy (Skyler and Justin, #ramp,
  10 Nov; re's design): any value from the vault, exactly, as the scrubber has them (/etc/githouse/
  secrets.json, readable by git alone), in an added line or a commit message; or anything shaped like a
  private key or a known service's token. A line ending 'githouse: not a secret' passes the shapes
  (a redaction test needs them), never the vault's values. `githouse scan` looks through all of history.
* After a push reaches GitHub, Jenkins' branch-tests job tests exactly that commit, on an agent with no
  secrets (Plan, section 11): /etc/githouse/jenkins.json says where, jenkins_reporter as whom.
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
OWNED = ('main', 'master', 'production', 'release/*')  # the deploy branches: githouse's since the flip
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
    """Whether a branch is a deploy branch (moved only by a merge here, never pushed here)."""
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
        else:  # on GitHub now, where the branches agent clones from: test what was pushed
            for old, new, ref in updates:
                branch = ref[len('refs/heads/'):]
                if new != ZERO and ref.startswith('refs/heads/') and not owned(branch):
                    test_branch(repo.name[:-len('.git')], branch, new)
    publish()


# --- secrets: refused at the door ---------------------------------------------------------------
# Every push comes here first; within a minute hunter's mirror (every container) and GitHub (everyone) have
# it, and a secret can't be taken back from either. So it's refused here, in pre-receive.

SECRETS_FILE = 'secrets.json'    # /etc/githouse/secrets.json: the vault's string values, the scrubber's list
SECRET_MIN = 12                  # shorter values ("admin", a port) are everywhere in code
NOT_A_SECRET = 'githouse: not a secret'
SHAPES = {
    'a private key': re.compile(r'-----BEGIN (?:[A-Z]+ )?PRIVATE KEY(?: BLOCK)?-----'),
    'an AWS access key': re.compile(r'\b(?:AKIA|ASIA)[0-9A-Z]{16}\b'),
    'a GitHub token': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,})\b'),
    'an Anthropic key': re.compile(r'\bsk-ant-[A-Za-z0-9_-]{20,}'),
    'an OpenAI key': re.compile(r'\bsk-(?:proj-)?[A-Za-z0-9]{32,}\b'),
    'a Slack token': re.compile(r'\bxox[abposr]-[A-Za-z0-9-]{10,}'),
    'a Google API key': re.compile(r'\bAIza[0-9A-Za-z_-]{35}\b'),
    'a Stripe live key': re.compile(r'\b[sr]k_live_[0-9A-Za-z]{20,}'),
    'a JWT (Pinata\'s keys are these)': re.compile(r'\beyJhbGciOi[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}'),
}


def secret_values(etc=None):
    """The vault's values worth looking for: each at least SECRET_MIN long; a value of many lines by its
    longest line (a key file). Longest first. None at all if the file isn't there (a fresh githouse)."""
    try:
        values = json.loads((Path(etc or ETC) / SECRETS_FILE).read_text())
    except (OSError, ValueError):
        return []
    found = set()
    for value in values if isinstance(values, list) else []:
        if isinstance(value, str):
            line = max(value.strip().splitlines() or [''], key=len).strip()
            if len(line) >= SECRET_MIN:
                found.add(line)
    return sorted(found, key=len, reverse=True)


def secret_in(line, values):
    """What secret this line carries, said without it ('a value from the vault, 40 characters'), or None."""
    for value in values:
        if value in line:
            return f'a value from the vault, {len(value)} characters'
    if NOT_A_SECRET in line:
        return None
    for name, shape in SHAPES.items():
        if shape.search(line):
            return name
    return None


def secrets_in_log(text, values):
    """[(commit, where, what)] in `git log -p -U0 --format=commit %H%n%B` output: added lines and messages."""
    found, commit, where, header = [], '', '', False
    for line in text.splitlines():
        if line.startswith('commit ') and SHA.fullmatch(line[7:]):
            commit, where, header = line[7:], 'its message', True
            continue
        if line.startswith('diff --git '):
            header = False
            continue
        if line.startswith('+++ '):
            where = line[6:] if line.startswith('+++ b/') else line[4:]
            continue
        if header or (line.startswith('+') and not line.startswith('+++')):
            what = secret_in(line if header else line[1:], values)
            if what:
                found.append((commit, where, what))
    return found


def _log(repo, *revs):
    return git(repo, 'log', '-p', '-U0', '--no-color', '--no-ext-diff', '--no-merges', '--format=commit %H%n%B', *revs)


def check_secrets(repo, updates, etc=None):
    """What's refused in a push for a secret it carries: [message]. Only commits new to githouse: a secret
    already in history (found by scan, rotated by a person) doesn't stop every push after it."""
    values, refused = secret_values(etc), []
    for old, new, ref in updates:
        if new == ZERO:
            continue
        for commit, where, what in secrets_in_log(_log(repo, new, '--not', '--all'), values):
            line = f'{commit[:8]} {where}: {what}. Take it out of the commit (amend or rebase) and push again'
            if not what.startswith('a value from the vault'):
                line += f"; if it isn't a secret (a test's fake), end its line with '{NOT_A_SECRET}'"
            if line not in refused:
                refused.append(line)
    return refused


def scan(repo, etc=None):
    """All of a repository's history, every ref: [(commit, where, what)]. For root, once, and after a leak."""
    return secrets_in_log(_log(repo, '--all'), secret_values(etc))


# --- branch tests on Jenkins (Plan, section 11) ----------------------------------------------------------
# After a push reaches GitHub, Jenkins' branch-tests job is asked to test exactly that commit, on the
# `branches` agent. As the reporter, who may only read and start builds; its password in /etc/githouse,
# since a hook runs under sshd's forced command with none of the container's environment (re, 6 Nov).

def test_branch(repo, branch, commit, etc=None):
    """Ask Jenkins to test this commit; True if it took the request. Never raises: a push mustn't fail on it."""
    import http.cookiejar
    etc = Path(etc or ETC)
    try:
        jenkins = json.loads((etc / 'jenkins.json').read_text())
        password = (etc / 'jenkins_reporter').read_text().strip()
    except (OSError, ValueError):
        return False  # not set up here: nothing to ask
    base, user = jenkins['url'].rstrip('/'), jenkins.get('user', 'reporter')
    import base64
    auth = 'Basic ' + base64.b64encode(f'{user}:{password}'.encode()).decode()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    try:
        # Jenkins wants a crumb with a password, tied to the session the cookie carries.
        asked = urllib.request.Request(f'{base}/crumbIssuer/api/json', headers={'Authorization': auth})
        with opener.open(asked, timeout=10) as answer:
            crumb = json.loads(answer.read())
        query = urllib.parse.urlencode({'REPO': repo, 'BRANCH': branch, 'COMMIT': commit})
        build = urllib.request.Request(f'{base}/job/branch-tests/buildWithParameters?{query}', data=b'', method='POST',
                                       headers={'Authorization': auth, crumb['crumbRequestField']: crumb['crumb']})
        with opener.open(build, timeout=10):
            return True
    except Exception as e:
        log(f'{repo}:{branch}: Jenkins didn\'t take the branch test ({e})')
        return False


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
    """Pull requests' heads, as GitHub has them now, and GitHub's deploy branches, as refs/github/<branch>;
    then each deploy branch squared with GitHub's (reconcile), and what merged retired. True if anything
    changed here."""
    listed = git(repo, 'ls-remote', 'github', 'refs/heads/*', 'refs/pull/*/head')
    there, source = {}, {}
    for line in listed.splitlines():
        sha, ref = line.split('\t', 1)
        if ref.startswith('refs/pull/'):
            there[ref], source[ref] = sha, ref
        elif owned(ref[len('refs/heads/'):]):
            mine = 'refs/github/' + ref[len('refs/heads/'):]
            there[mine], source[mine] = sha, ref
    here = {f'refs/github/{b}': s for b, s in refs(repo, 'refs/github/').items()}
    here.update({f'refs/pull/{n}': s for n, s in refs(repo, 'refs/pull/').items()})
    fetched = there != here
    if fetched:
        wanted = [f'+{source[ref]}:{ref}' for ref, sha in there.items() if here.get(ref) != sha]
        for start in range(0, len(wanted), 200):
            git(repo, 'fetch', '--quiet', '--no-tags', 'github', *wanted[start:start + 200])
        for ref in set(here) - set(there):  # gone from GitHub: GitHub's view of it goes too, nothing of ours
            git(repo, 'update-ref', '-d', ref)
    taken = reconcile(repo)
    # A clone checks out the default branch GitHub names, if it's one of ours.
    default = re.search(r'^ref: refs/heads/(\S+)\tHEAD$', git(repo, 'ls-remote', '--symref', 'github', 'HEAD'), re.M)
    if default and owned(default.group(1)) and default.group(1) in refs(repo, 'refs/heads/'):
        git(repo, 'symbolic-ref', 'HEAD', f'refs/heads/{default.group(1)}')
    if fetched or taken:
        retire(repo)
    publish()
    return fetched or taken


# Where GitHub's deploy branches differ from githouse's and stay that way: {repo: {branch: {...}}}, written to
# github.json for memory-lane; and what's been logged already, so a standing difference is said once.
DIFFERS = {}
_SAID = {}


def reconcile(repo):
    """Each deploy branch, githouse's against GitHub's (refs/github/), after the flip (Plan, section 6):
    * the same: nothing.
    * GitHub's behind ours, or gone: ours is pushed to it, never forced (a forward that failed, sent again).
    * GitHub's ahead of ours, or apart: pushed or merged on GitHub, around githouse. Not taken, not
      overwritten: logged once, and in github.json, for a person to look at; root takes it with adopt.
    * none here yet: taken from GitHub only if githouse has no deploy branch in this repository at all (a new
      githouse); a new name made on GitHub (a release/*) is said, like ahead, and root adopts it.
    True if a branch here changed."""
    ours = {b: s for b, s in refs(repo, 'refs/heads/').items() if owned(b)}
    theirs = refs(repo, 'refs/github/')
    differs, changed = {}, False
    for branch in sorted(set(ours) | set(theirs)):
        mine, gh = ours.get(branch), theirs.get(branch)
        if mine == gh:
            continue
        if mine is None:
            # Taken from GitHub only by a githouse with no deploy branch here at all (a new one). A new name made
            # on GitHub (release/x; master where main deploys) would be a base retire() reads, unsigned (re):
            # it's said, and root takes it with adopt.
            if not ours:
                if git(repo, 'update-ref', f'refs/heads/{branch}', gh, ZERO, check=False) is not None:
                    log(f'{repo.name}: {branch} taken from GitHub at {gh[:8]}: githouse had none')
                    changed = True
                continue
            differs[branch] = {'here': None, 'github': gh, 'state': 'new',
                               'why': 'made on GitHub, not here: not taken (root: githouse adopt)'}
            _say(repo, branch, ('new', gh), f'{branch}: new on GitHub at {gh[:8]}; not taken (root: githouse adopt)')
            continue
        if gh is None or _ancestor(repo, gh, mine):
            pushed = subprocess.run(['git', '--git-dir', str(repo), 'push', '--quiet', 'github',
                                     f'{mine}:refs/heads/{branch}'], capture_output=True, text=True)
            if pushed.returncode == 0:
                git(repo, 'update-ref', f'refs/github/{branch}', mine)
                _say(repo, branch, None, f'{branch} sent to GitHub at {mine[:8]}')
                continue
            state, why = 'behind', f'GitHub refused it: {pushed.stderr.strip()[:200]}'
        elif _ancestor(repo, mine, gh):
            state, why = 'ahead', 'pushed or merged on GitHub, not here: not taken (root: githouse adopt)'
        else:
            state, why = 'apart', 'GitHub and githouse have each moved: not taken (a person looks)'
        differs[branch] = {'here': mine, 'github': gh, 'state': state, 'why': why}
        _say(repo, branch, (state, gh, mine), f'{branch}: GitHub {state} at {(gh or "none")[:8]}, '
                                              f'githouse at {mine[:8]}; {why}')
    for branch in set(_SAID.get(repo.name, {})) - set(differs):
        _SAID[repo.name].pop(branch, None)
    DIFFERS[repo.name[:-4]] = differs
    return changed


def _say(repo, branch, key, line):
    """Log a line about a branch's difference with GitHub once, until it changes (key None: say it, and forget)."""
    said = _SAID.setdefault(repo.name, {})
    if key is None:
        said.pop(branch, None)
        log(f'{repo.name}: {line}')
    elif said.get(branch) != key:
        said[branch] = key
        log(f'{repo.name}: {line}')


def adopt(repo, branch):
    """Root's way to take GitHub's deploy branch after someone moved it there: only as a fast-forward of
    githouse's, and only to what GitHub has now. Returns the commits taken, oldest first."""
    if not owned(branch):
        raise Refused(f'{branch} is not a deploy branch')
    git(repo, 'fetch', '--quiet', '--no-tags', 'github', f'+refs/heads/{branch}:refs/github/{branch}')
    gh = refs(repo, 'refs/github/').get(branch)
    mine = refs(repo, 'refs/heads/').get(branch)
    if gh is None:
        raise Refused(f'GitHub has no {branch}')
    if mine == gh:
        return []
    if mine is not None and not _ancestor(repo, mine, gh):
        raise Refused(f'GitHub\'s {branch} ({gh[:8]}) is not ahead of githouse\'s ({mine[:8]}): '
                      f'they\'ve each moved; merge GitHub\'s into a branch of your own and sign it')
    taken = git(repo, 'rev-list', '--reverse', '--oneline', gh if mine is None else f'{mine}..{gh}').splitlines()
    git(repo, 'update-ref', f'refs/heads/{branch}', gh, mine or ZERO)
    log(f'{repo.name}: root adopted GitHub\'s {branch} at {gh[:8]} ({len(taken)} commits)')
    reconcile(repo)
    retire(repo)
    publish()
    return taken


def retire(repo):
    """Move every pushed branch that a merge brought into a deploy branch to refs/merged/."""
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
        raise Refused(f"{found['branch']}: not a branch that merges (the deploy branches don't)")
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
    # The target is githouse's own deploy branch (the flip): GitHub's is only a mirror of it.
    tip = refs(repo, 'refs/heads/').get(into)
    if tip is None:
        raise Refused(f'{into}: no such branch here')
    # The branch's head, as GitHub and this copy have it: it must be the commit signed for. A branch
    # pushed here has GitHub's copy forwarded; one only on GitHub (a pull request's) is fetched from there.
    there = git(repo, 'ls-remote', 'github', f'refs/heads/{branch}', check=False)
    on_github = (there or '').split('\t', 1)[0] or None
    here = refs(repo, 'refs/heads/').get(branch)
    if there is None and here is None:
        raise Refused(f"GitHub didn't answer, and githouse has no {branch}; nothing merged")
    for where, sha in (('GitHub', on_github), ('githouse', here)):
        if sha is not None and sha != commit:
            raise Refused(f'{branch} has moved on {where} ({sha[:8]}); sign for what it is now')
    if on_github is None and here is None:
        raise Refused(f'{branch}: no such branch')
    if here is None:
        git(repo, 'fetch', '--quiet', '--no-tags', 'github', f'refs/heads/{branch}')
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
    # Here first: githouse's branch is the one deploys come from. Only if it's still where we read it.
    if git(repo, 'update-ref', f'refs/heads/{into}', merged, tip, check=False) is None:
        raise Refused(f'{into} moved here meanwhile; sign again')
    result = {'outcome': 'merged', 'merge': merged, 'onto': tip}
    # Then GitHub, the mirror. A refusal there doesn't undo the merge: the minute's reconcile sends it again,
    # or, if GitHub's moved on its own, says so.
    pushed = subprocess.run(['git', '--git-dir', str(repo), 'push', '--quiet', 'github', f'{merged}:refs/heads/{into}'],
                            capture_output=True, text=True)
    if pushed.returncode == 0:
        git(repo, 'update-ref', f'refs/github/{into}', merged, check=False)
    else:
        result['github'] = f'not yet: {pushed.stderr.strip()[:200]}'
        log(f'{repo.name}: merged {merged[:8]} into {into}; GitHub refused it for now: {pushed.stderr.strip()[:200]}')
    try:
        retire(repo)
        publish()
    except RuntimeError as e:
        log(f'{repo.name}: merged {merged[:8]}; {e}')
    return result


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
    # github.json: where GitHub's deploy branches differ from githouse's and haven't been squared (reconcile).
    path = ROOT / 'github.json'
    temp = path.with_suffix('.json.tmp')
    temp.write_text(json.dumps({name: d for name, d in DIFFERS.items() if d}, indent=1, sort_keys=True))
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
    """On maybelle: the keys as relayed, then each repository squared with GitHub, every minute; merge orders by
    mail meanwhile."""
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
            if not refused:
                refused = check_secrets(repo, updates)
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
    if command == 'scan':  # scan [repo...]: every repository's whole history, for a secret it carries
        names = args[1:] or [r.name[:-4] for r in sorted(ROOT.glob('*.git'))]
        found = 0
        for name in names:
            for commit, where, what in scan(ROOT / f'{name}.git'):
                print(f'{name} {commit[:8]} {where}: {what}')
                found += 1
        print(f'{found} found, in {len(names)} repositories' + ('' if secret_values() else
              f' (no {SECRETS_FILE} here: shapes only, not the vault\'s values)'))
        return 1 if found else 0
    if command == 'adopt':  # adopt <repo> <branch>: root takes GitHub's deploy branch, as a fast-forward only
        if os.geteuid() == 0:
            sys.stderr.write('githouse: adopt as git (docker exec -u git), so what it writes stays git\'s\n')
            return 2
        if len(args) != 3 or not NAME.fullmatch(args[1]) or not (ROOT / f'{args[1]}.git' / 'HEAD').exists():
            sys.stderr.write('githouse: adopt <repo> <branch>, a repository here\n')
            return 2
        try:
            with MERGING:
                taken = adopt(ROOT / f'{args[1]}.git', args[2])
        except (Refused, RuntimeError) as e:
            sys.stderr.write(f'githouse: {e}\n')
            return 1
        print('\n'.join(taken) if taken else f'{args[2]} is already GitHub\'s; nothing taken')
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
