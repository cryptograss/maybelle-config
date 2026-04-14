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

# IPFS empty directory — every kubo node has this, ignore it
IPFS_EMPTY_DIR="qmunllspaccz1vlxqvkxqqlx5r1x345qqfhbsf67hva3nn"

# Check each pin against Release CIDs
ORPHAN_PINS=0
while IFS= read -r pin_cid; do
    pin_lower=$(echo "$pin_cid" | tr '[:upper:]' '[:lower:]')
    [ "$pin_lower" = "$IPFS_EMPTY_DIR" ] && continue
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

# Check Release pin state — show unpinned, deleted, and missing pins
python3 -c "
import json, sys, urllib.request, yaml

pins = set(line.strip().lower() for line in open('/tmp/audit-pins.txt'))
releases = json.loads(urllib.request.urlopen('${WIKI_API}?action=releaselist&format=json', timeout=30).read().decode())

# Also fetch page content for each release to check delete/unpin flags
api = '${WIKI_API}'
deleted = []
retired = []  # unpinned by directive
missing_pin = []

for r in releases.get('releases', []):
    cid = r.get('ipfs_cid') or r.get('page_title', '')
    title = r.get('title', cid[:16])
    cid_lower = cid.lower()
    is_pinned = cid_lower in pins
    pinned_on = r.get('pinned_on')

    # Fetch YAML to check flags
    page_title = 'Release:' + cid
    try:
        url = f'{api}?action=query&titles={page_title}&prop=revisions&rvprop=content&rvslots=main&format=json'
        resp = urllib.request.urlopen(url, timeout=15)
        qdata = json.loads(resp.read().decode())
        page_content = ''
        for p in qdata.get('query', {}).get('pages', {}).values():
            revs = p.get('revisions', [])
            if revs:
                page_content = revs[0].get('slots', {}).get('main', {}).get('*', '')
        ydata = yaml.safe_load(page_content) if page_content else {}
        if not isinstance(ydata, dict):
            ydata = {}
    except:
        ydata = {}

    if ydata.get('delete'):
        deleted.append((cid[:16], title, is_pinned))
    elif ydata.get('unpin'):
        retired.append((cid[:16], title, is_pinned))
    elif not is_pinned:
        missing_pin.append((cid[:16], title))

if deleted:
    print(f'  Deleted releases ({len(deleted)}):')
    for cid, title, pinned in deleted:
        status = 'STILL PINNED' if pinned else 'unpinned'
        print(f'    {cid}... {title} [{status}]')

if retired:
    print(f'  Retired releases ({len(retired)}):')
    for cid, title, pinned in retired:
        status = 'STILL PINNED' if pinned else 'unpinned'
        print(f'    {cid}... {title} [{status}]')

if missing_pin:
    print(f'  MISSING PINS ({len(missing_pin)}):')
    for cid, title in missing_pin:
        print(f'    {cid}... {title}')

if not deleted and not retired and not missing_pin:
    print('  All releases pinned, none deleted or retired')
else:
    active = len(releases.get('releases', [])) - len(deleted) - len(retired)
    print(f'  {active} active, {len(deleted)} deleted, {len(retired)} retired, {len(missing_pin)} missing pins')
" 2>/dev/null

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
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

ssh "$MAYBELLE_HOST" "docker exec -i jenkins python3 -" < "$SCRIPT_DIR/audit-chain-data.py" 2>/dev/null \
    || echo "  (could not check chain data)"

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
