#!/bin/bash
#
# Audit delivery-kid storage against PickiPedia Release pages
#
# Checks IPFS pins, seeding directories, and staging drafts against
# what's actually tracked in the wiki. Identifies orphaned storage.
#
# Usage:
#   ./maybelle/scripts/audit-storage-remote.sh
#

set -euo pipefail

DK_HOST="root@delivery-kid.cryptograss.live"
WIKI_API="https://pickipedia.xyz/api.php"

echo "=== Storage Audit ==="
echo ""

# 1. Get all Release CIDs from the wiki
echo "Fetching Release pages from wiki..."
RELEASE_CIDS=$(curl -s "${WIKI_API}?action=query&list=allpages&apnamespace=3004&aplimit=500&format=json" \
    | python3 -c "
import json, sys
d = json.load(sys.stdin)
for p in d.get('query', {}).get('allpages', []):
    # Page title is the CID (in Release: namespace)
    cid = p['title'].split(':', 1)[1] if ':' in p['title'] else p['title']
    print(cid)
")
RELEASE_COUNT=$(echo "$RELEASE_CIDS" | wc -l)
echo "  $RELEASE_COUNT Release pages in wiki"

# 2. Get all ReleaseDraft IDs from the wiki
echo "Fetching ReleaseDraft pages from wiki..."
DRAFT_IDS=$(curl -s "${WIKI_API}?action=query&list=allpages&apnamespace=3006&aplimit=500&format=json" \
    | python3 -c "
import json, sys
d = json.load(sys.stdin)
for p in d.get('query', {}).get('allpages', []):
    draft_id = p['title'].split(':', 1)[1] if ':' in p['title'] else p['title']
    print(draft_id)
")
DRAFT_COUNT=$(echo "$DRAFT_IDS" | wc -l)
echo "  $DRAFT_COUNT ReleaseDraft pages in wiki"

echo ""
echo "--- IPFS Pins ---"
ssh "$DK_HOST" "docker exec ipfs ipfs pin ls --type=recursive -q 2>/dev/null | head -100" > /tmp/audit-pins.txt
PIN_COUNT=$(wc -l < /tmp/audit-pins.txt)
echo "  $PIN_COUNT recursive pins on IPFS node"

# Check each pin against Release CIDs
ORPHAN_PINS=0
while IFS= read -r pin_cid; do
    # Normalize: lowercase for comparison
    pin_lower=$(echo "$pin_cid" | tr '[:upper:]' '[:lower:]')
    found=false
    while IFS= read -r rel_cid; do
        rel_lower=$(echo "$rel_cid" | tr '[:upper:]' '[:lower:]')
        if [ "$pin_lower" = "$rel_lower" ]; then
            found=true
            break
        fi
    done <<< "$RELEASE_CIDS"
    if [ "$found" = false ]; then
        echo "  ORPHAN PIN: $pin_cid"
        ORPHAN_PINS=$((ORPHAN_PINS + 1))
    fi
done < /tmp/audit-pins.txt
echo "  $ORPHAN_PINS orphaned pins (not in any Release page)"

echo ""
echo "--- Seeding Directories ---"
ssh "$DK_HOST" "ls /mnt/storage-box/staging/seeding/ 2>/dev/null || echo '(none)'" > /tmp/audit-seeding.txt
SEED_COUNT=$(grep -c . /tmp/audit-seeding.txt 2>/dev/null || echo 0)
echo "  $SEED_COUNT seeding directories"

while IFS= read -r seed_cid; do
    [ -z "$seed_cid" ] && continue
    [ "$seed_cid" = "(none)" ] && continue
    seed_lower=$(echo "$seed_cid" | tr '[:upper:]' '[:lower:]')
    found=false
    while IFS= read -r rel_cid; do
        rel_lower=$(echo "$rel_cid" | tr '[:upper:]' '[:lower:]')
        if [ "$seed_lower" = "$rel_lower" ]; then
            found=true
            break
        fi
    done <<< "$RELEASE_CIDS"
    if [ "$found" = false ]; then
        size=$(ssh "$DK_HOST" "du -sh /mnt/storage-box/staging/seeding/$seed_cid 2>/dev/null | cut -f1" || echo "?")
        echo "  ORPHAN SEED: $seed_cid ($size)"
    fi
done < /tmp/audit-seeding.txt

echo ""
echo "--- Staging Drafts ---"
ssh "$DK_HOST" "ls /mnt/storage-box/staging/drafts/ 2>/dev/null || echo '(none)'" > /tmp/audit-drafts.txt
STAGING_COUNT=$(grep -c . /tmp/audit-drafts.txt 2>/dev/null || echo 0)
echo "  $STAGING_COUNT draft directories on disk"

while IFS= read -r draft_dir; do
    [ -z "$draft_dir" ] && continue
    [ "$draft_dir" = "(none)" ] && continue
    draft_lower=$(echo "$draft_dir" | tr '[:upper:]' '[:lower:]')
    found=false
    while IFS= read -r wiki_draft; do
        wiki_lower=$(echo "$wiki_draft" | tr '[:upper:]' '[:lower:]')
        if [ "$draft_lower" = "$wiki_lower" ]; then
            found=true
            break
        fi
    done <<< "$DRAFT_IDS"
    if [ "$found" = false ]; then
        size=$(ssh "$DK_HOST" "du -sh /mnt/storage-box/staging/drafts/$draft_dir 2>/dev/null | cut -f1" || echo "?")
        echo "  ORPHAN DRAFT: $draft_dir ($size)"
    fi
done < /tmp/audit-drafts.txt

echo ""
echo "--- Blue Railroad Chain Data vs Releases ---"
MAYBELLE_HOST="root@maybelle.cryptograss.live"
CHAIN_DATA="/var/jenkins_home/shared/chain_data/chainData.json"

ssh "$MAYBELLE_HOST" "docker exec jenkins python3 -c \"
import json

d = json.load(open('$CHAIN_DATA'))

# Build CID-to-token mapping from chain data
# Also need the CIDv0 conversion for V2 tokens
def video_hash_to_cidv0(video_hash):
    if not video_hash:
        return None
    hex_str = video_hash[2:] if video_hash.startswith('0x') else video_hash
    if not hex_str or hex_str == '0' * 64:
        return None
    try:
        digest = bytes.fromhex(hex_str)
        multihash = bytes([0x12, 0x20]) + digest
        ALPHABET = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
        leading_zeros = sum(1 for b in multihash if b == 0)
        num = int.from_bytes(multihash, 'big')
        result = []
        while num > 0:
            num, remainder = divmod(num, 58)
            result.append(ALPHABET[remainder])
        return '1' * leading_zeros + ''.join(reversed(result))
    except:
        return None

SONG_MAP = {'5': 'Blue Railroad Train (Squats)', '6': 'Nine Pound Hammer (Pushups)', '7': 'Blue Railroad Train (Squats)', '10': 'Ginseng Sullivan (Army Crawls)'}

tokens = {}
for key in ['blueRailroads', 'blueRailroadV2s']:
    for tid, t in d.get(key, {}).items():
        song_id = str(t.get('songId', ''))
        uri = t.get('uri', '')
        video_hash = t.get('videoHash', '')

        cid = None
        if video_hash:
            cid = video_hash_to_cidv0(video_hash)
        elif uri and uri.startswith('ipfs://'):
            cid = uri[7:]

        song = SONG_MAP.get(song_id, f'Unknown (song {song_id})')
        expected_title = f'{song} #{tid}' if song_id in SONG_MAP else None
        tokens[tid] = {'cid': cid, 'song_id': song_id, 'song': song, 'expected_title': expected_title}

# Now check against wiki releases
import urllib.request
releases_url = 'https://pickipedia.xyz/api.php?action=releaselist&format=json'
with urllib.request.urlopen(releases_url, timeout=30) as resp:
    releases = json.loads(resp.read().decode())

release_by_cid = {}
for r in releases.get('releases', []):
    cid = r.get('ipfs_cid') or r.get('page_title', '')
    release_by_cid[cid.lower()] = r

for tid, t in sorted(tokens.items(), key=lambda x: int(x[0])):
    cid = t['cid']
    if not cid:
        continue
    rel = release_by_cid.get(cid.lower())
    if not rel:
        print(f'  NO RELEASE: Token #{tid} ({t[\"song\"]}) CID={cid[:16]}...')
    elif t['expected_title'] and rel.get('title') != t['expected_title']:
        print(f'  TITLE MISMATCH: Token #{tid} — expected \\\"{t[\"expected_title\"]}\\\" got \\\"{rel.get(\"title\",\"(none)\")}\\\"')
\"" 2>/dev/null || echo "  (could not check chain data)"

echo ""
echo "--- Summary ---"
echo "  Release pages: $RELEASE_COUNT"
echo "  ReleaseDraft pages: $DRAFT_COUNT"
echo "  IPFS pins: $PIN_COUNT"
echo "  Seeding dirs: $SEED_COUNT"
echo "  Staging drafts: $STAGING_COUNT"

# Cleanup temp files
rm -f /tmp/audit-pins.txt /tmp/audit-seeding.txt /tmp/audit-drafts.txt

echo ""
echo "=== Audit Complete ==="
