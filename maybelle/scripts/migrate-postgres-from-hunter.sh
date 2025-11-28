#!/bin/bash
# Migrate postgres data from hunter to maybelle with secrets filtering
#
# This script runs ENTIRELY on hunter/maybelle - no data goes through your laptop.
# It SSHs to hunter, dumps the database, filters secrets, and pipes directly to maybelle.
#
# Prerequisites:
#   - SSH access to hunter (root)
#   - Hunter must be able to SSH to maybelle (keys should be set up)
#   - ANSIBLE_VAULT_PASSWORD_FILE or ANSIBLE_VAULT_PASSWORD set
#   - Vault file accessible locally (for decrypting secrets list)
#
# Usage:
#   ./maybelle/scripts/migrate-postgres-from-hunter.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
CONFIG_FILE="$SCRIPT_DIR/../config.yml"
VAULT_FILE="$REPO_DIR/secrets/vault.yml"

# Parse config.yml
get_config() {
    grep "^$1:" "$CONFIG_FILE" | sed 's/^[^:]*: *"\?\([^"]*\)"\?/\1/'
}

MAYBELLE_HOST=$(get_config host)
MAYBELLE_USER=$(get_config user)
HUNTER_HOST="hunter.cryptograss.live"
HUNTER_USER="root"

echo "=== Migrate Postgres from Hunter to Maybelle ==="
echo ""
echo "Data flow: hunter → maybelle (direct, not through your laptop)"
echo ""

# Get vault password from file or env var
if [ -n "$ANSIBLE_VAULT_PASSWORD_FILE" ] && [ -f "$ANSIBLE_VAULT_PASSWORD_FILE" ]; then
    ANSIBLE_VAULT_PASSWORD=$(cat "$ANSIBLE_VAULT_PASSWORD_FILE")
    export ANSIBLE_VAULT_PASSWORD
    echo "Using vault password from $ANSIBLE_VAULT_PASSWORD_FILE"
elif [ -z "$ANSIBLE_VAULT_PASSWORD" ]; then
    echo "ERROR: Neither ANSIBLE_VAULT_PASSWORD_FILE nor ANSIBLE_VAULT_PASSWORD is set"
    echo "This is needed to decrypt the vault for secrets filtering"
    exit 1
fi

# Step 1: Extract secrets from vault (locally - just the secrets list, not the dump)
echo "Step 1: Extracting secrets from vault..."
SECRETS_JSON=$(ansible-vault view "$VAULT_FILE" | python3 -c '
import sys, yaml, json

data = yaml.safe_load(sys.stdin)
secrets = []

def extract(d):
    if isinstance(d, dict):
        for v in d.values():
            extract(v)
    elif isinstance(d, str) and len(d) > 3:
        secrets.append(d)
    elif isinstance(d, list):
        for item in d:
            extract(item)

extract(data)
print(json.dumps(secrets))
')
SECRET_COUNT=$(echo "$SECRETS_JSON" | python3 -c 'import sys,json; print(len(json.load(sys.stdin)))')
echo "  Found $SECRET_COUNT secrets to filter"

# Step 2: Run the migration directly on hunter, pipe to maybelle
echo ""
echo "Step 2: Dumping from hunter, filtering, and streaming to maybelle..."
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILENAME="magenta_memory_${TIMESTAMP}.sql.gz"

# Create a Python filter script to send to hunter
FILTER_SCRIPT=$(cat <<'PYTHON_EOF'
import sys
import json

secrets = json.loads(sys.argv[1])
for line in sys.stdin:
    for secret in secrets:
        if secret in line:
            line = line.replace(secret, '[REDACTED:VAULT_SECRET]')
    sys.stdout.write(line)
PYTHON_EOF
)

# SSH to hunter, dump DB, filter through python, gzip, SSH to maybelle
ssh "${HUNTER_USER}@${HUNTER_HOST}" "
    docker exec magenta-postgres pg_dump -U magent magenta_memory | \
    python3 -c '$FILTER_SCRIPT' '$SECRETS_JSON' | \
    gzip | \
    ssh ${MAYBELLE_USER}@${MAYBELLE_HOST} 'cat > /mnt/persist/magenta/backups/${BACKUP_FILENAME}'
"

echo "  Done! Backup saved to maybelle:/mnt/persist/magenta/backups/${BACKUP_FILENAME}"

# Step 3: Verify the backup exists
echo ""
echo "Step 3: Verifying backup on maybelle..."
BACKUP_SIZE=$(ssh "${MAYBELLE_USER}@${MAYBELLE_HOST}" "stat -c%s /mnt/persist/magenta/backups/${BACKUP_FILENAME} 2>/dev/null || echo 0")
if [ "$BACKUP_SIZE" -gt 0 ]; then
    echo "  Backup verified: ${BACKUP_FILENAME} (${BACKUP_SIZE} bytes)"
else
    echo "  ERROR: Backup file not found or empty!"
    exit 1
fi

echo ""
echo "=== Migration complete ==="
echo ""
echo "The filtered backup is at: /mnt/persist/magenta/backups/${BACKUP_FILENAME}"
echo ""
echo "To restore:"
echo "  1. If database is empty: Run chapter-1 (auto-restores from latest backup)"
echo "  2. Manual restore on maybelle:"
echo "     gunzip -c /mnt/persist/magenta/backups/${BACKUP_FILENAME} | docker exec -i magenta-postgres psql -U magent -d magenta_memory"
echo ""
