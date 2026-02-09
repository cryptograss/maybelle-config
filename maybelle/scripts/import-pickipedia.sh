#!/bin/bash
#
# Import PickiPedia data to VPS from maybelle backups
# This script runs ON maybelle and configures the remote VPS
#
# Usage:
#   /mnt/persist/maybelle-config/maybelle/scripts/import-pickipedia.sh
#

set -euo pipefail

PICKIPEDIA_HOST="5.78.112.39"
SSH_KEY="/root/.ssh/id_ed25519_hunter"
BACKUP_DIR="/mnt/persist/pickipedia/backups"
SECRETS_DIR="/var/jenkins_home/secrets/pickipedia"
STAGE_DIR="/var/jenkins_home/pickipedia_stage"
REMOTE_MW_ROOT="/var/www/pickipedia"

echo "============================================================"
echo "IMPORT PICKIPEDIA DATA TO VPS"
echo "============================================================"
echo ""

# Check prerequisites
if [ ! -f "$SSH_KEY" ]; then
    echo "ERROR: SSH key not found at $SSH_KEY"
    exit 1
fi

if [ ! -d "$BACKUP_DIR" ]; then
    echo "ERROR: Backup directory not found at $BACKUP_DIR"
    exit 1
fi

if [ ! -f "$SECRETS_DIR/LocalSettings.local.php" ]; then
    echo "ERROR: LocalSettings.local.php not found at $SECRETS_DIR"
    exit 1
fi

if [ ! -d "$STAGE_DIR" ]; then
    echo "ERROR: Jenkins staging directory not found at $STAGE_DIR"
    echo "Run a pickipedia-build job first to create the staged build"
    exit 1
fi

# Find latest database backup
LATEST_DB=$(ls -t "$BACKUP_DIR"/pickipedia_*.sql.gz 2>/dev/null | head -1)
if [ -z "$LATEST_DB" ]; then
    echo "ERROR: No database backup found in $BACKUP_DIR"
    exit 1
fi

echo "Using database backup: $LATEST_DB"
echo "Using secrets from: $SECRETS_DIR"
echo ""

# Test SSH connection
echo "Testing SSH connection to $PICKIPEDIA_HOST..."
if ! ssh -i "$SSH_KEY" -o ConnectTimeout=10 -o BatchMode=yes "root@$PICKIPEDIA_HOST" "echo ok" >/dev/null 2>&1; then
    echo "ERROR: Cannot SSH to $PICKIPEDIA_HOST"
    exit 1
fi
echo "✓ SSH connection OK"
echo ""

# Sync built MediaWiki from staging
echo "============================================================"
echo "SYNCING BUILT MEDIAWIKI"
echo "============================================================"
echo ""

echo "Syncing built MediaWiki from staging to VPS..."
echo "This includes all extensions and skins from the Jenkins build."
rsync -avz --progress --delete \
    --exclude 'images/' \
    --exclude 'cache/' \
    --exclude 'LocalSettings.local.php' \
    -e "ssh -i $SSH_KEY" \
    "$STAGE_DIR/" \
    "root@$PICKIPEDIA_HOST:$REMOTE_MW_ROOT/"

# Fix ownership
ssh -i "$SSH_KEY" "root@$PICKIPEDIA_HOST" "chown -R www-data:www-data $REMOTE_MW_ROOT"
echo "✓ Built MediaWiki synced"
echo ""

# Copy database backup to VPS
echo "============================================================"
echo "IMPORTING DATABASE"
echo "============================================================"
echo ""

echo "Copying database backup to VPS..."
scp -i "$SSH_KEY" "$LATEST_DB" "root@$PICKIPEDIA_HOST:/tmp/pickipedia-import.sql.gz"
echo "✓ Database backup copied"

echo "Importing database (this may take a while)..."
ssh -i "$SSH_KEY" "root@$PICKIPEDIA_HOST" bash -s << 'REMOTE_SCRIPT'
set -e
cd /tmp
gunzip -c pickipedia-import.sql.gz | mysql pickipedia
rm -f pickipedia-import.sql.gz
echo "Database imported successfully"
REMOTE_SCRIPT
echo "✓ Database imported"
echo ""

# Copy images
echo "============================================================"
echo "IMPORTING IMAGES"
echo "============================================================"
echo ""

if [ -d "$BACKUP_DIR/images" ]; then
    echo "Syncing images to VPS (this may take a while)..."
    rsync -avz --progress -e "ssh -i $SSH_KEY" \
        "$BACKUP_DIR/images/" \
        "root@$PICKIPEDIA_HOST:$REMOTE_MW_ROOT/images/"
    echo "✓ Images synced"

    # Fix ownership
    ssh -i "$SSH_KEY" "root@$PICKIPEDIA_HOST" "chown -R www-data:www-data $REMOTE_MW_ROOT/images"
    echo "✓ Image ownership fixed"
else
    echo "WARNING: No images directory found at $BACKUP_DIR/images"
    echo "Skipping image import"
fi
echo ""

# Copy LocalSettings.local.php
echo "============================================================"
echo "CONFIGURING SECRETS"
echo "============================================================"
echo ""

echo "Copying LocalSettings.local.php..."
scp -i "$SSH_KEY" "$SECRETS_DIR/LocalSettings.local.php" \
    "root@$PICKIPEDIA_HOST:$REMOTE_MW_ROOT/LocalSettings.local.php"

# Fix ownership and permissions
ssh -i "$SSH_KEY" "root@$PICKIPEDIA_HOST" bash -s << REMOTE_SCRIPT
chown www-data:www-data $REMOTE_MW_ROOT/LocalSettings.local.php
chmod 640 $REMOTE_MW_ROOT/LocalSettings.local.php
REMOTE_SCRIPT
echo "✓ Secrets configured"
echo ""

# Run MediaWiki update
echo "============================================================"
echo "RUNNING MEDIAWIKI UPDATE"
echo "============================================================"
echo ""

ssh -i "$SSH_KEY" "root@$PICKIPEDIA_HOST" bash -s << REMOTE_SCRIPT
cd $REMOTE_MW_ROOT
sudo -u www-data php maintenance/update.php --quick
REMOTE_SCRIPT
echo "✓ MediaWiki update complete"
echo ""

# Verify
echo "============================================================"
echo "VERIFYING"
echo "============================================================"
echo ""

HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 30 "http://$PICKIPEDIA_HOST/wiki/Main_Page" 2>/dev/null || echo "000")
if [ "$HTTP_CODE" = "200" ] || [ "$HTTP_CODE" = "302" ]; then
    echo "✓ Wiki responding (HTTP $HTTP_CODE)"
else
    echo "⚠ Wiki returned HTTP $HTTP_CODE - may need debugging"
fi

echo ""
echo "============================================================"
echo "IMPORT COMPLETE"
echo "============================================================"
echo ""
echo "PickiPedia should now be accessible at:"
echo "  https://pickipedia.xyz (once DNS propagates)"
echo "  http://$PICKIPEDIA_HOST (direct IP)"
echo ""
