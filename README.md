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
- Its freshness is checked by the `cron-health` Jenkins job

## Backup System

Memory Lane database backups run automatically:

### Local Backups
- **Every two hours**, at odd hours (01:00, 03:00 … 23:00), named by Ethereum block height: `magenta_memory_<height>.dump` (`pg_dump -Fc`)
- **Location**: `/mnt/persist/magenta/backups/`
- **Retention**: 12 hours
- **Script**: `/mnt/persist/magenta/backup-postgres.sh`

### Daily Backups
- **Schedule**: the 03:00 dump is copied
- **Location**: `/mnt/persist/magenta/backups/daily/`
- **Retention**: 7 days

### The NearlyFreeSpeech mirror (magenta database only)
- **Schedule**: 04:00 daily
- **Destination**: NearlyFreeSpeech `/home/private/backups/`
- **Retention**: a mirror of `daily/` (`rsync --delete`), so the same 7 days
- **Script**: `/usr/local/bin/backup-to-nfs.sh`
- **Log**: `/var/log/nfs-backup.log`

The live database and every local copy are on the same volume (`/mnt/persist`), so the offsite copy is the only one that survives losing it.

### The offsite backup: everything a cold start needs

`maybelle/scripts/offsite-backup.sh`, daily at 05:00 UTC from root's cron on maybelle, sends one [restic](https://restic.net) snapshot holding:

- the newest daily dump of the magenta database
- the newest dump of PickiPedia's database
- all of PickiPedia's uploaded files (mirrored first to `/mnt/persist/pickipedia/files/`)
- a dump of GlitchTip's database
- githouse's repositories and its merge log (`/mnt/persist/githouse/git`)
- hunter's unpushed git work (`/mnt/persist/hunter-vcs/latest`)

**Where**: `offsite_repo` in `maybelle/ansible/maybelle.yml`. For now, the Hetzner Storage Box that delivery-kid mounts, at `/mnt/storage-box/offsite/restic`, reached over SSH through delivery-kid. That is off maybelle but still inside Hetzner, so the NearlyFreeSpeech mirror above stays until `offsite_repo` is a store outside Hetzner.

**Kept**: 7 daily, 4 weekly, 12 monthly snapshots. On Sundays restic reads back a tenth of the repository.

**Password**: restic encrypts everything before it leaves. The password is `offsite_restic_password` in the vault; a person puts it there (`openssl rand -hex 32`). Without it the backup fails and `cron-health` goes red.

**Log**: `/var/log/offsite-backup.log`. Each run ends with a `RESULT` line: `OK`, `DEGRADED` (sent, but a part was stale or missing; the `problem:` lines above it say which) or `FAILED`.

#### Restoring from it

This is written here, not only on the wiki, because the wiki is one of the things being restored. On any machine with root's key for delivery-kid:

```bash
apt install restic
ansible-vault view secrets/vault.yml | grep offsite_restic_password    # into a file, say ~/.offsite-pw
REPO=sftp:root@delivery-kid.cryptograss.live:/mnt/storage-box/offsite/restic
restic -r $REPO --password-file ~/.offsite-pw snapshots
restic -r $REPO --password-file ~/.offsite-pw restore latest --target /restore
```

Everything comes back under `/restore` at the path it had on maybelle, for example `/restore/mnt/persist/magenta/backups/daily/magenta_memory_<block height>.dump`. To take one part only, add `--include <path>`.

If delivery-kid's server is gone but the Storage Box is not, mount the box anywhere (CIFS; `storage_box_host`, `storage_box_user` in `delivery-kid/ansible/inventory.yml`, the password in the vault) and use the local path: `restic -r <mount>/offsite/restic ...`.

Then:
- **magenta database**: put the dump in `/mnt/persist/magenta/backups/` and run chapter 1; ansible offers it when it finds the database empty (see below).
- **PickiPedia database and files**: `maybelle/scripts/import-pickipedia.sh` loads a dump; the files go back to `/var/www/pickipedia/images/` on the wiki's server.
- **githouse**: the repositories go back to `/mnt/persist/githouse/git/` before maybelle's playbook runs; without them a new githouse takes its branches from GitHub.

### Monitoring
The `cron-health` Jenkins job (every 5 minutes) checks that the newest dump is under 3 hours old, the newest daily copy under 26 hours, that the last NearlyFreeSpeech sync is under 26 hours old and sent something (an empty `daily/` mirrors as "total size is 0"), and that the offsite backup's last run is under 26 hours old and ended `OK`. A check that turns red is said in #ops.

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
