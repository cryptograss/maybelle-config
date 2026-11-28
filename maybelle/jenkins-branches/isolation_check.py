#!/usr/bin/env python3
"""Run inside the branches agent at every deploy (maybelle.yml, "The branches agent reaches nothing of ours"):
nothing of ours answers it but Jenkins and its proxy, the proxy carries nothing but what it lists, and it
does carry that (Cryptograss:Moods/ramp/Plan, section 11). Exits 1, failing the deploy, either way.
A file, not a heredoc in the task: ansible splits a shell task's words before running it, and an apostrophe
in a comment there broke the first deploy (9 Nov).

Both halves, because only refusals were ever checked: a proxy that refuses everything passed every line of
this file, and the agent went from 22 Nov to 28 Nov unable to install a package while each deploy read green
(re found the outage, docs-tests-qa, 28 Nov 2026; magent added the half that would have caught it). What the
agent cannot work without is the registries: a branch's ci/test opens with pip and npm.
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
