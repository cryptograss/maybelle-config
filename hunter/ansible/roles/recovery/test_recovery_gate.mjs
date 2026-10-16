// node --experimental-strip-types --test hunter/ansible/roles/recovery/test_recovery_gate.mjs
// (or: jiti hunter/ansible/roles/recovery/test_recovery_gate.mjs, where Node has no TypeScript)
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { decide } from './files/recovery-gate.ts';

const bash = (command) => decide('bash', { command }).verdict;

test('the look-first commands from its rules pass without asking', () => {
  for (const c of [
    'uptime', 'df -h / /opt', 'free -h', 'systemctl status docker --no-pager',
    "docker ps -a --format 'table {{.Names}}\\t{{.Status}}'",
    "timeout 20 docker ps -a --format 'table {{.Names}}\\t{{.Status}}'",
    'journalctl -u docker --since -3h --no-pager | tail -n 150',
    'journalctl -p err --since -3h --no-pager | tail -n 80',
    'docker logs --tail 80 docs-tests-qa-arthel', 'docker system df', 'docker image ls',
    'journalctl -b -1 | grep -i reboot', 'cat /opt/magenta/maybelle-config/hunter/CONTAINERS.md',
    "curl -s 'http://10.0.0.2:3000/api/recent_messages/?limit=200'",
  ]) assert.equal(bash(c), 'allow', c);
});

test('what crashed hunter, deletes, or reboots is refused, however it is wrapped', () => {
  for (const c of [
    'docker buildx history ls', 'docker builder prune -f', 'docker system prune -a', 'true && docker buildx du',
    'docker rm -f justin-arthel', 'docker rmi magenta-arthel', 'docker volume rm x', 'docker container rm x',
    'docker image prune', 'cd /opt/magenta/justin && docker compose down', 'docker-compose down',
    'reboot', 'sudo reboot', 'sudo shutdown -r now', 'systemctl reboot', 'echo hi; poweroff',
    'rm -rf /opt/magenta/justin', 'sudo rm -r /var/lib/docker', 'dd if=/dev/zero of=/dev/sda',
  ]) assert.equal(bash(c), 'deny', c);
});

test('anything that may change something asks Justin first', () => {
  for (const c of [
    'docker start delivery-kid-arthel', 'sudo systemctl restart docker', 'docker restart x',
    'docker inspect justin-arthel', 'echo x > /tmp/f', 'ls; touch /tmp/x', 'cat $(which docker)',
    'tail -n 5 `ls`', 'find / -delete', "awk 'BEGIN{system(\"id\")}'", 'sed -i s/a/b/ f',
    'systemctl restart caddy', 'curl -X POST http://10.0.0.2:3000/api/x', 'curl https://example.com',
    'python3 -c "print(1)"', 'git push', 'timeout 5 docker start x',
  ]) assert.equal(bash(c), 'ask', c);
});

test('reading where the keys are asks, even with a read-only program', () => {
  assert.equal(bash('cat ~/.config/magenta/openrouter_key'), 'ask');
  assert.equal(bash('cat /home/recovery/.claude/.credentials.json'), 'ask');
  assert.equal(decide('read', { path: '/home/recovery/.config/magenta/claude_token' }).verdict, 'ask');
  assert.equal(bash('cat ~/.config/magenta/x; docker buildx ls'), 'deny');
});

test('the other tools: reading and the record pass, writing asks', () => {
  for (const t of ['read', 'grep', 'find', 'ls']) assert.equal(decide(t, { path: '/opt/magenta' }).verdict, 'allow');
  assert.equal(decide('mcp__magenta_memory_v2__search_messages', { query: 'hunter' }).verdict, 'allow');
  assert.equal(decide('write', { path: '/tmp/x', content: 'y' }).verdict, 'ask');
  assert.equal(decide('edit', { path: '/opt/magenta/x' }).verdict, 'ask');
  assert.equal(decide('codemode', { code: '1' }).verdict, 'ask');
});
