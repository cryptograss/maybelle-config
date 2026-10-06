#!/bin/bash
# Tell the Moods (memory-lane) that a server's redeploy started, finished or failed.
#
#   report-deploy.sh <hunter|maybelle|delivery-kid|pickipedia> <started|finished|failed> [who] [note]
#
# The note, when a deploy has ended, is its log's page on PickiPedia
# (post-deploy-log.py): the Moods link it.
#
# memory-lane announces it in the Moods it concerns (hunter's and maybelle's
# in all of them) and pulses that server's dot while it's under way. This
# never fails a deploy: without the key (vault: memory_lane_deploy_key), or
# with memory-lane unreachable -- as it is while maybelle redeploys -- it
# says so and returns 0.

SERVER="$1"
STATE="$2"
BY="${3:-}"
NOTE="${4:-}"
KEY_FILE="${MEMORY_LANE_DEPLOY_KEY_FILE:-/root/.memory_lane_deploy_key}"
URL="${MEMORY_LANE_URL:-https://memory-lane.maybelle.cryptograss.live}/api/deploys/"
TRIES="${REPORT_TRIES:-1}"

if [ ! -r "$KEY_FILE" ]; then
    echo "⚠ No memory-lane deploy key; not telling the Moods"
    exit 0
fi
COMMIT=$(git -C /mnt/persist/maybelle-config rev-parse --short=12 HEAD 2>/dev/null || true)
BODY=$(python3 -c 'import json, sys; print(json.dumps(dict(zip(("server", "state", "by", "commit", "note"), sys.argv[1:]))))' \
    "$SERVER" "$STATE" "$BY" "$COMMIT" "$NOTE")

for i in $(seq 1 "$TRIES"); do
    # The key goes in as a header file, never on curl's command line, where ps would show it.
    CODE=$(curl -s -o /dev/null -w "%{http_code}" -m 10 -X POST \
        -H "Content-Type: application/json" \
        -H @<(printf 'Authorization: Bearer %s\n' "$(cat "$KEY_FILE")") \
        --data "$BODY" "$URL" 2>/dev/null)  # curl prints 000 itself when it can't connect
    if [ "$CODE" = "201" ]; then
        echo "✓ Told the Moods: $SERVER $STATE"
        exit 0
    fi
    [ "$i" -lt "$TRIES" ] && sleep 10
done
echo "⚠ Could not tell the Moods ($SERVER $STATE: HTTP $CODE)"
exit 0
