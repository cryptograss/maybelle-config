#!/usr/bin/env python3
"""
Release reconciliation — ensures all Release pages on PickiPedia
have deterministic BitTorrent metadata.

Designed to run as a cron job on the delivery-kid VPS.

For each Release missing a bittorrent_infohash:
  1. Fetch the album directory from local IPFS by CID
  2. Generate a deterministic .torrent file (CID as torrent name)
  3. Pin the .torrent file to IPFS
  4. Update the Release page YAML via MediaWiki API

Idempotent: safe to run repeatedly. Skips releases that already
have an infohash. Deterministic: same files always produce the
same infohash, so even if everything is rebuilt from scratch,
the results are identical.

Usage:
  python reconcile_releases.py              # run once
  python reconcile_releases.py --dry-run    # show what would change

Environment variables:
  WIKI_API_URL       - MediaWiki API endpoint (default: https://pickipedia.xyz/api.php)
  WIKI_BOT_USER      - Bot username for MediaWiki login
  WIKI_BOT_PASSWORD   - Bot password for MediaWiki login
  IPFS_API_URL       - IPFS API endpoint (default: http://ipfs:5001)
  IPFS_GATEWAY_URL   - IPFS gateway for webseeds (default: https://ipfs.io)
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Add parent directory to path so we can import the torrent module
sys.path.insert(0, str(Path(__file__).parent / "services"))

from services.torrent import create_torrent, DEFAULT_TRACKERS

# --- Configuration ---

WIKI_API_URL = os.environ.get("WIKI_API_URL", "https://pickipedia.xyz/api.php")
WIKI_BOT_USER = os.environ.get("WIKI_BOT_USER", "")
WIKI_BOT_PASSWORD = os.environ.get("WIKI_BOT_PASSWORD", "")
IPFS_API_URL = os.environ.get("IPFS_API_URL", "http://ipfs:5001")
IPFS_GATEWAY_URL = os.environ.get("IPFS_GATEWAY_URL", "https://ipfs.io")


def log(msg):
    print(f"[reconcile] {msg}", flush=True)


# --- Wiki API helpers ---

class WikiSession:
    """Handles MediaWiki API authentication and requests."""

    def __init__(self, api_url, bot_user, bot_password):
        import requests
        self.api_url = api_url
        self.session = requests.Session()
        self.logged_in = False

        if bot_user and bot_password:
            self._login(bot_user, bot_password)

    def _login(self, user, password):
        # Step 1: get login token
        r = self.session.get(self.api_url, params={
            "action": "query",
            "meta": "tokens",
            "type": "login",
            "format": "json",
        })
        login_token = r.json()["query"]["tokens"]["logintoken"]

        # Step 2: login
        r = self.session.post(self.api_url, data={
            "action": "login",
            "lgname": user,
            "lgpassword": password,
            "lgtoken": login_token,
            "format": "json",
        })
        result = r.json().get("login", {})
        if result.get("result") != "Success":
            raise RuntimeError(f"Wiki login failed: {result}")
        log(f"Logged in to wiki as {user}")
        self.logged_in = True

    def get_releases_missing_torrent(self):
        """Query releases that have an IPFS CID but no bittorrent_infohash."""
        r = self.session.get(self.api_url, params={
            "action": "releaselist",
            "filter": "missing-torrent",
            "format": "json",
        })
        data = r.json()
        return data.get("releases", [])

    def get_page_content(self, title):
        """Get raw page content."""
        r = self.session.get(self.api_url, params={
            "action": "query",
            "titles": title,
            "prop": "revisions",
            "rvprop": "content",
            "rvslots": "main",
            "format": "json",
        })
        pages = r.json().get("query", {}).get("pages", {})
        for page_id, page in pages.items():
            if page_id == "-1":
                return None
            revisions = page.get("revisions", [])
            if revisions:
                return revisions[0].get("slots", {}).get("main", {}).get("*", "")
        return None

    def edit_page(self, title, content, summary):
        """Edit a wiki page."""
        # Get CSRF token
        r = self.session.get(self.api_url, params={
            "action": "query",
            "meta": "tokens",
            "format": "json",
        })
        csrf_token = r.json()["query"]["tokens"]["csrftoken"]

        r = self.session.post(self.api_url, data={
            "action": "edit",
            "title": title,
            "text": content,
            "summary": summary,
            "token": csrf_token,
            "format": "json",
        })
        result = r.json()
        if "error" in result:
            raise RuntimeError(f"Wiki edit failed: {result['error']}")
        return result


# --- IPFS helpers ---

def ipfs_get_directory(cid, output_dir):
    """Fetch a directory from IPFS to a local path using ipfs CLI or API."""
    # Try using the kubo RPC API
    import requests
    try:
        # Use /api/v0/get to download a tar archive of the directory
        r = requests.post(
            f"{IPFS_API_URL}/api/v0/get",
            params={"arg": cid, "archive": "true"},
            stream=True,
            timeout=300,
        )
        if r.status_code != 200:
            log(f"  IPFS API /get failed ({r.status_code}), trying ipfs CLI...")
            return _ipfs_get_cli(cid, output_dir)

        # Extract tar archive
        import tarfile
        import io
        tar_data = io.BytesIO(r.content)
        with tarfile.open(fileobj=tar_data) as tar:
            tar.extractall(path=output_dir)

        # The tar extracts to output_dir/{cid}/...
        extracted = output_dir / cid
        if extracted.is_dir():
            return extracted
        # Sometimes CID gets capitalized — check for that
        for child in output_dir.iterdir():
            if child.is_dir():
                return child
        return None

    except Exception as e:
        log(f"  IPFS API error: {e}, trying CLI...")
        return _ipfs_get_cli(cid, output_dir)


def _ipfs_get_cli(cid, output_dir):
    """Fallback: use ipfs CLI if available."""
    try:
        result = subprocess.run(
            ["ipfs", "get", cid, "-o", str(output_dir / cid)],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode == 0:
            return output_dir / cid
        log(f"  ipfs get failed: {result.stderr[:200]}")
        return None
    except FileNotFoundError:
        log("  ipfs CLI not found")
        return None


def ipfs_add_file(file_path):
    """Add a file to IPFS and return its CID."""
    import requests
    try:
        with open(file_path, "rb") as f:
            r = requests.post(
                f"{IPFS_API_URL}/api/v0/add",
                files={"file": (file_path.name, f)},
                params={"pin": "true"},
                timeout=60,
            )
        if r.status_code == 200:
            return r.json().get("Hash")
        log(f"  IPFS add failed: {r.status_code}")
        return None
    except Exception as e:
        log(f"  IPFS add error: {e}")
        return None


# --- YAML update helpers ---

def add_torrent_to_yaml(yaml_content, infohash, trackers):
    """Add bittorrent fields to release YAML content.

    Inserts after the last existing field, before any trailing whitespace.
    """
    lines = yaml_content.rstrip().split("\n")

    # Check if already present (shouldn't be, but be safe)
    for line in lines:
        if line.strip().startswith("bittorrent_infohash:"):
            return yaml_content  # Already has it

    # Add the fields
    lines.append(f"bittorrent_infohash: {infohash}")
    if trackers:
        lines.append("bittorrent_trackers:")
        for tracker in trackers:
            lines.append(f"  - {tracker}")

    return "\n".join(lines) + "\n"


# --- Main reconciliation ---

def reconcile(dry_run=False):
    """Run one reconciliation pass."""
    log("Starting reconciliation...")

    wiki = WikiSession(WIKI_API_URL, WIKI_BOT_USER, WIKI_BOT_PASSWORD)

    releases = wiki.get_releases_missing_torrent()
    log(f"Found {len(releases)} releases missing BitTorrent metadata")

    if not releases:
        log("Nothing to do")
        return 0

    updated = 0
    errors = 0

    for release in releases:
        cid = release["ipfs_cid"]
        title = release.get("title") or cid
        page_title = f"Release:{release['page_title']}"

        log(f"Processing {page_title} ({title})...")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Fetch from IPFS
            log(f"  Fetching {cid} from IPFS...")
            album_dir = ipfs_get_directory(cid, tmpdir)
            if album_dir is None:
                log(f"  ERROR: Could not fetch {cid} from IPFS — skipping")
                errors += 1
                continue

            # Generate deterministic torrent
            log("  Generating torrent...")
            torrent_path = tmpdir / f"{cid}.torrent"
            result = create_torrent(
                directory=album_dir,
                name=cid,
                output_path=torrent_path,
                webseeds=[f"{IPFS_GATEWAY_URL}/ipfs/{cid}/"],
            )

            if not result.success:
                log(f"  ERROR: Torrent generation failed: {result.error}")
                errors += 1
                continue

            log(f"  Infohash: {result.infohash}")
            log(f"  Files: {result.file_count}, Size: {result.total_size}, Piece length: {result.piece_length}")

            if dry_run:
                log("  DRY RUN — would update wiki and pin .torrent")
                updated += 1
                continue

            # Pin .torrent file to IPFS
            log("  Pinning .torrent to IPFS...")
            torrent_cid = ipfs_add_file(torrent_path)
            if torrent_cid:
                log(f"  .torrent pinned: {torrent_cid}")
            else:
                log("  WARNING: Could not pin .torrent file (continuing anyway)")

            # Update wiki page
            log("  Updating wiki page...")
            current_content = wiki.get_page_content(page_title)
            if current_content is None:
                log(f"  ERROR: Could not read {page_title}")
                errors += 1
                continue

            new_content = add_torrent_to_yaml(
                current_content, result.infohash, DEFAULT_TRACKERS
            )

            if new_content == current_content:
                log("  Already up to date (infohash already present)")
                continue

            wiki.edit_page(
                page_title,
                new_content,
                f"Add BitTorrent metadata (infohash: {result.infohash[:12]}...)"
            )
            log(f"  Updated {page_title}")
            updated += 1

    log(f"Done: {updated} updated, {errors} errors, {len(releases)} total")
    return errors


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reconcile Release pages with BitTorrent metadata")
    parser.add_argument("--dry-run", action="store_true", help="Show what would change without making changes")
    args = parser.parse_args()

    errors = reconcile(dry_run=args.dry_run)
    sys.exit(1 if errors else 0)
