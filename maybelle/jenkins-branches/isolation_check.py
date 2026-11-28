#!/usr/bin/env python3
"""Run inside the branches agent at every deploy (maybelle.yml, "The branches agent reaches nothing of ours"):
nothing of ours answers it but Jenkins and its proxy, the proxy carries nothing but what it lists, and it
does carry that (Cryptograss:Moods/ramp/Plan, section 11). Exits 1, failing the deploy, either way.
A file, not a heredoc in the task: ansible splits a shell task's words before running it, and an apostrophe
in a comment there broke the first deploy (9 Nov).

Both halves, because only refusals were ever checked: a proxy that turned every request away passed every
other line of this file, and no deploy would have said so. What the agent cannot work without is the
registries -- a branch's ci/test opens with pip and npm.

This half would NOT have caught the outage of 22 to 28 Nov, when 53 runs failed at pip or npm. The proxy
was answering throughout; run-ci handed each build one spelling of each proxy variable and the other empty,
and an empty https_proxy means "no proxy" to python and npm (re, docs-tests-qa, 28 Nov 2026, proven on the
agent in branch-tests #632 and #633; mended in re/run-ci-proxy-both-spellings). What catches that is a watch
on results: red when every recent run stopped before reaching a test. This catches the other case, the day
the proxy itself dies, which until now was equally silent.
"""
import socket, sys
must_not = [('10.0.0.2', p) for p in (22, 2222, 3000, 5432, 8000, 8080)] + [
    ('10.0.0.4', 22), ('10.0.0.4', 80), ('10.0.0.4', 443), ('169.254.169.254', 80),
    ('172.17.0.1', 22), ('172.17.0.1', 8080), ('1.1.1.1', 443)]  # the host, by another bridge; the internet
bad = []
for host, port in must_not:
    s = socket.socket(); s.settimeout(3)
    try:
        s.connect((host, port)); bad.append(f'{host}:{port} answered')
    except OSError:
        pass
    finally:
        s.close()
for host, port in (('jenkins', 8080), ('branches-proxy', 8888)):
    try:
        socket.create_connection((host, port), timeout=5).close()
    except OSError as e:
        bad.append(f'{host}:{port} unreachable ({e})')
# The proxy refuses what isn't on its list, IP addresses included, plain HTTP or CONNECT, and any
# port but 443 for CONNECT (re).
for request in (b'CONNECT 10.0.0.2:3000 HTTP/1.1\r\nHost: 10.0.0.2:3000\r\n\r\n',
                b'GET http://10.0.0.2:3000/ HTTP/1.1\r\nHost: 10.0.0.2:3000\r\n\r\n',
                b'CONNECT github.com:22 HTTP/1.1\r\nHost: github.com:22\r\n\r\n'):
    s = socket.create_connection(('branches-proxy', 8888), timeout=10)
    s.sendall(request)
    status = s.recv(200).split(b'\r\n')[0]
    s.close()
    if b' 200' in status or not status:
        bad.append(f'the proxy carried {request.split(b" ")[1].decode()} ({status.decode() or "no answer"})')
# And it carries the registries. tinyproxy answers a CONNECT with 200 only once it has reached the host
# itself, so one of these proves the whole way out: the list (403 if a host is off it), the name (500 if
# DNS cannot answer), and the route (500 if nothing out there replies). Checked against tinyproxy 1.11.1,
# the version in the proxy's image, on hunter: an allowed host that cannot resolve gives "500 Unable to
# connect" and one off the list gives "403 Filtered", so a failure here says which of the three it is.
for host in ('pypi.org', 'files.pythonhosted.org', 'registry.npmjs.org', 'github.com'):
    try:
        s = socket.create_connection(('branches-proxy', 8888), timeout=15)
        s.sendall(f'CONNECT {host}:443 HTTP/1.1\r\nHost: {host}:443\r\n\r\n'.encode())
        status = s.recv(200).split(b'\r\n')[0].decode(errors='replace')
        s.close()
    except OSError as e:
        status = f'({e})'
    if ' 200' not in status:
        bad.append(f'the proxy would not carry {host}:443, which pip and npm need: {status or "no answer"}')

print('\n'.join(bad) or 'nothing of ours answers, and the registries do')
sys.exit(1 if bad else 0)
