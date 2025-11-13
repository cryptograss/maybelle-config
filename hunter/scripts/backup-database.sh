#!/bin/bash
# Backup magenta database for retrieval by maybelle
set -e

BACKUP_DIR="/var/backups/magenta"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="magenta_memory_${TIMESTAMP}.dump"
CONTAINER_NAME="magenta-postgres"
DB_NAME="magenta_memory"
DB_USER="magent"

# Create backup directory if it doesn't exist
mkdir -p "$BACKUP_DIR"

# Create backup
echo "Creating database backup: $BACKUP_FILE"
docker exec "$CONTAINER_NAME" pg_dump -U "$DB_USER" -Fc "$DB_NAME" > "$BACKUP_DIR/$BACKUP_FILE"

# Create 'latest' symlink
ln -sf "$BACKUP_FILE" "$BACKUP_DIR/latest.dump"

# Count messages to verify
MSG_COUNT=$(docker exec "$CONTAINER_NAME" psql -U "$DB_USER" -d "$DB_NAME" -t -c "SELECT COUNT(*) FROM conversations_message;" | tr -d ' ')
echo "Backup complete: $MSG_COUNT messages"

# Keep only last 7 days of backups
find "$BACKUP_DIR" -name "magenta_memory_*.dump" -mtime +7 -delete
echo "Old backups cleaned up (kept last 7 days)"
