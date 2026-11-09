#!/usr/bin/env python3
"""Run inside the branches agent at every deploy (maybelle.yml, "The branches agent reaches nothing of ours"):
nothing of ours answers it but Jenkins and its proxy, and the proxy carries nothing but what it lists
(Cryptograss:Moods/ramp/Plan, section 11). Exits 1, failing the deploy, if anything gets through.
A file, not a heredoc in the task: ansible splits a shell task's words before running it, and an apostrophe
in a comment there broke the first deploy (9 Nov).
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
print('\n'.join(bad) or 'nothing of ours answers')
sys.exit(1 if bad else 0)
