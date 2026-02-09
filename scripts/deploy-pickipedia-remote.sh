#!/bin/bash
#
# Deploy PickiPedia VPS remotely via maybelle
# Run this from your laptop - it pipes the vault password to maybelle
#
# Usage:
#   ./deploy-pickipedia-remote.sh [--fresh-host]
#
# The script will prompt for your vault password.
#

set -euo pipefail

MAYBELLE="root@maybelle.cryptograss.live"
DEPLOY_SCRIPT="/mnt/persist/maybelle-config/maybelle/scripts/deploy-pickipedia.sh"

# Parse arguments
ARGS=""
if [ "${1:-}" = "--fresh-host" ]; then
    ARGS="--fresh-host"
fi

# Check for ANSIBLE_VAULT_PASSWORD env var, otherwise prompt
if [ -n "${ANSIBLE_VAULT_PASSWORD:-}" ]; then
    VAULT_PASS="$ANSIBLE_VAULT_PASSWORD"
else
    echo -n "Vault password: "
    read -rs VAULT_PASS
    echo ""
fi

if [ -z "$VAULT_PASS" ]; then
    echo "ERROR: No vault password provided"
    exit 1
fi

echo "Deploying PickiPedia VPS via maybelle..."
echo ""

# Pipe vault password to remote script
echo "$VAULT_PASS" | ssh "$MAYBELLE" "$DEPLOY_SCRIPT" $ARGS
