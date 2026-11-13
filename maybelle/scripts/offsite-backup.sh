#!/bin/bash
# The offsite backup: everything a cold start needs, in one restic repository off maybelle.
# (Justin, #backups, 12-13 Nov 2026. What a cold start needs, and why each thing is here:
# Cryptograss:Backups on PickiPedia. How to restore: maybelle/README.md, which a cold start can
# still read when the wiki is the thing being restored.)
#
# Daily, from root's cron on maybelle. One snapshot holds:
#   - the newest daily dump of the magenta database (backup-postgres.sh makes it at 03:00)
#   - the newest dump of PickiPedia's database (backup-pickipedia.sh, 03:30)
#   - PickiPedia's uploaded files, all of them, mirrored here from the wiki's server first
#   - a dump of GlitchTip's database, made here
#   - githouse's repositories, with its merge log (/srv/git/.merges)
#   - hunter's unpushed git work (pull-hunter-vcs.sh brings it hourly)
#
# Where it goes is OFFSITE_REPO, any restic repository. Until there is a store outside Hetzner
# that is the Storage Box, reached through delivery-kid (OFFSITE_BOX names that host and mount,
# and the run stops if the box isn't mounted there, rather than fill delivery-kid's own disk).
# Moving it is a new OFFSITE_REPO and that store's keys in /etc/offsite-backup.env.
#
# restic encrypts before anything leaves. Its password is the vault's offsite_restic_password;
# a person puts it there, and without it this fails and says so.
#
# Kept: 7 daily, 4 weekly, 12 monthly. Sundays, restic also reads back a part of the repository.
#
# Every run ends with one line in the log, which cron-health's "offsite backup" stage reads:
#   <time> RESULT OK ...         everything was fresh and was sent
#   <time> RESULT DEGRADED ...   a snapshot was sent, but something in it is stale or missing
#   <time> RESULT FAILED ...     nothing was sent
# A part that can't be gathered doesn't stop the rest: a snapshot short one part beats none.

set -uo pipefail

[ -r "${OFFSITE_ENV:-/etc/offsite-backup.env}" ] && . "${OFFSITE_ENV:-/etc/offsite-backup.env}"

LOG="${OFFSITE_LOG:-/var/log/offsite-backup.log}"
PERSIST="${OFFSITE_PERSIST:-/mnt/persist}"
REPO="${OFFSITE_REPO:-}"
BOX="${OFFSITE_BOX:-}"                      # host:mountpoint that must be mounted, or empty
PASSWORD_FILE="${OFFSITE_PASSWORD_FILE:-/root/.offsite-restic-password}"
WIKI_HOST="${OFFSITE_WIKI_HOST:-root@5.78.112.39}"
WIKI_FILES="${OFFSITE_WIKI_FILES:-/var/www/pickipedia/images}"
WIKI_KEY="${OFFSITE_WIKI_KEY:-/root/.ssh/id_ed25519_hunter}"
FRESH_HOURS="${OFFSITE_FRESH_HOURS:-26}"    # the dumps are daily
VCS_FRESH_HOURS="${OFFSITE_VCS_FRESH_HOURS:-3}"   # hunter's work is pulled hourly

STAGE="$PERSIST/offsite-staging"
FILES_MIRROR="$PERSIST/pickipedia/files"

now() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { echo "$(now) $*" >> "$LOG"; }
finish() { log "RESULT $*"; [ "$1" = "OK" ]; exit; }

PROBLEMS=()
problem() { PROBLEMS+=("$1"); log "problem: $1"; }

hours_old() { echo $(( ($(date +%s) - $(stat -c %Y "$1")) / 3600 )); }

# The newest file matching a pattern, added to PATHS; a problem if there is none or it's stale.
PATHS=()
newest() {
    local what="$1" dir="$2" pattern="$3" file age
    file=$(find "$dir" -maxdepth 1 -type f -name "$pattern" -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2-)
    if [ -z "$file" ]; then
        problem "$what: no $pattern in $dir"
        return
    fi
    age=$(hours_old "$file")
    [ "$age" -le "$FRESH_HOURS" ] || problem "$what: newest is $age hours old ($(basename "$file"))"
    PATHS+=("$file")
    log "$what: $(basename "$file"), $(stat -c %s "$file") bytes, $age hours old"
}

# Trims its own log, in place: Jenkins is given this one file, and reads the last RESULT from it.
if [ -f "$LOG" ] && [ "$(wc -l < "$LOG")" -gt 6000 ]; then
    tail -n 4000 "$LOG" > "$LOG.tmp" && cat "$LOG.tmp" > "$LOG"
    rm -f "$LOG.tmp"
fi

log "start"

[ -n "$REPO" ] || finish FAILED "no OFFSITE_REPO"
[ -s "$PASSWORD_FILE" ] || finish FAILED "no restic password at $PASSWORD_FILE (the vault's offsite_restic_password; a person puts it there)"
command -v restic > /dev/null || finish FAILED "restic is not installed"

RESTIC=(restic --repo "$REPO" --password-file "$PASSWORD_FILE")
if [ -n "$BOX" ]; then
    BOX_HOST="${BOX%%:*}"
    BOX_MOUNT="${BOX#*:}"
    SSH_BOX=(ssh -o BatchMode=yes -o ConnectTimeout=20 "$BOX_HOST")
    "${SSH_BOX[@]}" mountpoint -q "$BOX_MOUNT" \
        || finish FAILED "$BOX_MOUNT is not mounted on $BOX_HOST, or $BOX_HOST can't be reached"
    RESTIC+=(-o "sftp.command=${SSH_BOX[*]} -s sftp")
fi

mkdir -p "$STAGE" "$FILES_MIRROR"

# --- gather ---

newest "magenta database" "$PERSIST/magenta/backups/daily" 'magenta_memory_*.dump'
newest "PickiPedia database" "$PERSIST/pickipedia/backups" 'pickipedia_*.sql.gz'

# PickiPedia's uploads, whole. A mirror (--delete): the history is restic's. MediaWiki rebuilds thumb/.
if rsync -a --delete --exclude=/thumb/ --exclude=/temp/ --exclude=/lockdir/ --exclude='*.log' \
        -e "ssh -i $WIKI_KEY -o BatchMode=yes -o ConnectTimeout=20 -o StrictHostKeyChecking=accept-new" \
        "$WIKI_HOST:$WIKI_FILES/" "$FILES_MIRROR/" >> "$LOG" 2>&1; then
    log "PickiPedia files: $(find "$FILES_MIRROR" -type f | wc -l) files"
else
    problem "PickiPedia files: the copy from $WIKI_HOST failed; sending the last copy that worked"
fi
PATHS+=("$FILES_MIRROR")

# GlitchTip: error history only, and small. Whole or not at all.
if docker exec glitchtip-postgres pg_dump -Fc -U glitchtip glitchtip > "$STAGE/glitchtip.dump.tmp" 2>> "$LOG" \
        && [ -s "$STAGE/glitchtip.dump.tmp" ]; then
    mv "$STAGE/glitchtip.dump.tmp" "$STAGE/glitchtip.dump"
else
    rm -f "$STAGE/glitchtip.dump.tmp"
    problem "GlitchTip: pg_dump failed"
fi
[ -f "$STAGE/glitchtip.dump" ] && PATHS+=("$STAGE/glitchtip.dump")

if [ -d "$PERSIST/githouse/git" ]; then
    PATHS+=("$PERSIST/githouse/git")
else
    problem "githouse: no $PERSIST/githouse/git"
fi

VCS="$PERSIST/hunter-vcs/latest"
if [ -f "$VCS/manifest.json" ]; then
    age=$(hours_old "$VCS/manifest.json")
    [ "$age" -le "$VCS_FRESH_HOURS" ] || problem "hunter's unpushed work: last pulled copy is $age hours old"
    PATHS+=("$VCS")
else
    problem "hunter's unpushed work: no $VCS/manifest.json"
fi

# --- send ---

if ! "${RESTIC[@]}" cat config > /dev/null 2>&1; then
    log "no repository at $REPO yet: making one"
    "${RESTIC[@]}" init >> "$LOG" 2>&1 || finish FAILED "couldn't reach or make the repository at $REPO"
fi

"${RESTIC[@]}" backup --host maybelle --tag offsite "${PATHS[@]}" >> "$LOG" 2>&1 \
    || finish FAILED "restic backup failed (see above)"

# --group-by host: the dumps' names change daily, and restic's own grouping (by paths) would
# make every snapshot a group of one, which a keep rule never trims.
"${RESTIC[@]}" forget --host maybelle --tag offsite --group-by host \
        --keep-daily 7 --keep-weekly 4 --keep-monthly 12 --prune >> "$LOG" 2>&1 \
    || problem "restic forget failed: the snapshot is there, old ones weren't trimmed"

if [ "$(date -u +%u)" = "7" ] || [ -n "${OFFSITE_CHECK_NOW:-}" ]; then
    "${RESTIC[@]}" check --read-data-subset=10% >> "$LOG" 2>&1 \
        || problem "restic check failed: the repository may be damaged"
fi

if [ "${#PROBLEMS[@]}" -eq 0 ]; then
    finish OK "${#PATHS[@]} parts sent to $REPO"
fi
finish DEGRADED "sent to $REPO, but: $(IFS=';'; echo "${PROBLEMS[*]}")"
