"""report-deploy.sh tells the Moods a deploy started, finished or failed; an end carries the commits its playbook
deployed (Cryptograss:Moods/ramp/Plan, section 4: deployed is a commit). Run against a listener of our own."""

import http.server
import json
import os
import subprocess
import threading
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / 'report-deploy.sh'


@pytest.fixture
def moods(tmp_path):
    bodies = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            bodies.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            self.send_response(201)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    (tmp_path / 'key').write_text('test-key\n')
    env = {**os.environ, 'MEMORY_LANE_DEPLOY_KEY_FILE': str(tmp_path / 'key'),
           'MEMORY_LANE_URL': f'http://127.0.0.1:{server.server_port}', 'DEPLOY_COMMITS_DIR': str(tmp_path / 'deploys')}

    def report(*args):
        subprocess.run(['bash', str(SCRIPT), *args], env=env, check=True, capture_output=True)
        return bodies[-1]

    yield report, tmp_path / 'deploys'
    server.shutdown()


def test_an_end_carries_what_this_deploy_wrote_and_a_start_clears_the_last(moods):
    report, deploys = moods
    deploys.mkdir()
    (deploys / 'hunter.commits.json').write_text('{"magenta": "from-the-last-deploy"}')
    assert 'commits' not in report('hunter', 'started', 'justin')
    assert not (deploys / 'hunter.commits.json').exists()   # never sent by this deploy's end
    (deploys / 'hunter.commits.json').write_text(json.dumps({'magenta': 'a' * 40}))
    done = report('hunter', 'finished', 'justin', 'https://pickipedia.xyz/wiki/log')
    assert done['commits'] == {'magenta': 'a' * 40}
    assert (done['server'], done['state'], done['by'], done['note']) == ('hunter', 'finished', 'justin',
                                                                         'https://pickipedia.xyz/wiki/log')


def test_an_end_whose_playbook_wrote_nothing_sends_no_commits(moods):
    report, _ = moods
    report('maybelle', 'started', 'justin')
    assert 'commits' not in report('maybelle', 'failed', 'justin')


def test_an_unreadable_file_is_left_out_not_a_failed_report(moods):
    report, deploys = moods
    report('delivery-kid', 'started')
    (deploys / 'delivery-kid.commits.json').write_text('not json')
    assert 'commits' not in report('delivery-kid', 'finished')
