#!/bin/bash
# Say something before the disk is full, not after.
#
# hunter reached 90% in September 2026 and nobody knew until someone went
# looking by hand. A full disk here takes out magenta_memory, the MCP server,
# Memory Lane, every agent container and every per-user wiki at once, so the
# warning is worth more than the tidiness.
#
#   --motd   print a banner only when above the warning threshold; silent
#            otherwise, so logins stay quiet on a healthy box
#   (none)   print the current state and exit non-zero above the alarm
#            threshold, for the timer and for anything that wants a signal
#
# Deliberately reports, never deletes. Retention is a separate, reviewable
# thing; this one only has opinions.

set -u

WARN=${HUNTER_DISK_WARN:-85}
ALARM=${HUNTER_DISK_ALARM:-92}
MOUNT=${HUNTER_DISK_MOUNT:-/}

read -r used_pct used avail total < <(
    df -h --output=pcent,used,avail,size "$MOUNT" | tail -1 | tr -d '%'
)

biggest() {
    du -xhd1 /opt /var/lib/docker 2>/dev/null | sort -rh | head -5 | sed 's/^/      /'
}

if [ "${1:-}" = "--motd" ]; then
    if [ "$used_pct" -ge "$ALARM" ]; then
        printf '\n  \033[1;31mDISK %s%% FULL\033[0m — %s used of %s, only %s free on %s\n' \
            "$used_pct" "$used" "$total" "$avail" "$MOUNT"
        printf '  Things will start failing. See maybelle-config#131.\n\n'
    elif [ "$used_pct" -ge "$WARN" ]; then
        printf '\n  \033[1;33mDisk %s%% full\033[0m — %s free of %s on %s\n' \
            "$used_pct" "$avail" "$total" "$MOUNT"
        printf '  Worth a look before it becomes urgent. See maybelle-config#131.\n\n'
    fi
    exit 0
fi

echo "disk $MOUNT: ${used_pct}% used (${used} of ${total}, ${avail} free)"

if [ "$used_pct" -ge "$WARN" ]; then
    echo "  largest directories:"
    biggest
fi

if [ "$used_pct" -ge "$ALARM" ]; then
    echo "ALARM: at or above ${ALARM}%"
    exit 2
elif [ "$used_pct" -ge "$WARN" ]; then
    echo "WARNING: at or above ${WARN}%"
    exit 1
fi
exit 0
