# Hunter Deployment

Infrastructure for deploying the magenta memory system to hunter VPS.

## Architecture

### Shared Services (run on hunter host or dedicated container)

**PostgreSQL Database** (`ai-sandbox` or `postgres` container):
- Database: `magenta_memory`
- Accessible from all user containers
- Stores all conversation history with `from`/`to` columns

**Conversation Watcher** (`watcher` container or systemd service):
- Monitors `/opt/magenta/*/` claude logs directories
- Auto-imports new messages in real-time
- Runs as single shared service
- Configuration:
  - `CLAUDE_LOGS_DIR`: Colon-separated paths to watch (e.g., `/opt/magenta/justin/.claude/project-logs:/opt/magenta/rj/.claude/project-logs`)
  - `WATCHER_ERA_NAME`: Era to import into (default: "Current Working Era (Era N)")

### Per-User Containers

Each team member gets isolated container with:
- Dedicated ports (e.g., justin: 14000-14009, rj: 14010-14019)
- Own `.claude` directory mounted from `/opt/magenta/{username}/`
- Shared access to PostgreSQL database
- SSH access via key-based routing
- A Paseo agent daemon (see below)

### Paseo Agent Daemon

[Paseo](https://github.com/getpaseo/paseo) is a self-hosted orchestrator that
gives the agents a web and mobile interface instead of a terminal. It runs
*inside* each user container, so agents get the real environment — workspace,
MCP servers, `gh` credentials, `~/.claude` — exactly as a terminal session
would. It is harness-agnostic (Claude Code, Codex, Copilot, OpenCode, Pi),
which is the main reason we picked it over a first-party client.

| | |
|---|---|
| Container port | 6767 |
| Host port | `19090 + (ssh_port - 2222)` — justin 19090, rj 19091, skyler 19092, fibonacci 19093 |
| Public URL | `https://paseo.{username}.hunter.cryptograss.live` |
| Auth | `PASEO_PASSWORD`, per user, from the vault |
| State | `/home/magent/.paseo` (on the mounted home volume, survives rebuilds) |
| Logs | `/tmp/paseo.log` inside the container |

**The daemon does not start unless `PASEO_PASSWORD` is set.** It can run
arbitrary code on the container by design, so an unset password means no
daemon rather than a daemon behind a default one. To enable it, add
`paseo_password` to the vault and re-run the playbook.

The host port binds to `127.0.0.1` only. Caddy is the sole public entrance,
which is also what terminates TLS — the mobile clients require it.

#### Seeing each other's sessions

`paseo_password` is one shared secret across all users, the same way
`code_server_password` already is. Each of us runs our own daemon, but anyone
can open anyone else's URL and watch those sessions live — no separate shared
instance needed.

Per-user daemons rather than one shared one, because a single daemon would run
every agent as the same `magent` in one container: one home directory, one
`~/.claude` auth, one git identity, one workspace. It would also break
memory-lane's attribution, since the watcher maps
`/opt/magenta/<user>/home/.claude/projects` per user — every conversation would
land under whoever's container hosted the daemon.

If we later want a genuine org-level view — all daemons in one dashboard, plus
GitHub/Slack/Discord triggers — that's [Paseo Hub](https://paseo.sh/docs/hub),
self-hostable with `npx @getpaseo/hub` against our existing PostgreSQL. Note
that Hub's shared view is trigger runs and daemon status; live session viewing
is still per-daemon.

#### Two non-obvious daemon settings

Both are set in the compose template and both are required here:

- `PASEO_LISTEN=0.0.0.0:6767` — the daemon defaults to `127.0.0.1`, which
  Docker port mapping cannot reach, since mapping forwards to the container's
  interface rather than its loopback.
- `PASEO_HOSTNAMES` — the daemon validates the `Host` header against an
  allowlist defaulting to `localhost`, so Caddy's domain must be named or every
  proxied request is rejected.

The daemon is started with `paseo daemon start --web-ui`. Bare `paseo` runs the
interactive flow that prompts about the relay; relay consent only happens under
`paseo daemon pair --relay`, which we never call — Caddy is our transport.

## Files

- `Dockerfile` - Main user container image
- `Dockerfile.services` - Services container (watcher, postgres)
- `docker-compose.services.yml` - Shared services stack
- `docker-compose.local.yml` - Local development setup
- `ansible/` - Ansible playbooks for hunter deployment

## Deployment

### Local Development

**Prerequisites:**
1. An SSH tunnel to maybelle's postgres (or hunter's postgres via maybelle):
   ```bash
   ssh -L 172.17.0.1:15432:10.0.0.2:5432 root@maybelle.cryptograss.live
   ```
   Note: Bind to `172.17.0.1` (Docker bridge IP) so containers can reach it via `host.docker.internal`.

2. A `.env.local` file with required variables (see magenta repo for template):
   ```bash
   POSTGRES_PASSWORD=your_password
   GH_TOKEN=ghp_your_token  # Optional, for GitHub CLI in container
   SSH_AUTHORIZED_KEY="ssh-ed25519 AAAA... you@host"  # Your SSH public key
   ```

**Starting the dev environment:**
```bash
cd hunter

# Read your SSH key into the env and start
SSH_AUTHORIZED_KEY="$(cat ~/.ssh/id_ed25519.pub)" \
  docker compose -f docker-compose.local.yml \
  --env-file ~/projects/JustinHolmesMusic/magenta/.env.local \
  up --build
```

**Services started:**
- `magenta-dev` - Development container with code-server, SSH on port 2222
- `memory-lane-local` - Django memory viewer on port 3000

**Connecting:**
```bash
# SSH into the container
ssh -p 2222 magent@localhost

# Or use code-server in browser
open http://localhost:8080
```

**MCP Server:**
The local dev container connects to the public MCP endpoint at `https://mcp.maybelle.cryptograss.live` by default. No tunnel needed for MCP.

**Troubleshooting:**

*"host.docker.internal" not resolving:*
The compose file includes `extra_hosts` to map this on Linux. If still failing, ensure the postgres tunnel is bound to `172.17.0.1`, not `127.0.0.1` or `0.0.0.0`.

*SSH asking for password:*
The `SSH_AUTHORIZED_KEY` env var wasn't set or is empty. Pass it explicitly:
```bash
SSH_AUTHORIZED_KEY="$(cat ~/.ssh/id_ed25519.pub)" docker compose ...
```

*MCP "connection already closed":*
Check that `https://mcp.maybelle.cryptograss.live` is reachable. Run `/mcp` in Claude Code to reconnect.

### Hunter VPS

```bash
# Deploy everything
cd hunter/ansible
ansible-playbook -i inventory.yml playbook.yml
```

This will:
1. Build shared base image
2. Deploy postgres container
3. Deploy watcher container
4. Create per-user containers for justin, rj, skyler
5. Configure SSH routing and Caddy reverse proxy

## Watcher Service

The watcher runs as a **shared service** (not per-user) because:
- Single process can watch multiple user directories
- Deduplication works across all users
- Simpler resource usage
- Consistent era management

It watches all user log directories and imports to shared database with proper `from`/`to` attribution.

## Database Access

All containers access the shared database:
- Host: `postgres` (container name) or `ai-sandbox` (if using existing postgres)
- Database: `magenta_memory`
- User: `magent`
- Password: (from ansible vault or environment)

## Logs

- Watcher: `/opt/magenta/logs/watcher.log` (rotated, 10MB max, 5 backups)
- Import counts displayed in real-time
- Deduplication stats tracked

## Ready for Hunter

✅ Import count tracking (created vs skipped)
✅ Line accounting with verification
✅ Perfect deduplication (tested on 365 files, 131K+ lines)
✅ Watcher running and importing in real-time
✅ All infrastructure organized in `/hunter`
