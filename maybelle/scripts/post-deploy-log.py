#!/usr/bin/env python3
"""Put a deploy's log on PickiPedia, as DrivingThatTrain.

    post-deploy-log.py <server> <finished|failed> <log file> --vault-password-file F
                       [--by WHO] [--took SECONDS] [--dry-run]

Run by each deploy script on maybelle when its ansible run ends. The page
is ``Cryptograss:<Server>/deploy_logs/<block>``: for a failed deploy, the
whole log; for one that finished, a summary -- the play recap, the tasks
that changed something, the warnings, and the log's last lines.

Nothing secret goes up. While a deploy runs its vault password is on
maybelle, so this opens the vault (secrets/vault.yml) and takes every
value in it out of the log by exact match, named: ``[vault: some_key]``.
Then the shapes memory-lane's redaction knows (tokens with known prefixes,
NAME=secret, credentials in URLs, Authorization headers, --password X) are
taken out too, for anything the vault doesn't hold. The page says how much
was taken out of each kind.

The bot's own login comes from the same vault -- DrivingThatTrain_Bot_Username
(``DrivingThatTrain@deploy-logs``, a BotPassword) and DrivingThatTrain_Bot_Password
-- and is never written to disk. Its edits are bot edits: out of recent
changes, and out of the Moods' wiki feed.

This never fails a deploy: whatever goes wrong, it says so and exits 0.
Prints the page's URL when it posts it (the deploy scripts pass that on to
the Moods).
"""

import argparse
import html
import http.cookiejar
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent.parent
VAULT = REPO_DIR / 'secrets' / 'vault.yml'
WIKI_URL = os.environ.get('WIKI_URL', 'https://pickipedia.xyz')
USER_AGENT = 'DrivingThatTrain/1.0 (deploy logs; +https://pickipedia.xyz/wiki/User:DrivingThatTrain)'
PAGES = {'hunter': 'Cryptograss:Hunter', 'maybelle': 'Cryptograss:Maybelle',
         'delivery-kid': 'Cryptograss:Delivery-kid', 'pickipedia': 'Cryptograss:PickiPedia'}
MAX_LOG_CHARS = 1_500_000   # a wiki page holds 2 MB; a log longer than this keeps its start and its end
TAIL_LINES = 40             # a finished deploy's summary ends with this much of the log
MIN_VAULT_VALUE = 6         # shorter vault values aren't secrets worth the false matches
# Nor is a plain lowercase word -- 'localhost', 'pickipedia' -- a host or a database's name,
# which a deploy log says on every line. A secret has a digit, a capital, a space or a symbol.
PLAIN_WORD = re.compile(r'^[a-z][a-z_.-]{0,23}$')

# Block heights name the pages. As post-audit-to-wiki.py learned (pickipedia#112):
# ask the chain, and if it won't answer, extrapolate from a recent verified
# block at the realised 12.044 s, never from the Merge at 12.
ETH_RPC_URL = 'https://ethereum-rpc.publicnode.com'
ANCHOR_BLOCK = 25000000
ANCHOR_TIMESTAMP = 1777637363  # block 25,000,000 -- 2026-05-01T12:09:23Z
SECONDS_PER_BLOCK = 12.044


# --- what goes on the page --------------------------------------------------

# memory-lane's conversations/services/redaction.py, the patterns that apply
# to a deploy log. Keep the two in step.
_TOKENS = [
    r'-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----',
    r'\bgh[pousr]_[A-Za-z0-9]{36,}\b',
    r'\bgithub_pat_[A-Za-z0-9_]{50,}\b',
    r'\bsk-ant-[A-Za-z0-9_-]{20,}',
    r'\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}',
    r'\b(?:AKIA|ASIA)[0-9A-Z]{16}\b',
    r'\bxox[abposr]-[A-Za-z0-9-]{10,}',
    r'\b(?:sk|rk)_live_[A-Za-z0-9]{16,}',
    r'\bAIza[0-9A-Za-z_-]{35}\b',
    r'\bnpm_[A-Za-z0-9]{36}\b',
    r'\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}',
]
TOKEN = re.compile('|'.join(f'(?:{t})' for t in _TOKENS), re.S)
_SECRET_NAME = (r'(?<![A-Za-z0-9_.-])[A-Za-z0-9_.-]{0,40}?(?:secret|token|passw(?:or)?d|passwd|api[_-]?key|private[_-]?key'
                r'|access[_-]?key|credentials?|mnemonic|seed[_-]?phrase)(?![a-z])(?:[_.-][A-Za-z0-9_.-]{0,40})?')
ASSIGNMENT = re.compile(
    rf'(?i)(?P<name>["\']?{_SECRET_NAME}["\']?\s*[:=]\s*["\']?)(?P<value>[^\s"\'\\,;(){{}}\[\]]{{4,}})(?=$|[\s"\'\\,;)}}\]])')
URL_CREDENTIALS = re.compile(r'(?P<head>\b[a-z][a-z0-9+.-]*://[^\s:/@"\']+:)(?P<value>[^\s@/"\']+)(?=@)')
FLAG = re.compile(
    r'(?P<head>(?<![\w-])--?[a-z0-9-]{0,30}(?:password|passwd|secret|token|private-key|privkey|api-key|apikey|access-key)'
    r'(?![a-z])[a-z0-9-]{0,30}(?:\s+|=))(?P<value>(?!-)[^\s"\'\\,;`]{4,})', re.I)
AUTH_HEADER = re.compile(r'(?i)(?P<head>\bauthorization\s*[:=]\s*["\']?(?:bearer|basic|token)\s+)(?P<value>[A-Za-z0-9._~+/=-]{8,})')
_LOOKS_SECRET = re.compile(r'[0-9+/=@#$%^&*!-]|^(?=.*[a-z])(?=.*[A-Z]).{16,}$')
_CODE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$')
_PLACEHOLDER = re.compile(r'^(?:[0-9.:]+|[/~].*|\./.*|your[-_].*|.*example.*|.*placeholder.*|\{\{.*|\$\{?.*|<.*|%.*|\*+|x+'
                          r'|\.\.\.|changeme|password|secret|token|none|null|true|false|redacted.*|\[redacted\].*'
                          r'|\[vault: .*)$', re.I)
MARK = '[REDACTED]'
ANSI = re.compile(r'\x1b\[[0-9;]*[A-Za-z]')


def vault_values(vault_password_file):
    """{secret value: its name}, every string in the vault worth matching, longest first."""
    shown = subprocess.run(['ansible-vault', 'view', str(VAULT), '--vault-password-file', str(vault_password_file)],
                           capture_output=True, text=True, timeout=60, check=True).stdout
    import yaml
    values = {}

    def walk(name, value):
        if isinstance(value, dict):
            for key, inner in value.items():
                walk(f'{name}.{key}' if name else str(key), inner)
        elif isinstance(value, list):
            for i, inner in enumerate(value):
                walk(f'{name}[{i}]', inner)
        elif isinstance(value, str):
            for piece in [value.strip()] + [line.strip() for line in value.splitlines()]:
                # A multi-line secret (a key file) line by line too.
                if len(piece) >= MIN_VAULT_VALUE and not PLAIN_WORD.match(piece):
                    values.setdefault(piece, name)
    walk('', yaml.safe_load(shown) or {})
    return dict(sorted(values.items(), key=lambda kv: -len(kv[0]))), yaml.safe_load(shown) or {}


def redact(text, secrets):
    """(text with secrets taken out, {kind: how many, 'names': {vault name: how many}})."""
    counts = {'vault': 0, 'pattern': 0, 'names': {}}
    for value, name in secrets.items():
        if value in text:
            n = text.count(value)
            counts['vault'] += n
            counts['names'][name] = counts['names'].get(name, 0) + n
            text = text.replace(value, f'[vault: {name}]')

    def token(match):
        counts['pattern'] += 1
        return MARK

    def keep(match):
        value = match.group('value')
        if (_PLACEHOLDER.match(value) or value.startswith(MARK) or not _LOOKS_SECRET.search(value)
                or (_CODE.match(value) and not re.search(r'[0-9]', value))):
            return match.group(0)
        counts['pattern'] += 1
        return match.group(1) + MARK
    text = TOKEN.sub(token, text)
    for pattern in (AUTH_HEADER, URL_CREDENTIALS, FLAG, ASSIGNMENT):
        text = pattern.sub(keep, text)
    return text, counts


def summary(log):
    """What a finished deploy did, from ansible's own lines."""
    lines = log.splitlines()
    recap, changed, warnings, task = [], [], [], None
    in_recap = False
    for line in lines:
        if line.startswith('PLAY RECAP'):
            in_recap = True
            continue
        if in_recap:
            if re.match(r'^\S+\s+:\s+ok=', line):
                recap.append(re.sub(r'\s+', ' ', line.strip()))
                continue
            in_recap = False
        found = re.match(r'^(?:TASK|RUNNING HANDLER) \[(.+)\] \*', line)
        if found:
            task = found.group(1)
        elif line.startswith('changed:') and task and task not in changed:
            changed.append(task)
        elif line.startswith('[WARNING]') and line not in warnings:
            warnings.append(line)
    return recap, changed, warnings, lines[-TAIL_LINES:]


def failures(log):
    """[(task, its fatal line)]: where a failed deploy stopped."""
    task, out = None, []
    for line in log.splitlines():
        found = re.match(r'^(?:TASK|RUNNING HANDLER) \[(.+)\] \*', line)
        if found:
            task = found.group(1)
        elif re.match(r'^(?:fatal|failed): \[', line):
            out.append((task or '(before any task)', line[:600]))
    return out


def pre(text):
    """Text that shows as itself in a <pre> block."""
    return '<pre>' + html.escape(text, quote=False) + '</pre>'


def page(server, state, block, log, counts, by='', took=None, commit='', when=None):
    when = when or time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())
    ok = state == 'finished'
    how_long = f', in {took // 60}m {took % 60:02d}s' if took is not None else ''
    head = [f"'''{'✓' if ok else '✗'} {server} {'redeployed' if ok else 'redeploy failed'}''' "
            f"at block {block:,} ({when}){how_long}"
            + (f', by {by}' if by else '') + (f', maybelle-config <code>{commit}</code>' if commit else '') + '.',
            '',
            f"Posted by DrivingThatTrain. Taken out before posting: {counts['vault']} vault "
            f"value{'s' if counts['vault'] != 1 else ''}, by exact match, and {counts['pattern']} "
            f"secret-shaped string{'s' if counts['pattern'] != 1 else ''}."
            + (' The vault values: ' + ', '.join(f'<code>{html.escape(n)}</code> ×{c}' for n, c in
                                                sorted(counts.get('names', {}).items(), key=lambda nc: -nc[1])) + '.'
               if counts.get('names') else ''),
            '']
    if ok:
        recap, changed, warnings, tail = summary(log)
        head += ['== Recap ==', pre('\n'.join(recap) or '(no play recap in the log)'), '',
                 f'== Changed ({len(changed)}) ==']
        head += [f'* {html.escape(t, quote=False)}' for t in changed] or ['Nothing changed.']
        if warnings:
            head += ['', f'== Warnings ({len(warnings)}) ==', pre('\n'.join(warnings))]
        head += ['', f'== The last {len(tail)} lines ==', pre('\n'.join(tail))]
    else:
        stopped = failures(log)
        if stopped:
            head += ['== Where it stopped ==']
            head += [f'* {html.escape(task, quote=False)}' for task, _ in stopped]
            head += [pre('\n'.join(line for _, line in stopped)), '']
        if len(log) > MAX_LOG_CHARS:
            keep = MAX_LOG_CHARS // 5
            log = (log[:keep] + f'\n\n[... {len(log) - MAX_LOG_CHARS:,} characters left out here; '
                   f'the whole log is on maybelle ...]\n\n' + log[-(MAX_LOG_CHARS - keep):])
        head += ['== The whole log ==', pre(log)]
    head += ['', f'[[Category:Deploy logs]] [[Category:{server} deploy logs]]']
    return '\n'.join(head) + '\n'


# --- where it goes ------------------------------------------------------------

def current_block():
    try:
        request = urllib.request.Request(
            ETH_RPC_URL, data=json.dumps({'jsonrpc': '2.0', 'method': 'eth_blockNumber', 'params': [], 'id': 1}).encode(),
            headers={'Content-Type': 'application/json', 'User-Agent': USER_AGENT})
        with urllib.request.urlopen(request, timeout=5) as answer:
            block = int(json.loads(answer.read().decode())['result'], 16)
        if block > ANCHOR_BLOCK:
            return block
        print(f'  [eth rpc gave an implausible block {block}; estimating]', file=sys.stderr)
    except Exception as e:
        print(f'  [eth rpc unavailable ({type(e).__name__}); estimating]', file=sys.stderr)
    return ANCHOR_BLOCK + round((time.time() - ANCHOR_TIMESTAMP) / SECONDS_PER_BLOCK)


class Wiki:
    """The MediaWiki action API, signed in with a BotPassword. Standard library only."""

    def __init__(self, url=WIKI_URL):
        self.api = url.rstrip('/') + '/api.php'
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(self, post=False, **params):
        params['format'] = 'json'
        data = urllib.parse.urlencode(params).encode()
        request = (urllib.request.Request(self.api, data=data, headers={'User-Agent': USER_AGENT}) if post else
                   urllib.request.Request(f'{self.api}?{data.decode()}', headers={'User-Agent': USER_AGENT}))
        with self.opener.open(request, timeout=60) as answer:
            body = json.loads(answer.read().decode())
        if 'error' in body:
            raise RuntimeError(f"wiki: {body['error'].get('code')}: {body['error'].get('info')}")
        return body

    def login(self, username, password):
        token = self.call(action='query', meta='tokens', type='login')['query']['tokens']['logintoken']
        result = self.call(post=True, action='login', lgname=username, lgpassword=password, lgtoken=token)['login']
        if result.get('result') != 'Success':
            raise RuntimeError(f"wiki login: {result.get('result')}: {result.get('reason', '')}")

    def create(self, title, text, summary):
        """Make the page; if it exists already, the next free '<title>-2', '-3'... The title made."""
        token = self.call(action='query', meta='tokens')['query']['tokens']['csrftoken']
        for n in range(1, 10):
            attempt = title if n == 1 else f'{title}-{n}'
            try:
                self.call(post=True, action='edit', title=attempt, text=text, summary=summary,
                          bot='1', createonly='1', token=token)
                return attempt
            except RuntimeError as e:
                if 'articleexists' not in str(e):
                    raise
        raise RuntimeError(f'{title} and the next eight are all taken')


AWAIT_FOR = 4 * 3600  # seconds a poster started early waits to be told how the deploy went


def awaited(path):
    """{'state', 'took'}, once the deploy script writes it to `path` (then gone); None if it never does."""
    deadline = time.time() + AWAIT_FOR
    while time.time() < deadline:
        try:
            told = json.loads(Path(path).read_text())
            Path(path).unlink(missing_ok=True)
            return told
        except (FileNotFoundError, ValueError):
            time.sleep(1)
    return None


def post(args):
    # The vault first: a maybelle deploy's playbook removes its password at the end,
    # so there the poster starts before it (--await), keeping what it reads in memory.
    secrets, vault = vault_values(args.vault_password_file)
    if args.await_file:
        told = awaited(args.await_file)
        if told is None:
            raise RuntimeError(f'never told how the deploy went ({args.await_file})')
        args.state, args.took = told['state'], told.get('took')
    log = ANSI.sub('', Path(args.log).read_text(errors='replace'))
    log, counts = redact(log, secrets)
    commit = subprocess.run(['git', '-C', str(REPO_DIR), 'rev-parse', '--short=12', 'HEAD'],
                            capture_output=True, text=True).stdout.strip()
    block = current_block()
    title = f'{PAGES[args.server]}/deploy_logs/{block}'
    text = page(args.server, args.state, block, log, counts, by=args.by, took=args.took, commit=commit)
    if args.dry_run:
        print(f'--- {title} ---\n{text}')
        return
    username, password = vault.get('DrivingThatTrain_Bot_Username'), vault.get('DrivingThatTrain_Bot_Password')
    if not (username and password):
        print('⚠ No DrivingThatTrain_Bot_Username / _Password in the vault; deploy log not posted')
        tell(args, 'log not posted: no DrivingThatTrain_Bot_Username / _Password in the vault')
        return
    wiki = Wiki()
    for attempt in range(3):  # a pickipedia deploy may still be settling
        try:
            wiki.login(username, password)
            made = wiki.create(title, text, f'{args.server} {args.state}: deploy log')
            break
        except Exception as e:
            if attempt == 2:
                raise
            print(f'  [wiki not ready ({type(e).__name__}); trying again in 20 s]', file=sys.stderr)
            time.sleep(20)
    url = f"{WIKI_URL}/wiki/{urllib.parse.quote(made.replace(' ', '_'), safe=':/')}"
    print(f'✓ Deploy log: {url}')
    tell(args, url)


def tell(args, note):
    """What the Moods are told with the deploy's report (its note): the page, or why there isn't one."""
    if args.url_file:
        Path(args.url_file).write_text(redact(note, {})[0][:200])


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('server', choices=sorted(PAGES))
    parser.add_argument('state', choices=('finished', 'failed', 'later'), help="'later' with --await")
    parser.add_argument('log')
    parser.add_argument('--vault-password-file', required=True)
    parser.add_argument('--by', default='')
    parser.add_argument('--took', type=int)
    parser.add_argument('--url-file', help="write the posted page's URL here -- or why it wasn't posted -- for the "
                                           "deploy script to pass on to the Moods")
    parser.add_argument('--await', dest='await_file', help='read the vault now, then wait for {"state", "took"} in '
                                                          'this file before posting (for a playbook that removes '
                                                          'its vault password as it ends: maybelle)')
    parser.add_argument('--dry-run', action='store_true', help='print the page instead of posting it')
    args = parser.parse_args()
    try:
        post(args)
    except Exception as e:
        print(f'⚠ Deploy log not posted ({why(e)})')
        tell(args, f'log not posted: {why(e)}')  # in the Mood's deploy line: no terminal needed to know
    return 0


def why(e):
    """What went wrong, for the terminal running the deploy -- never the vault's text: a YAML
    error quotes the line it failed on, so that one is told by its type alone."""
    if type(e).__module__.startswith('yaml'):
        return type(e).__name__
    detail = str(e)
    if isinstance(e, subprocess.CalledProcessError):
        last = (e.stderr or '').strip().splitlines()[-1:]  # ansible-vault's own reason, e.g. a wrong password
        detail = f"{e.cmd[0]} exited {e.returncode}" + (f': {last[0]}' if last else '')
    return f'{type(e).__name__}: {detail}'[:300]


if __name__ == '__main__':
    sys.exit(main())
