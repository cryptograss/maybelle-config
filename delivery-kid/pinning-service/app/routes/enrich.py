"""Release enrichment — add BitTorrent metadata to Release pages.

Triggered by Jenkins (or manually). Queries PickiPedia for releases
missing bittorrent_infohash, fetches album from local IPFS, generates
deterministic torrent, updates the wiki page.

Requires API key auth (X-API-Key header) and wiki bot credentials
(WIKI_BOT_USER, WIKI_BOT_PASSWORD env vars).
"""

import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

import httpx
import yaml

from fastapi import APIRouter, Depends

from ..auth import require_auth
from ..config import get_settings, Settings
from ..services.torrent import create_torrent, DEFAULT_TRACKERS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/enrich", tags=["enrich"])


class WikiSession:
    """Lightweight wiki API client using httpx."""

    def __init__(self, api_url: str, bot_user: str, bot_password: str):
        self.api_url = api_url
        self.client = httpx.AsyncClient(timeout=60.0)
        self.cookies = {}
        self.bot_user = bot_user
        self.bot_password = bot_password
        self._logged_in = False

    async def login(self):
        if self._logged_in:
            return
        # Get login token
        r = await self.client.get(self.api_url, params={
            "action": "query", "meta": "tokens",
            "type": "login", "format": "json",
        })
        self.cookies.update(r.cookies)
        login_token = r.json()["query"]["tokens"]["logintoken"]

        # Login
        r = await self.client.post(self.api_url, data={
            "action": "login",
            "lgname": self.bot_user,
            "lgpassword": self.bot_password,
            "lgtoken": login_token,
            "format": "json",
        }, cookies=self.cookies)
        self.cookies.update(r.cookies)
        result = r.json().get("login", {})
        if result.get("result") != "Success":
            raise RuntimeError(f"Wiki login failed: {result}")
        self._logged_in = True
        logger.info("Logged in to wiki as %s", self.bot_user)

    async def get_releases_missing_torrent(self):
        r = await self.client.get(self.api_url, params={
            "action": "releaselist",
            "filter": "missing-torrent",
            "format": "json",
        })
        return r.json().get("releases", [])

    async def get_page_content(self, title: str):
        r = await self.client.get(self.api_url, params={
            "action": "query", "titles": title,
            "prop": "revisions", "rvprop": "content",
            "rvslots": "main", "format": "json",
        }, cookies=self.cookies)
        pages = r.json().get("query", {}).get("pages", {})
        for page_id, page in pages.items():
            if page_id == "-1":
                return None
            revisions = page.get("revisions", [])
            if revisions:
                return revisions[0].get("slots", {}).get("main", {}).get("*", "")
        return None

    async def edit_page(self, title: str, content: str, summary: str):
        await self.login()
        # Get CSRF token
        r = await self.client.get(self.api_url, params={
            "action": "query", "meta": "tokens", "format": "json",
        }, cookies=self.cookies)
        self.cookies.update(r.cookies)
        csrf_token = r.json()["query"]["tokens"]["csrftoken"]

        r = await self.client.post(self.api_url, data={
            "action": "edit", "title": title,
            "text": content, "summary": summary,
            "token": csrf_token, "bot": "1", "format": "json",
        }, cookies=self.cookies)
        self.cookies.update(r.cookies)
        result = r.json()
        if "error" in result:
            raise RuntimeError(f"Wiki edit failed: {result['error']}")
        return result

    async def close(self):
        await self.client.aclose()


async def fetch_ipfs_directory(cid: str, ipfs_api_url: str) -> Path | None:
    """Fetch a CID directory from local IPFS to a temp dir."""
    tmpdir = Path(tempfile.mkdtemp(prefix="enrich-"))
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            r = await client.post(
                f"{ipfs_api_url}/api/v0/get",
                params={"arg": cid, "archive": "true"},
            )
            if r.status_code != 200:
                logger.warning("IPFS get failed for %s: %s", cid, r.status_code)
                shutil.rmtree(tmpdir)
                return None

        # Write and extract tar
        tar_path = tmpdir / "archive.tar"
        tar_path.write_bytes(r.content)
        subprocess.run(
            ["tar", "xf", str(tar_path), "-C", str(tmpdir)],
            capture_output=True, check=True,
        )
        tar_path.unlink()

        # Find extracted directory
        for child in tmpdir.iterdir():
            if child.is_dir():
                return child
        shutil.rmtree(tmpdir)
        return None
    except Exception as e:
        logger.error("Error fetching %s: %s", cid, e)
        shutil.rmtree(tmpdir, ignore_errors=True)
        return None


def add_torrent_to_yaml(yaml_content: str, infohash: str, trackers: list[str]) -> str:
    """Add bittorrent fields to release YAML."""
    try:
        data = yaml.safe_load(yaml_content)
        if not isinstance(data, dict):
            data = {}
    except yaml.YAMLError:
        data = {}

    if data.get("bittorrent_infohash"):
        return yaml_content  # Already has it

    data["bittorrent_infohash"] = infohash
    data["bittorrent_trackers"] = trackers

    return yaml.dump(data, default_flow_style=False, allow_unicode=True)


@router.post("/releases")
async def enrich_releases(
    identity: str = Depends(require_auth),
    settings: Settings = Depends(get_settings),
):
    """
    Enrich Release pages with BitTorrent metadata.

    For each Release missing bittorrent_infohash:
    1. Fetch album directory from local IPFS
    2. Generate deterministic .torrent
    3. Update Release page YAML

    Requires auth (API key or wallet). Returns detailed log.
    """
    import os
    wiki_bot_user = os.environ.get("WIKI_BOT_USER", "")
    wiki_bot_password = os.environ.get("WIKI_BOT_PASSWORD", "")
    wiki_api_url = os.environ.get("WIKI_API_URL", "https://pickipedia.xyz/api.php")

    if not wiki_bot_user or not wiki_bot_password:
        return {
            "success": False,
            "error": "WIKI_BOT_USER and WIKI_BOT_PASSWORD not configured",
            "log": [],
        }

    wiki = WikiSession(wiki_api_url, wiki_bot_user, wiki_bot_password)
    log_entries = []

    try:
        releases = await wiki.get_releases_missing_torrent()
        log_entries.append({
            "event": "query",
            "message": f"Found {len(releases)} releases missing BitTorrent metadata",
        })

        if not releases:
            return {"success": True, "updated": 0, "errors": 0, "log": log_entries}

        updated = 0
        errors = 0

        for release in releases:
            cid = release["ipfs_cid"]
            title = release.get("title") or cid
            page_title = f"Release:{release['page_title']}"

            log_entries.append({
                "event": "processing",
                "cid": cid,
                "title": title,
            })

            # Fetch from IPFS
            album_dir = await fetch_ipfs_directory(cid, settings.ipfs_api_url)
            if album_dir is None:
                log_entries.append({
                    "event": "error",
                    "cid": cid,
                    "message": "Could not fetch from IPFS",
                })
                errors += 1
                continue

            try:
                # Generate torrent
                result = create_torrent(
                    directory=album_dir,
                    name=cid,
                    webseeds=[f"https://ipfs.io/ipfs/{cid}/"],
                )

                if not result.success:
                    log_entries.append({
                        "event": "error",
                        "cid": cid,
                        "message": f"Torrent generation failed: {result.error}",
                    })
                    errors += 1
                    continue

                log_entries.append({
                    "event": "torrent",
                    "cid": cid,
                    "infohash": result.infohash,
                    "file_count": result.file_count,
                    "total_size": result.total_size,
                    "piece_length": result.piece_length,
                })

                # Update wiki page
                current_content = await wiki.get_page_content(page_title)
                if current_content is None:
                    log_entries.append({
                        "event": "error",
                        "cid": cid,
                        "message": f"Could not read {page_title}",
                    })
                    errors += 1
                    continue

                new_content = add_torrent_to_yaml(
                    current_content, result.infohash, DEFAULT_TRACKERS
                )

                if new_content == current_content:
                    log_entries.append({
                        "event": "skip",
                        "cid": cid,
                        "message": "Already has infohash",
                    })
                    continue

                await wiki.edit_page(
                    page_title, new_content,
                    f"Add BitTorrent metadata (infohash: {result.infohash[:12]}...)"
                )
                log_entries.append({
                    "event": "updated",
                    "cid": cid,
                    "infohash": result.infohash,
                    "page": page_title,
                })
                updated += 1

            finally:
                # Cleanup temp directory
                parent = album_dir.parent
                shutil.rmtree(parent, ignore_errors=True)

        return {
            "success": True,
            "updated": updated,
            "errors": errors,
            "total": len(releases),
            "log": log_entries,
        }

    except Exception as e:
        log_entries.append({"event": "fatal", "message": str(e)})
        return {
            "success": False,
            "error": str(e),
            "log": log_entries,
        }

    finally:
        await wiki.close()
