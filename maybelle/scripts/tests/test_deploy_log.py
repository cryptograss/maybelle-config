"""A deploy's log on PickiPedia: nothing secret goes up, and the page says what happened.

post-deploy-log.py is named with hyphens and run, not imported, so it is
loaded here by path.
"""

import importlib.util
import json
import sys
import urllib.parse
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('post_deploy_log', SCRIPTS / 'post-deploy-log.py')
poster = importlib.util.module_from_spec(spec)
sys.modules['post_deploy_log'] = poster
spec.loader.exec_module(poster)

VAULT_YAML = """
memory_lane_postgres_password: Zq8-correct-horse-battery
DrivingThatTrain_Bot_Username: DrivingThatTrain@deploy-logs
DrivingThatTrain_Bot_Password: abcdefghij0123456789klmnopqrstuv
short: abc
nested:
  inner_key: inner-SECRET-99
ssh_key: |
  -----BEGIN OPENSSH PRIVATE KEY-----
  b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQ
  -----END OPENSSH PRIVATE KEY-----
"""

FINISHED = """PLAY [hunter] ******
TASK [Gathering Facts] ******
ok: [hunter.cryptograss.live]
TASK [memory-lane : Write .env] ******
changed: [hunter.cryptograss.live]
[WARNING]: Platform linux on host hunter is using the discovered Python interpreter
TASK [Restart the watcher] ******
ok: [hunter.cryptograss.live]
RUNNING HANDLER [restart caddy] ******
changed: [hunter.cryptograss.live]
TASK [Show the dsn] ******
ok: [hunter.cryptograss.live] => {"msg": "postgres://magent:Zq8-correct-horse-battery@10.0.0.2/magenta_memory"}

PLAY RECAP *********************************************************************
hunter.cryptograss.live    : ok=4    changed=2    unreachable=0    failed=0    skipped=1    rescued=0    ignored=0
"""

FAILED = """TASK [Gathering Facts] ******
ok: [hunter]
TASK [Build magenta-arthel base image] ******
fatal: [hunter]: FAILED! => {"changed": false, "msg": "Error building </pre> image: token=ghp_0123456789abcdefghijklmnopqrstuvwxyz"}

PLAY RECAP *********************************************************************
hunter    : ok=1    changed=0    unreachable=0    failed=1    skipped=0    rescued=0    ignored=0
"""


@pytest.fixture
def secrets(monkeypatch):
    monkeypatch.setattr(poster.subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout=VAULT_YAML))
    values, vault = poster.vault_values('/tmp/vault-pass')
    return values, vault


def test_every_vault_value_comes_out_named_and_short_ones_stay(secrets):
    values, vault = secrets
    text, counts = poster.redact('db Zq8-correct-horse-battery, inner-SECRET-99, abc, '
                                 'b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQ', values)
    assert 'Zq8' not in text and 'inner-SECRET' not in text and 'b3Blbn' not in text
    assert '[vault: memory_lane_postgres_password]' in text and '[vault: nested.inner_key]' in text
    assert '[vault: ssh_key]' in text  # a multi-line secret, a line at a time
    assert 'abc,' in text  # three characters: not worth the false matches
    assert counts['vault'] == 3
    assert vault['DrivingThatTrain_Bot_Password'] == 'abcdefghij0123456789klmnopqrstuv'


def test_secret_shapes_the_vault_doesnt_hold_come_out_too(secrets):
    text, counts = poster.redact('token=ghp_0123456789abcdefghijklmnopqrstuvwxyz and '
                                 'Authorization: Bearer abc123DEF456ghi789 and --password hunter2pass', secrets[0])
    assert 'ghp_' not in text and 'abc123DEF' not in text and 'hunter2pass' not in text
    assert counts['pattern'] == 3


def test_a_finished_deploy_is_summed_up(secrets):
    log, counts = poster.redact(FINISHED, secrets[0])
    text = poster.page('hunter', 'finished', 26133806, log, counts, by='jmyles', took=626, commit='ae318170abcd',
                       when='2026-10-06 14:00 UTC')
    assert text.startswith("'''✓ hunter redeployed''' at block 26,133,806 (2026-10-06 14:00 UTC), in 10m 26s, by jmyles")
    assert 'Taken out before posting: 1 vault value, by exact match' in text
    assert '* memory-lane : Write .env\n* restart caddy\n' in text  # what changed, handlers too
    assert 'ok=4 changed=2 unreachable=0 failed=0' in text
    assert '[WARNING]: Platform linux' in text
    assert 'Zq8-correct' not in text and '[vault: memory_lane_postgres_password]' in text
    assert '[[Category:Deploy logs]] [[Category:hunter deploy logs]]' in text
    assert 'The whole log' not in text


def test_a_failed_deploy_is_all_there_safely(secrets):
    log, counts = poster.redact(FAILED, secrets[0])
    text = poster.page('hunter', 'failed', 26133806, log, counts)
    assert text.startswith("'''✗ hunter redeploy failed'''")
    assert '== Where it stopped ==\n* Build magenta-arthel base image' in text
    assert '== The whole log ==' in text and 'Gathering Facts' in text
    assert 'ghp_' not in text
    assert text.count('</pre>') == text.count('<pre>')  # a </pre> in the log can't end the block early
    assert 'Error building &lt;/pre&gt; image' in text


def test_a_huge_log_keeps_its_start_and_its_end():
    log = 'START\n' + 'x' * (poster.MAX_LOG_CHARS * 2) + '\nEND'
    text = poster.page('maybelle', 'failed', 1, log, {'vault': 0, 'pattern': 0})
    assert 'START' in text and 'END' in text and 'characters left out here' in text
    assert len(text) < poster.MAX_LOG_CHARS + 10_000


class FakeWiki:
    """MediaWiki's action API, as far as login and a createonly edit go."""

    def __init__(self, taken=()):
        self.taken, self.asked, self.made = set(taken), [], {}

    def open(self, request, timeout=None):
        params = dict(urllib.parse.parse_qsl(request.data.decode() if request.data else request.full_url.split('?', 1)[1]))
        self.asked.append(params)
        if params.get('meta') == 'tokens':
            body = {'query': {'tokens': {'logintoken': 'L+\\', 'csrftoken': 'C+\\'}}}
        elif params.get('action') == 'login':
            ok = params['lgname'] == 'DrivingThatTrain@deploy-logs' and params['lgpassword'] == 'pw'
            body = {'login': {'result': 'Success' if ok else 'Failed', 'reason': 'Incorrect password'}}
        elif params.get('action') == 'edit':
            if params['title'] in self.taken:
                body = {'error': {'code': 'articleexists', 'info': 'The article you tried to create has been created already.'}}
            else:
                self.taken.add(params['title'])
                self.made[params['title']] = params
                body = {'edit': {'result': 'Success'}}
        return SimpleNamespace(read=lambda: json.dumps(body).encode(), __enter__=None)


class Answer:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self.body


def wiki_with(fake):
    wiki = poster.Wiki('https://pickipedia.example')
    wiki.opener = SimpleNamespace(open=lambda request, timeout=None: Answer(fake.open(request).read()))
    return wiki


def test_the_bot_signs_in_and_makes_the_page_as_a_bot_edit():
    fake = FakeWiki(taken={'Cryptograss:Hunter/deploy_logs/7'})
    wiki = wiki_with(fake)
    wiki.login('DrivingThatTrain@deploy-logs', 'pw')
    made = wiki.create('Cryptograss:Hunter/deploy_logs/7', 'text', 'hunter finished: deploy log')
    assert made == 'Cryptograss:Hunter/deploy_logs/7-2'  # two deploys in one block: the next free name
    edit = fake.made[made]
    assert (edit['bot'], edit['createonly'], edit['token']) == ('1', '1', 'C+\\')
    with pytest.raises(RuntimeError, match='wiki login: Failed'):
        wiki_with(FakeWiki()).login('DrivingThatTrain@deploy-logs', 'wrong')


def test_it_never_fails_a_deploy(monkeypatch, tmp_path, capsys):
    def broken(*a, **k):
        raise FileNotFoundError('ansible-vault')
    monkeypatch.setattr(poster.subprocess, 'run', broken)
    log = tmp_path / 'deploy.log'
    log.write_text(FINISHED)
    monkeypatch.setattr(sys, 'argv', ['post-deploy-log.py', 'hunter', 'finished', str(log),
                                      '--vault-password-file', '/nope'])
    assert poster.main() == 0
    assert 'Deploy log not posted (FileNotFoundError)' in capsys.readouterr().out


def test_a_dry_run_prints_the_page(monkeypatch, tmp_path, capsys):
    calls = []

    def run(cmd, **k):
        calls.append(cmd[0])
        return SimpleNamespace(stdout=VAULT_YAML if cmd[0] == 'ansible-vault' else 'ae318170abcd\n')
    monkeypatch.setattr(poster.subprocess, 'run', run)
    monkeypatch.setattr(poster, 'current_block', lambda: 26133806)
    log = tmp_path / 'deploy.log'
    log.write_text('\x1b[0;32m' + FINISHED + '\x1b[0m')
    monkeypatch.setattr(sys, 'argv', ['post-deploy-log.py', 'delivery-kid', 'finished', str(log),
                                      '--vault-password-file', '/v', '--took', '61', '--dry-run'])
    poster.main()
    out = capsys.readouterr().out
    assert out.startswith('--- Cryptograss:Delivery-kid/deploy_logs/26133806 ---')
    assert 'in 1m 01s' in out and 'maybelle-config <code>ae318170abcd</code>' in out
    assert '\x1b' not in out and 'Zq8' not in out
