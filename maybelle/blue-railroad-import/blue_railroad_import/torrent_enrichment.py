"""Enrich Release pages with BitTorrent metadata.

Queries PickiPedia for releases missing bittorrent_infohash,
calls delivery-kid to generate deterministic torrents from IPFS,
and writes the metadata back to the wiki pages.
"""

import json
import logging
import urllib.request
import urllib.parse
from typing import Optional

import yaml

from .wiki_client import WikiClientProtocol, SaveResult

logger = logging.getLogger(__name__)


def get_releases_missing_torrent(wiki_api_url: str) -> list[dict]:
    """Query PickiPedia API for releases missing BitTorrent metadata."""
    params = urllib.parse.urlencode({
        "action": "releaselist",
        "filter": "missing-torrent",
        "format": "json",
    })
    url = f"{wiki_api_url}?{params}"

    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
        return data.get("releases", [])
    except Exception as e:
        logger.error("Failed to query releases: %s", e)
        return []


def generate_torrent_for_cid(
    cid: str,
    delivery_kid_url: str,
    api_key: str,
    name: Optional[str] = None,
) -> Optional[dict]:
    """Call delivery-kid's /enrich/torrent endpoint for a CID.

    Returns the response dict on success, None on failure.
    """
    url = f"{delivery_kid_url}/enrich/torrent"
    body = {"cid": cid}
    if name:
        body["name"] = name
    payload = json.dumps(body).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "X-API-Key": api_key,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=300) as response:
            result = json.loads(response.read().decode("utf-8"))

        if result.get("success"):
            return result
        else:
            logger.warning("Torrent generation failed for %s: %s", cid, result.get("error"))
            return None
    except Exception as e:
        logger.error("Error calling delivery-kid for %s: %s", cid, e)
        return None


def append_torrent_fields(
    existing_yaml: str,
    infohash: str,
    trackers: list[str],
    webseeds: list[str] | None = None,
    torrent_url: str | None = None,
) -> str:
    """Append bittorrent fields to existing Release YAML without reformatting.

    Parses to verify it's valid YAML and doesn't already have the fields,
    but appends to the original string to preserve formatting.
    """
    try:
        data = yaml.safe_load(existing_yaml)
        if not isinstance(data, dict):
            return existing_yaml
    except yaml.YAMLError:
        return existing_yaml

    if data.get("bittorrent_infohash"):
        return existing_yaml  # Already has it

    # Build the new fields as YAML and append
    new_fields = {
        "bittorrent_infohash": infohash,
        "bittorrent_trackers": trackers,
    }
    if webseeds:
        new_fields["bittorrent_webseeds"] = webseeds
    if torrent_url:
        new_fields["bittorrent_torrent_url"] = torrent_url
    suffix = yaml.dump(new_fields, default_flow_style=False, allow_unicode=True)

    # Ensure there's a newline before appending
    base = existing_yaml.rstrip("\n")
    return base + "\n" + suffix


def enrich_releases(
    wiki: WikiClientProtocol,
    delivery_kid_url: str,
    delivery_kid_api_key: str,
) -> list[SaveResult]:
    """Find releases missing torrents and enrich them.

    1. Query PickiPedia API for releases missing bittorrent_infohash
    2. For each, call delivery-kid to generate a deterministic torrent
    3. Append infohash + trackers to the Release page YAML
    4. Save via wiki client (under Blue Railroad bot identity)

    Returns list of SaveResults for all pages processed.
    """
    results = []

    releases = get_releases_missing_torrent(wiki.api_url)
    logger.info("Found %d releases missing BitTorrent metadata", len(releases))

    if not releases:
        return results

    for release in releases:
        cid = release["ipfs_cid"]
        page_title = f"Release:{release['page_title']}"
        title = release.get("title") or cid

        logger.info("  Processing: %s (%s...)", title, cid[:16])

        # Call delivery-kid for torrent generation
        torrent = generate_torrent_for_cid(
            cid, delivery_kid_url, delivery_kid_api_key,
            name=release.get("title"),
        )

        if torrent is None:
            results.append(SaveResult(page_title, "error", f"Torrent generation failed for {cid}"))
            continue

        infohash = torrent["infohash"]
        trackers = torrent["trackers"]
        webseeds = torrent.get("webseeds") or []
        torrent_url = torrent.get("torrent_url")

        logger.info("    Infohash: %s", infohash)
        logger.info("    Files: %s, Size: %s", torrent.get('file_count'), torrent.get('total_size'))

        # Read current page content
        existing_content = wiki.get_page_content(page_title)
        if existing_content is None:
            results.append(SaveResult(page_title, "error", f"Page not found: {page_title}"))
            continue

        # Append torrent fields
        new_content = append_torrent_fields(existing_content, infohash, trackers, webseeds, torrent_url)

        if new_content == existing_content:
            results.append(SaveResult(page_title, "unchanged", "Already has infohash"))
            logger.info("    Skipped (already has infohash)")
            continue

        # Save via wiki client
        summary = f"Add BitTorrent metadata (infohash: {infohash[:12]}...)"
        result = wiki.save_page(page_title, new_content, summary)
        results.append(result)

        logger.info("    %s: %s", result.action, result.message or page_title)

    return results
