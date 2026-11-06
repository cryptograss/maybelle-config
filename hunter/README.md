# Hunter Deployment

Infrastructure for deploying the magenta memory system to hunter VPS.

**Since 1 Nov 2026, Moods own directories and emergents own processes**
(Cryptograss:Moods as Directories on PickiPedia; the model is
Cryptograss:Mood Architecture). A Mood is a directory at `/moods/<slug>`,
seen by every container. Each emergent has one container and one poller
(`emergent-<name>`); people have `workbench`. PostgreSQL does not run on
hunter: the database lives on maybelle. The watcher is retired
(maybelle-config#254): the pollers record every turn themselves.

## Architecture

### The shared directory (`/moods`)

`/opt/magenta/moods/<slug>` on hunter's disk holds a Mood's clones and
files. Every container mounts it at `/moods`. It is owned by the group
`moods` (gid 3000), setgid, with a default ACL, so one emergent's clone is
another's to commit in whatever their umask; git trusts `/moods/*`
system-wide. A thread gets a worktree under `/moods/<slug>/threads/<thread>/`.

### Emergents

One container per emergent (`emergent-<name>`, first `agency-<name>`), each
with its own home, poller and workspace. The container image is built from
this directory's `Dockerfile`; a new emergent joins the `moods` group at
startup (`container_startup.py setup_agent`).

### workbench

The people's box (maybelle-config#257): mounts `/moods`, has ssh and
code-server, and no docker.sock and no poller. Reached with
`ssh sshrouter@hunter workbench`, or by Mood slug:
`ssh sshrouter@hunter chartifacts` puts you in `/moods/chartifacts`
(append `tmux` for a shared session named after the Mood).

### The ssh router

`sshrouter@hunter` takes a person's name (their own container) or a Mood's
slug (a shell in workbench, in that Mood's directory). Anything else falls
through to the default. `roles/ssh-router/templates/route-ssh.sh.j2` renders
it; its tests run in CI.

### Previews

Each Mood directory has ten preview ports in workbench, kept in its
`.previews` file, served at `<slug><N>.hunter.cryptograss.live` through
Caddy (roles/mood-previews; `mood-previews.py`, installed as
`hunter-mood-previews` on a one-minute timer). Certificates are
issued on demand, only for listed hostnames.

## Files

- `Dockerfile` - the base container image (joins the `moods` group, git
  `safe.directory` for `/moods/*`)
- `docker-compose.local.yml` - local development setup
- `multi-log-viewer.sh` - a tmux layout of the running containers' logs
- `ansible/` - playbooks for hunter deployment (inventory, roles, router)
- `container_startup.py` - what runs when a container starts
- `scripts/agent_homes.py`, `scripts/fold_mood_homes.py` - home moves and
  the fold of a retired Mood container's workspace into `/moods`

## Deployment

### Local development

**Prerequisites:**

1. An SSH tunnel to maybelle's postgres (the database has never run on
   hunter):
   ```bash
   ssh -L 172.17.0.1:15432:10.0.0.2:5432 root@maybelle.cryptograss.live
   ```
   Note: Bind to `172.17.0.1` (Docker bridge IP) so containers can reach it
   via `host.docker.internal`.

2. A `.env.local` file with required variables (see magenta repo for
   template):
   ```bash
   POSTGRES_PASSWORD=your_password
   GH_TOKEN=ghp_your_token  # Optional, for GitHub CLI in container
   SSH_AUTHORIZED_KEY="ssh-ed25519 AAAA... you@host"  # Your SSH public key
   ```

**Starting the dev environment:**

```bash
cd hunter

SSH_AUTHORIZED_KEY="$(cat ~/.ssh/id_ed25519.pub)" \
  docker compose -f docker-compose.local.yml \
  --env-file ~/projects/JustinHolmesMusic/magenta/.env.local \
  up --build
```

**Connecting:**

```bash
ssh -p 2222 magent@localhost
open http://localhost:8080  # code-server
```

**MCP server:** the dev container connects to the public MCP endpoint at
`https://mcp.maybelle.cryptograss.live` by default. No tunnel needed.

**Troubleshooting:**

*"host.docker.internal" not resolving:* the compose file includes
`extra_hosts` to map this on Linux. If it still fails, ensure the postgres
tunnel is bound to `172.17.0.1`, not `127.0.0.1` or `0.0.0.0`.

*SSH asking for password:* the `SSH_AUTHORIZED_KEY` env var wasn't set or
is empty. Pass it explicitly (see the start command above).

*MCP "connection already closed":* check that
`https://mcp.maybelle.cryptograss.live` is reachable. Run `/mcp` in Claude
Code to reconnect.

### Hunter VPS

```bash
cd hunter/ansible
ansible-playbook -i inventory.yml playbook.yml
```

This deploys the shared directory and its ACLs, the emergents' containers from
the inventory (`emergent-*`), `workbench`, the ssh router and Caddy, and
runs the hourly unpushed-work backup (`housekeeping`), and `faces`. The
`services` role only takes down what used to run here (the watcher,
retired by #254; mcp-server, memory-lane, magenta-postgres). Postgres
lives on maybelle.

## Logs

Run `multi-log-viewer.sh` from a machine with root ssh to hunter: a tmux
layout with one pane per running container plus Caddy. For one container:
`ssh root@hunter.cryptograss.live "docker logs -f <name>"`.
