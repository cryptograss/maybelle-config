#!/bin/bash
# Daily PickiPedia MySQL backup from VPS
# Pulls dump via SSH for disaster recovery and hunter preview environments

set -euo pipefail

LOG_FILE="/var/log/pickipedia-backup.log"
BACKUP_DIR="/mnt/persist/pickipedia/backups"
SSH_KEY="/root/.ssh/id_ed25519_hunter"
VPS_HOST="5.78.112.39"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S'): $*" >> "$LOG_FILE"
}

log "Starting PickiPedia backup from VPS"

# Ensure backup directory exists
mkdir -p "$BACKUP_DIR"

# Backup filename
BACKUP_FILE="$BACKUP_DIR/pickipedia_$(date +%Y%m%d).sql.gz"

# Run mysqldump on VPS and pipe back
# The VPS has local MySQL with root access via socket
if ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "root@${VPS_HOST}" \
    "mysqldump pickipedia" 2>> "$LOG_FILE" \
    | gzip > "$BACKUP_FILE"; then

    log "Backup successful: $BACKUP_FILE ($(stat -c%s "$BACKUP_FILE") bytes)"

    # Keep last 7 days of backups
    find "$BACKUP_DIR" -name "pickipedia_*.sql.gz" -mtime +7 -delete

    # Sync to hunter for preview environments
    log "Syncing to hunter..."
    if rsync -avz "$BACKUP_DIR"/ root@hunter.cryptograss.live:/opt/magenta/pickipedia-backups/ >> "$LOG_FILE" 2>&1; then
        log "Sync to hunter complete"
    else
        log "WARNING - sync to hunter failed"
    fi

    # Backup images from VPS
    log "Backing up images from VPS..."
    IMAGES_BACKUP_DIR="$BACKUP_DIR/images"
    mkdir -p "$IMAGES_BACKUP_DIR"
    if rsync -avz -e "ssh -i $SSH_KEY -o StrictHostKeyChecking=no" \
        "root@${VPS_HOST}:/var/www/pickipedia/images/" "$IMAGES_BACKUP_DIR/" >> "$LOG_FILE" 2>&1; then
        log "Images backup complete: $(find "$IMAGES_BACKUP_DIR" -type f | wc -l) files"
    else
        log "WARNING - images backup failed"
    fi
else
    log "Backup FAILED"
    rm -f "$BACKUP_FILE"  # Remove partial file
    exit 1
fi
