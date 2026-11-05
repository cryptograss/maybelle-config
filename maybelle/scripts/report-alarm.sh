#!/bin/bash
# Tell #ops when one of a Jenkins job's checks turns red, or green again.
#
#   report-alarm.sh <job> <status dir>
#
# Each stage of the job leaves a file in <status dir>, named for the stage,
# holding its shell's exit code (cron-health.groovy's `trap ... EXIT`). This
# compares each with what was last said for that stage and posts to
# memory-lane's /api/alarms/ only on a change: red when a check starts
# failing, green when it recovers. Never once per run: cron-health runs every
# five minutes. A stage nothing has been said of yet counts as green, so a
# check that's already red is said once, the first time.
#
# What was said is kept in $JENKINS_HOME/alarm-state/<job>/, and changes only
# when memory-lane took the post (201): an alarm it couldn't take is tried
# again next run. Without the key (vault: memory_lane_alarm_key, as
# MOOD_ALARM_KEY in Jenkins' environment) it says so and does nothing.
# Never fails the build.

JOB="$1"
STATUS_DIR="$2"
STATE_DIR="${JENKINS_HOME:-/var/jenkins_home}/alarm-state/$JOB"
URL="${MEMORY_LANE_URL:-https://memory-lane.maybelle.cryptograss.live}/api/alarms/"

if [ -z "$JOB" ] || [ ! -d "$STATUS_DIR" ]; then
    echo "report-alarm: no job, or no status dir at $STATUS_DIR"
    exit 0
fi
if [ -z "${MOOD_ALARM_KEY:-}" ]; then
    echo "⚠ No MOOD_ALARM_KEY; not telling #ops"
    exit 0
fi
mkdir -p "$STATE_DIR"

for f in "$STATUS_DIR"/*; do
    [ -f "$f" ] || continue
    stage=$(basename "$f")
    code=$(tr -dc '0-9' < "$f")
    now=green
    [ "${code:-1}" = "0" ] || now=red
    was=$(cat "$STATE_DIR/$stage" 2>/dev/null || echo green)
    [ "$now" = "$was" ] && continue
    body=$(python3 -c 'import json, sys; print(json.dumps(dict(zip(("job", "stage", "state", "url"), sys.argv[1:]))))' \
        "$JOB" "$stage" "$now" "${BUILD_URL:-}")
    # The key goes in as a header file, never on curl's command line, where ps would show it.
    code=$(curl -s -o /dev/null -w "%{http_code}" -m 10 -X POST \
        -H "Content-Type: application/json" \
        -H @<(printf 'Authorization: Bearer %s\n' "$MOOD_ALARM_KEY") \
        --data "$body" "$URL" 2>/dev/null)
    if [ "$code" = "201" ]; then
        echo "$now" > "$STATE_DIR/$stage"
        echo "✓ Told #ops: $JOB · $stage is $now"
    else
        echo "⚠ Could not tell #ops ($JOB · $stage $now: HTTP $code); trying again next run"
    fi
done
exit 0
