#!/bin/bash
#
# Run Blue Railroad import manually on maybelle
#
# Usage from laptop:
#   ssh root@maybelle.cryptograss.live /mnt/persist/maybelle-config/maybelle/scripts/run-bluerailroad-import.sh
#

set -euo pipefail

CHAIN_DATA="/var/jenkins_home/shared/chain_data/chainData.json"
PYTHON="/opt/blue-railroad-import/bin/python"

echo "=== Blue Railroad Import ==="

# What's installed is this repository's maybelle/blue-railroad-import/, as of the last deploy: the
# bot had a repository and a checkout of its own until 23 Nov 2026, and `git log` in the container
# was how you saw which commit was running. This checkout, on maybelle, is where it comes from now.
echo -n "blue-railroad-import (maybelle-config): "
git -C /mnt/persist/maybelle-config log --oneline -1 -- maybelle/blue-railroad-import 2>/dev/null || echo "unknown"
echo ""

# Check chain data exists
docker exec jenkins test -f "$CHAIN_DATA" || {
    echo "ERROR: Chain data not found at $CHAIN_DATA"
    exit 1
}

echo "Running import..."
docker exec jenkins bash -c "
    $PYTHON -m blue_railroad_import.cli import \
        --chain-data $CHAIN_DATA \
        --wiki-url https://pickipedia.xyz \
        --username \"\$BLUERAILROAD_BOT_USERNAME\" \
        --password \"\$BLUERAILROAD_BOT_PASSWORD\" \
        -v
"

echo ""
echo "Running torrent enrichment..."
docker exec jenkins bash -c "
    $PYTHON -m blue_railroad_import.cli enrich-torrents \
        --wiki-url https://pickipedia.xyz \
        --username \"\$BLUERAILROAD_BOT_USERNAME\" \
        --password \"\$BLUERAILROAD_BOT_PASSWORD\" \
        --delivery-kid-api-key \"\$DELIVERY_KID_API_KEY\" \
        -v
"

echo ""
echo "Running IPFS metadata enrichment..."
docker exec jenkins bash -c "
    $PYTHON -m blue_railroad_import.cli enrich-ipfs \
        --wiki-url https://pickipedia.xyz \
        --username \"\$BLUERAILROAD_BOT_USERNAME\" \
        --password \"\$BLUERAILROAD_BOT_PASSWORD\" \
        -v
"

echo ""
echo "=== Complete ==="
