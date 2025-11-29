#!/bin/bash
# Migrate postgres data from hunter to maybelle
#
# Uses SSH agent forwarding so your local SSH key is used to reach hunter
# through maybelle.
#
# Data flows: hunter → maybelle (over private network, not through your laptop)
#
# Uses pg_dump custom format (-Fc) which:
#   - Is already compressed
#   - Avoids psql \restrict issues on restore
#   - Works reliably with pg_restore
#
# Prerequisites:
#   - SSH access to both maybelle and hunter (uses your default SSH key)
#
# Usage:
#   ./maybelle/scripts/migrate-postgres-from-hunter.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/../config.yml"

# Parse config.yml
get_config() {
    grep "^$1:" "$CONFIG_FILE" | sed 's/^[^:]*: *"\?\([^"]*\)"\?/\1/'
}

MAYBELLE_HOST=$(get_config host)
MAYBELLE_USER=$(get_config user)

echo "=== Migrate Postgres from Hunter to Maybelle ==="
echo ""
echo "Data flow: hunter → maybelle (via private network)"
echo "Format: pg_dump custom format (-Fc)"
echo ""

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILENAME="magenta_memory_${TIMESTAMP}.dump"

# SSH to maybelle with agent forwarding (-A), which then SSHs to hunter
echo "SSHing to maybelle to pull database from hunter..."
ssh -A "${MAYBELLE_USER}@${MAYBELLE_HOST}" bash -s "$BACKUP_FILENAME" << 'REMOTE_SCRIPT'
set -e
BACKUP_FILENAME="$1"

echo "  Pulling database from hunter..."

# Ensure backup directory exists
mkdir -p /mnt/persist/magenta/backups

# Pull from hunter (using forwarded agent) in custom format
ssh root@hunter.cryptograss.live "docker exec magenta-postgres pg_dump -Fc -U magent magenta_memory" \
    > "/mnt/persist/magenta/backups/${BACKUP_FILENAME}"

# Report size
SIZE=$(stat -c%s "/mnt/persist/magenta/backups/${BACKUP_FILENAME}")
echo "  Saved: /mnt/persist/magenta/backups/${BACKUP_FILENAME} (${SIZE} bytes)"
REMOTE_SCRIPT

echo ""
echo "=== Migration complete ==="
echo ""
echo "Backup saved to: /mnt/persist/magenta/backups/${BACKUP_FILENAME}"
echo ""
echo "To restore:"
echo "  1. If database is empty: Run chapter-1 (auto-restores from latest backup)"
echo "  2. Manual restore on maybelle:"
echo "     docker exec -i magenta-postgres pg_restore -U magent -d magenta_memory --no-owner < /mnt/persist/magenta/backups/${BACKUP_FILENAME}"
echo ""
