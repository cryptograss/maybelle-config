# maybelle-config

Deployment configuration for cryptograss infrastructure. Maybelle is the control plane for deploying and managing hunter (development VPS) and maybelle itself (CI/CD server).

## Structure

- **secrets/** - Single unified vault for all secrets (gitignored plaintext, encrypted for production)
- **hunter/** - Deployment configuration for hunter VPS (multi-user magenta development environment)
- **maybelle/** - Deployment configuration for maybelle server (Jenkins CI/CD)

## Deployment

All deployments run from maybelle server.

### Deploy to hunter VPS
```bash
cd ~/maybelle-config/hunter
./deploy.sh
```

### Update maybelle itself
```bash
cd ~/maybelle-config/maybelle
ansible-playbook -i localhost, ansible/maybelle.yml --ask-vault-pass
```

## Secrets Management

Secrets are stored in `secrets/vault-plaintext.yml` (gitignored). Required variables:

**Hunter secrets:**
- `memory_lane_postgres_password` - PostgreSQL password
- `justin_vscode_password` - Justin's code-server password
- `rj_vscode_password` - R.J.'s code-server password

**Maybelle secrets:**
- `maybelle_env` - Jenkins environment variables (multiline string)
- `jenkins_admin_password` - Jenkins admin login
- `github_token` - GitHub API token

**Note:** TLS certificates are handled automatically by Caddy via Let's Encrypt.

For production, encrypt with:
```bash
ansible-vault encrypt secrets/vault-plaintext.yml -o secrets/vault.yml
```

To edit encrypted vault:
```bash
ansible-vault edit secrets/vault.yml
```

To decrypt for local development:
```bash
ansible-vault decrypt secrets/vault.yml -o secrets/vault-plaintext.yml
```

## Architecture

**Maybelle** (maybelle.cryptograss.live):
- Jenkins CI/CD in Docker
- Caddy reverse proxy with automatic SSL
- Builds arthel (justinholmes.com + cryptograss.live)
- Hosts Memory Lane (PostgreSQL + Django + MCP server)
- Deploys to hunter via SSH

**Hunter** (hunter.cryptograss.live):
- Multi-user development containers
- Watcher service (monitors conversations, writes to maybelle's database)
- Per-user isolation with dedicated ports and SSH

## Production Deployment

Production builds go through a two-stage process for security:

1. **Jenkins builds** the site and writes to `/var/jenkins_home/www/builds/production/`
2. **Jenkins creates** a `.deploy-ready` marker file with build metadata
3. **Root cron** (every 2 min) checks for marker, rsyncs to NearlyFreeSpeech, removes marker

This separation means a compromised Jenkins cannot directly push to production - an attacker would also need root access on maybelle.

**Logs:**
- Deploy log: `/var/log/nfs-deploy.log`
- Viewable in Jenkins via the backup-memory-lane job

## Backup System

Memory Lane database backups run automatically:

### Local Backups
- **Bi-hourly**: Every 2 hours at :00, named by Ethereum block height
- **Location**: `/mnt/persist/magenta/backups/`
- **Retention**: 2 days
- **Script**: `/mnt/persist/magenta/backup-postgres.sh`

### Daily Backups
- **Schedule**: 3am daily (copied from bi-hourly)
- **Location**: `/mnt/persist/magenta/backups/daily/`
- **Retention**: 30 days

### Offsite Backups
- **Schedule**: 4am daily (after daily backup created)
- **Destination**: NearlyFreeSpeech `/home/private/backups/`
- **Retention**: Mirrors daily folder (~30 days)
- **Script**: `/usr/local/bin/backup-to-nfs.sh`
- **Log**: `/var/log/nfs-backup.log`

### Monitoring
The `backup-memory-lane` Jenkins job runs every 2 hours at :30 and checks:
- Latest backup exists and is <3 hours old
- Daily backup count and age
- Offsite sync log for recent successful syncs

### Manual Backup
```bash
# On maybelle as root:
/mnt/persist/magenta/backup-postgres.sh
```

### Manual Offsite Sync
```bash
# On maybelle as root:
/usr/local/bin/backup-to-nfs.sh
```

### Restore from Backup
On maybelle rebuild, ansible will:
1. Check if database is empty
2. List available backups (sorted by size)
3. Prompt for which backup to restore

To manually restore:
```bash
docker exec magenta-postgres pg_restore -U magent -d magenta_memory --no-owner --no-privileges /backups/[backup-file].dump
```

## Secrets Management

Secrets are stored in `secrets/vault.yml` (ansible-vault encrypted).

**Required secrets:**

| Variable | Purpose |
|----------|---------|
| `memory_lane_postgres_password` | PostgreSQL password for Memory Lane |
| `ingest_api_key` | API key for watcher to submit conversations |
| `jenkins_admin_password` | Jenkins admin login |
| `maybelle_github_token` | GitHub API token for Jenkins |
| `jenkins_github_ssh_key` | SSH key for Jenkins GitHub access (base64) |
| `hunter_root_ssh_key` | SSH key for maybelle→hunter access (base64) |
| `nfs_ssh_key` | SSH key for production deploys and backups (base64) |
| `justin_vscode_password` | Justin's code-server password on hunter |

To edit:
```bash
ansible-vault edit secrets/vault.yml
```

## Repository Migration

This repo consolidates deployment configuration from:
- `magenta/hunter/` → `hunter/`
- `arthel/deployment/` → `maybelle/`
- Scattered secrets → `secrets/vault.yml`
