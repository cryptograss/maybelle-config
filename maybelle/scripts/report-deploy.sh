#!/bin/bash
# Tell the Moods (memory-lane) that a server's redeploy started, finished or failed.
#
#   report-deploy.sh <hunter|maybelle|delivery-kid|pickipedia> <started|finished|failed> [who] [note]
#
# The note, when a deploy has ended, is its log's page on PickiPedia
# (post-deploy-log.py): the Moods link it. An end also carries the commits the
# deploy's playbook put on the server, from <server>.commits.json.
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
# The commits this deploy put on the server, by repository ({"magenta": "<sha>"}), as its playbook wrote them
# (the ramp's "live on <server>"; Cryptograss:Moods/ramp/Plan, section 4). A start clears the last deploy's, so an
# end only ever sends what this one wrote; one that wrote none sends none.
COMMITS_FILE="${DEPLOY_COMMITS_DIR:-/mnt/persist/deploys}/$SERVER.commits.json"
if [ "$STATE" = "started" ]; then
    mkdir -p "$(dirname "$COMMITS_FILE")" && rm -f "$COMMITS_FILE"
fi
BODY=$(python3 - "$SERVER" "$STATE" "$BY" "$COMMIT" "$NOTE" "$COMMITS_FILE" <<'PY'
import json, sys
server, state, by, commit, note, commits_file = sys.argv[1:]
body = {'server': server, 'state': state, 'by': by, 'commit': commit, 'note': note}
if state != 'started':
    try:
        with open(commits_file) as f:
            commits = json.load(f)
        if isinstance(commits, dict):
            body['commits'] = {str(k): str(v) for k, v in commits.items()}
    except (OSError, ValueError):
        pass
print(json.dumps(body))
PY
)

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
