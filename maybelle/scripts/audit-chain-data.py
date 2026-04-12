#!/usr/bin/env python3
"""Check Blue Railroad chain data against wiki Release pages.

Run inside the Jenkins container on maybelle:
  docker exec jenkins python3 /path/to/audit-chain-data.py
"""

import json
import urllib.request

CHAIN_DATA = "/var/jenkins_home/shared/chain_data/chainData.json"
WIKI_API = "https://pickipedia.xyz/api.php"

SONG_MAP = {
    '5': ('Blue Railroad Train', 'Squats'),
    '6': ('Nine Pound Hammer', 'Pushups'),
    '7': ('Blue Railroad Train', 'Squats'),
    '10': ('Ginseng Sullivan', 'Army Crawls'),
}

BASE58_ALPHABET = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'


def video_hash_to_cidv0(video_hash):
    if not video_hash:
        return None
    hex_str = video_hash[2:] if video_hash.startswith('0x') else video_hash
    if not hex_str or hex_str == '0' * 64:
        return None
    try:
        digest = bytes.fromhex(hex_str)
        multihash = bytes([0x12, 0x20]) + digest
        leading_zeros = sum(1 for b in multihash if b == 0)
        num = int.from_bytes(multihash, 'big')
        result = []
        while num > 0:
            num, remainder = divmod(num, 58)
            result.append(BASE58_ALPHABET[remainder])
        return '1' * leading_zeros + ''.join(reversed(result))
    except Exception:
        return None


def main():
    # Load chain data
    with open(CHAIN_DATA) as f:
        d = json.load(f)

    # Build token info
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

            song_exercise = SONG_MAP.get(song_id)
            if song_exercise:
                song_name, exercise = song_exercise
                expected_title = f"{song_name} ({exercise}) #{tid}"
            else:
                expected_title = None

            tokens[tid] = {
                'cid': cid,
                'song_id': song_id,
                'expected_title': expected_title,
            }

    # Fetch releases from wiki
    url = f"{WIKI_API}?action=releaselist&format=json"
    with urllib.request.urlopen(url, timeout=30) as resp:
        releases = json.loads(resp.read().decode())

    release_by_cid = {}
    for r in releases.get('releases', []):
        cid = r.get('ipfs_cid') or r.get('page_title', '')
        release_by_cid[cid.lower()] = r

    # Check each token
    issues = 0
    for tid in sorted(tokens, key=lambda x: int(x)):
        t = tokens[tid]
        cid = t['cid']
        if not cid:
            continue

        rel = release_by_cid.get(cid.lower())
        if not rel:
            print(f"  NO RELEASE: Token #{tid} CID={cid[:16]}...")
            issues += 1
        elif t['expected_title'] and rel.get('title') != t['expected_title']:
            print(f"  TITLE MISMATCH: Token #{tid}")
            print(f"    expected: {t['expected_title']}")
            print(f"    actual:   {rel.get('title', '(none)')}")
            issues += 1

    if issues == 0:
        print("  All tokens have matching Release pages with correct titles")


if __name__ == '__main__':
    main()
