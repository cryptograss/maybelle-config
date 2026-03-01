#!/bin/bash
#
# Deploy delivery-kid from maybelle
# This script runs ON maybelle and handles the full deploy
#
# The vault password is passed via stdin from the caller's laptop.
#
# Usage from laptop:
#   echo "$ANSIBLE_VAULT_PASSWORD" | ssh root@maybelle.cryptograss.live /mnt/persist/maybelle-config/maybelle/scripts/deploy-delivery-kid.sh [username] [--fresh-host]
#

set -o pipefail
# Note: NOT using 'set -e' because we want to handle errors explicitly

DEPLOY_USER="${1:-remote}"
FRESH_HOST=false
REPO_DIR="/mnt/persist/maybelle-config"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
LOG_FILE="/mnt/persist/logs/delivery-kid-deploy-${TIMESTAMP}.log"
VAULT_FILE="/tmp/vault_pass_$$"
DELIVERY_KID_HOST="delivery-kid.cryptograss.live"
DELIVERY_KID_IP="46.62.220.103"

# Parse arguments
if [ "$2" = "--fresh-host" ] || [ "$1" = "--fresh-host" ]; then
    FRESH_HOST=true
    if [ "$1" = "--fresh-host" ]; then
        DEPLOY_USER="remote"
    fi
fi

echo "============================================================"
echo "DEPLOY DELIVERY-KID FROM MAYBELLE"
if [ "$FRESH_HOST" = true ]; then
    echo "(FRESH HOST - will reset SSH keys)"
fi
echo "============================================================"
echo ""
echo "Deploy user: $DEPLOY_USER"
echo "Target: $DELIVERY_KID_HOST ($DELIVERY_KID_IP)"
echo ""

# Read vault password from stdin
echo "Reading vault password from stdin..."
read -r VAULT_PASSWORD
if [ -z "$VAULT_PASSWORD" ]; then
    echo "ERROR: No vault password provided on stdin"
    exit 1
fi

# Write to temp file
echo "$VAULT_PASSWORD" > "$VAULT_FILE"
chmod 600 "$VAULT_FILE"
echo "✓ Vault password received"

# Ensure log directory exists
mkdir -p /mnt/persist/logs

# Cleanup function (keep logs, only remove vault file)
cleanup() {
    rm -f "$VAULT_FILE"
}
trap cleanup EXIT

# Update repository
echo ""
echo "Updating maybelle-config repository..."
cd "$REPO_DIR"

# Ensure we can fetch all branches (fixes shallow single-branch clones)
git remote set-branches origin '*'
git fetch origin main production

# Hard reset to production (handles force pushes/rebases)
git checkout production 2>/dev/null || git checkout -b production origin/production
git reset --hard origin/production

# Check that production is not behind main
if ! git merge-base --is-ancestor origin/main origin/production; then
    echo "ERROR: production branch is behind main"
    echo "Please update production to include latest main changes"
    exit 1
fi
echo "✓ Repository updated"

# Handle fresh host SSH keys
if [ "$FRESH_HOST" = true ]; then
    echo ""
    echo "============================================================"
    echo "HANDLING FRESH HOST SSH KEYS"
    echo "============================================================"
    echo ""

    echo "Removing old SSH host keys for $DELIVERY_KID_HOST and $DELIVERY_KID_IP..."
    ssh-keygen -R "$DELIVERY_KID_HOST" 2>/dev/null || true
    ssh-keygen -R "$DELIVERY_KID_IP" 2>/dev/null || true
    echo "✓ Old host keys removed"

    echo ""
    echo "Fetching new SSH host key..."
    ssh-keyscan -H "$DELIVERY_KID_HOST" >> ~/.ssh/known_hosts 2>/dev/null
    ssh-keyscan -H "$DELIVERY_KID_IP" >> ~/.ssh/known_hosts 2>/dev/null
    echo "✓ New host key added to known_hosts"
fi

# Run ansible
echo ""
echo "============================================================"
echo "RUNNING ANSIBLE PLAYBOOK"
echo "============================================================"
echo ""

START_TIME=$(date +%s)

cd "$REPO_DIR/delivery-kid/ansible"

# Run ansible playbook
ANSIBLE_CMD="ansible-playbook --vault-password-file=\"$VAULT_FILE\" -i inventory.yml playbook.yml"

if bash -c "$ANSIBLE_CMD" 2>&1 | tee "$LOG_FILE"; then
    DEPLOY_STATUS="success"
    EXIT_CODE=0
    echo ""
    echo "✓ Deployment complete"
else
    DEPLOY_STATUS="failure"
    EXIT_CODE=1
    echo ""
    echo "✗ Deployment failed"
fi

END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))

echo ""
echo "============================================================"
echo "Deployment took ${DURATION} seconds"
echo "Full deployment log saved to: $LOG_FILE"
echo ""
if [ $EXIT_CODE -eq 0 ]; then
    echo "✓ SUCCESS"
    echo ""
    echo "Services should be available at:"
    echo "  - https://delivery-kid.cryptograss.live/api/health"
    echo "  - https://ipfs.delivery-kid.cryptograss.live/ipfs/<CID>"
else
    echo "✗ FAILED"
fi

exit $EXIT_CODE
