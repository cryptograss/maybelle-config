#!/usr/bin/env python3
"""Repackage pre-10-Nov fMP4 video Releases so iPhones play them.

Calls delivery-kid's ``POST /enrich/repackage`` endpoint (maybelle-config#298)
for every video Release on PickiPedia whose video init segment still carries
ffmpeg 7.1's empty 12-byte ``sdtp`` box (or for explicit CIDs passed on the
command line).

Reads ``delivery_kid_api_key`` from ``secrets/vault.yml`` via
``ANSIBLE_VAULT_PASSWORD_FILE`` (or ``ANSIBLE_VAULT_PASSWORD``), same as
``reseed-cid.py`` and the deploy scripts, or from ``DELIVERY_KID_API_KEY``.

Usage:
  maybelle/scripts/repackage-sdtp-releases.py --dry-run
  maybelle/scripts/repackage-sdtp-releases.py
  maybelle/scripts/repackage-sdtp-releases.py QmYacRRA9WjdwwvbcmF8ujYZDvBN9n5m39atTSmoX4fWV1
"""

import argparse
import concurrent.futures
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import yaml


DELIVERY_KID_URL = "https://delivery-kid.cryptograss.live"
IPFS_GATEWAY = "https://ipfs.delivery-kid.cryptograss.live/ipfs"
WIKI_API = "https://pickipedia.xyz/api.php"
NS_RELEASE = 3004
VAULT_PATH = Path(__file__).resolve().parents[2] / "secrets" / "vault.yml"
USER_AGENT = "cryptograss-repackage/1.0"


def load_api_key() -> str:
    """Return the delivery-kid API key from env or ansible-vault."""
    explicit = os.environ.get("DELIVERY_KID_API_KEY")
    if explicit:
        return explicit

    cmd = ["ansible-vault", "view", str(VAULT_PATH)]
    if os.environ.get("ANSIBLE_VAULT_PASSWORD_FILE"):
        cmd += ["--vault-password-file", os.environ["ANSIBLE_VAULT_PASSWORD_FILE"]]
    elif not os.environ.get("ANSIBLE_VAULT_PASSWORD"):
        raise RuntimeError(
            "Neither DELIVERY_KID_API_KEY nor ANSIBLE_VAULT_PASSWORD(_FILE) is set"
        )

    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"ansible-vault view failed: {result.stderr.strip()}")
    data = yaml.safe_load(result.stdout) or {}
    key = data.get("delivery_kid_api_key")
    if not key:
        raise RuntimeError(f"delivery_kid_api_key missing from {VAULT_PATH}")
    return key


def _http_bytes(url: str, timeout: int = 15) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _wiki_api(params: dict) -> dict:
    query = urllib.parse.urlencode({**params, "format": "json", "formatversion": "2"})
    return json.loads(_http_bytes(f"{WIKI_API}?{query}", timeout=20).decode("utf-8"))


def normalize_cid(cid: str) -> str:
    return cid.lower() if cid.startswith("Bafy") else cid


def init_segment_has_sdtp(cid: str) -> bool:
    """Check whether a video Release's first video rendition carries an sdtp box."""
    base = f"{IPFS_GATEWAY}/{normalize_cid(cid)}"
    try:
        m3u8 = _http_bytes(f"{base}/master.m3u8", timeout=10).decode("utf-8", "replace")
    except Exception:
        return False
    streams = [
        line.strip()
        for line in m3u8.splitlines()
        if line.strip() and not line.startswith("#")
    ]
    first_video = next((s for s in streams if "audio" not in s), None)
    if not first_video or "/" not in first_video:
        return False
    sub_dir = first_video.rsplit("/", 1)[0]
    try:
        playlist = _http_bytes(f"{base}/{first_video}", timeout=10).decode("utf-8", "replace")
    except Exception:
        return False
    match = re.search(r'#EXT-X-MAP:URI="([^"]+)"', playlist)
    if not match:
        return False
    map_uri = match.group(1)
    if map_uri.startswith("/"):
        return False
    try:
        init_bytes = _http_bytes(f"{base}/{sub_dir}/{map_uri}", timeout=10)
    except Exception:
        return False
    return b"sdtp" in init_bytes


def find_sdtp_video_releases() -> list[tuple[str, str]]:
    """Return [(cid, title), ...] for every non-redirect video Release with sdtp."""
    pages = _wiki_api(
        {
            "action": "query",
            "list": "allpages",
            "apnamespace": str(NS_RELEASE),
            "apfilterredir": "nonredirects",
            "aplimit": "500",
        }
    )["query"]["allpages"]
    titles = [p["title"] for p in pages if "/" not in p["title"]]

    videos: list[tuple[str, str]] = []
    for i in range(0, len(titles), 50):
        batch = titles[i : i + 50]
        res = _wiki_api(
            {
                "action": "query",
                "prop": "revisions",
                "rvprop": "content",
                "rvslots": "main",
                "titles": "|".join(batch),
            }
        )
        for page in res["query"]["pages"]:
            rev = (page.get("revisions") or [None])[0]
            if not rev:
                continue
            try:
                data = yaml.safe_load(rev["slots"]["main"]["content"])
            except Exception:
                continue
            if isinstance(data, dict) and data.get("release_type") == "video":
                cid = page["title"].split(":", 1)[1]
                videos.append((cid, str(data.get("title") or "")))

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        flags = list(pool.map(lambda item: init_segment_has_sdtp(item[0]), videos))

    return [item for item, has_sdtp in zip(videos, flags) if has_sdtp]


def repackage_cid(cid: str, api_key: str, *, dry_run: bool = False) -> dict:
    """Call POST /enrich/repackage on delivery-kid for one CID."""
    url = f"{DELIVERY_KID_URL}/enrich/repackage"
    payload = json.dumps({"cid": normalize_cid(cid), "dry_run": dry_run}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "X-API-Key": api_key,
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "cids",
        nargs="*",
        help="Explicit CID(s) to repackage; omit to scan PickiPedia for all video Releases with sdtp",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List which init segments delivery-kid would rewrite without pinning anything",
    )
    parser.add_argument(
        "--json-out",
        help="Optional path to write the [{old_cid, new_cid, title, fixed}] JSON mapping",
    )
    args = parser.parse_args(argv)

    try:
        api_key = load_api_key()
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print(
            "  Set ANSIBLE_VAULT_PASSWORD_FILE (same as deploy scripts) or DELIVERY_KID_API_KEY.",
            file=sys.stderr,
        )
        return 2

    if args.cids:
        targets = [(cid, "") for cid in args.cids]
    else:
        print("Scanning PickiPedia video Releases for init segments carrying sdtp...")
        targets = find_sdtp_video_releases()
        print(f"Found {len(targets)} video Release(s) needing repackage.\n")

    if not targets:
        print("Nothing to repackage.")
        return 0

    mode = "DRY RUN" if args.dry_run else "REPACKAGE"
    print(f"=== {mode} ({len(targets)} release(s)) via {DELIVERY_KID_URL} ===")
    mappings: list[dict] = []
    failures = 0

    for cid, title in targets:
        label = f" ({title})" if title else ""
        try:
            res = repackage_cid(cid, api_key, dry_run=args.dry_run)
        except Exception as exc:
            print(f"  FAIL  {cid}{label}: {exc}")
            failures += 1
            continue

        if not res.get("success"):
            print(f"  FAIL  {cid}{label}: {res.get('error')}")
            failures += 1
            continue

        fixed = res.get("fixed") or []
        new_cid = res.get("new_cid")
        if args.dry_run:
            print(f"  dry   {cid}{label}: {len(fixed)} init segment(s) -> {fixed}")
        elif new_cid:
            print(f"  ok    {cid} -> {new_cid}{label}")
            mappings.append(
                {"old_cid": cid, "new_cid": new_cid, "title": title, "fixed": fixed}
            )
        else:
            print(f"  skip  {cid}{label}: no sdtp segments needed fixing")

    if mappings:
        print("\n=== Paste into #uploads-and-embeds so magent can move the Release pages ===")
        for item in mappings:
            suffix = f"  # {item['title']}" if item["title"] else ""
            print(f"{item['old_cid']} -> {item['new_cid']}{suffix}")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(mappings, indent=2) + "\n", encoding="utf-8")
        print(f"\nWrote mapping to {args.json_out}")

    if failures:
        print(f"\n{failures} of {len(targets)} failed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
