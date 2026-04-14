"""Enrich Release pages with file size, type, and thumbnails from IPFS.

Queries the IPFS gateway for each release's CID to determine
file size and content type, then updates the wiki page YAML.
Also generates and uploads thumbnails for video releases.
"""

import json
import logging
import urllib.request
import urllib.parse
from typing import Optional

import yaml

from .wiki_client import WikiClientProtocol, SaveResult
from .thumbnail import get_thumbnail_filename, generate_thumbnail

logger = logging.getLogger(__name__)


def get_all_releases(wiki_api_url: str) -> list[dict]:
    """Query PickiPedia API for all releases."""
    params = urllib.parse.urlencode({
        "action": "releaselist",
        "filter": "all",
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


def probe_ipfs_cid(cid: str, gateway_url: str) -> Optional[dict]:
    """Probe an IPFS CID via the gateway to get size and content type.

    Uses a HEAD request first. If the CID is a directory, the gateway
    returns text/html. For files, it returns the actual MIME type and
    Content-Length.

    Returns dict with 'file_size', 'file_type', 'is_directory' or None on failure.
    """
    # Normalize CID — CIDv1 may be capitalized by MediaWiki
    if cid.startswith('Bafy'):
        cid = cid[0].lower() + cid[1:]

    url = f"{gateway_url}/ipfs/{cid}"

    try:
        req = urllib.request.Request(url, method='HEAD')
        req.add_header('User-Agent', 'blue-railroad-import/1.0')

        with urllib.request.urlopen(req, timeout=30) as response:
            content_type = response.headers.get('Content-Type', '')
            content_length = response.headers.get('Content-Length')
            # X-Ipfs-Roots header presence indicates a UnixFS directory listing
            etag = response.headers.get('Etag', '')

            is_directory = (
                'text/html' in content_type
                or '"DirIndex' in etag
            )

            result = {
                'is_directory': is_directory,
            }

            if is_directory:
                result['file_type'] = 'directory'
            else:
                # Strip charset and parameters from content type
                mime = content_type.split(';')[0].strip()
                if mime and mime != 'application/octet-stream':
                    result['file_type'] = mime

            if content_length:
                try:
                    result['file_size'] = int(content_length)
                except ValueError:
                    pass

            return result

    except urllib.error.HTTPError as e:
        logger.warning("  HTTP %d for %s", e.code, cid[:16])
        return None
    except Exception as e:
        logger.warning("  Failed to probe %s: %s", cid[:16], e)
        return None


def enrich_release_metadata(
    wiki: WikiClientProtocol,
    gateway_url: str = "https://ipfs.delivery-kid.cryptograss.live",
) -> list[SaveResult]:
    """Enrich Release pages with file_size and file_type from IPFS.

    For each release missing file_size or file_type, probes the IPFS
    gateway and updates the wiki page YAML.

    Args:
        wiki: Wiki client instance
        gateway_url: IPFS gateway URL to probe

    Returns:
        List of SaveResult for each page processed.
    """
    results = []

    releases = get_all_releases(wiki.api_url)
    logger.info("Found %d releases", len(releases))

    needs_enrichment = [
        r for r in releases
        if not r.get('file_size') or not r.get('file_type')
    ]
    logger.info("%d releases need file_size or file_type enrichment", len(needs_enrichment))

    for release in needs_enrichment:
        cid = release.get('ipfs_cid') or release.get('page_title', '')
        title = release.get('title', cid[:16])
        page_title = f"Release:{cid}"

        logger.info("  Probing %s (%s)...", cid[:16], title)

        probe = probe_ipfs_cid(cid, gateway_url)
        if probe is None:
            results.append(SaveResult(page_title, 'error', 'Failed to probe IPFS'))
            continue

        # Read current page content
        content = wiki.get_page_content(page_title)
        if not content:
            results.append(SaveResult(page_title, 'error', 'Could not read page'))
            continue

        try:
            data = yaml.safe_load(content)
            if not isinstance(data, dict):
                results.append(SaveResult(page_title, 'error', 'Page content not YAML dict'))
                continue
        except yaml.YAMLError:
            results.append(SaveResult(page_title, 'error', 'Invalid YAML'))
            continue

        # Check what needs updating
        needs_update = False

        if not data.get('file_size') and probe.get('file_size'):
            data['file_size'] = probe['file_size']
            needs_update = True
            logger.info("    file_size: %d", probe['file_size'])

        if not data.get('file_type') and probe.get('file_type'):
            data['file_type'] = probe['file_type']
            needs_update = True
            logger.info("    file_type: %s", probe['file_type'])

        if not needs_update:
            logger.info("    No new data from probe")
            results.append(SaveResult(page_title, 'unchanged', 'No new metadata from IPFS'))
            continue

        new_content = yaml.dump(data, default_flow_style=False, allow_unicode=True)
        import blue_railroad_import
        v = blue_railroad_import.BOT_VERSION
        summary = f'Enrich IPFS metadata (bot: {v})' if v != 'unknown' else 'Enrich IPFS metadata'
        result = wiki.save_page(page_title, new_content, summary)
        results.append(result)

        if result.action in ('updated', 'created'):
            logger.info("    Updated %s", page_title)
        elif result.action == 'error':
            logger.error("    ERROR: %s", result.message)

    return results


def enrich_thumbnails(
    wiki: WikiClientProtocol,
) -> list[SaveResult]:
    """Generate and upload thumbnails for video releases missing them.

    For each release with a video file_type or release_type=video that
    doesn't have a thumbnail, downloads the video, extracts a frame,
    uploads to the wiki, and writes the thumbnail filename to the YAML.

    Args:
        wiki: Wiki client instance

    Returns:
        List of SaveResult for pages that were updated.
    """
    results = []

    releases = get_all_releases(wiki.api_url)

    # Find video releases missing thumbnails
    needs_thumb = []
    for r in releases:
        # Skip deleted/unpinned releases
        pinned_on = r.get('pinned_on')
        if pinned_on is not None and pinned_on == []:
            continue

        file_type = r.get('file_type', '')
        release_type = r.get('release_type', '')

        has_video = (
            (file_type and file_type.startswith('video/'))
            or release_type == 'video'
            or release_type == 'blue-railroad'
        )
        if not has_video:
            continue

        cid = r.get('ipfs_cid') or r.get('page_title', '')
        if not cid:
            continue

        # Check if thumbnail already exists on wiki
        thumb_filename = get_thumbnail_filename(cid)
        if wiki.file_exists(thumb_filename):
            continue

        needs_thumb.append(r)

    logger.info("  %d video releases need thumbnails", len(needs_thumb))

    generated = 0
    failed = 0
    for r in needs_thumb:
        cid = r.get('ipfs_cid') or r.get('page_title', '')
        title = r.get('title', cid[:16])
        page_title = f"Release:{cid}"

        logger.info("  Generating thumbnail for %s (%s)...", cid[:16], title)

        thumb_path = generate_thumbnail(cid)
        if not thumb_path:
            logger.info("    Failed to generate thumbnail")
            failed += 1
            continue

        # Upload to wiki
        thumb_filename = get_thumbnail_filename(cid)
        description = f"Thumbnail for release video (IPFS: {cid})"
        comment = f"Upload thumbnail for {title}"
        success = wiki.upload_file(thumb_path, thumb_filename, description, comment)

        # Clean up
        try:
            thumb_path.unlink()
        except Exception:
            pass

        if not success:
            logger.info("    Failed to upload thumbnail")
            failed += 1
            continue

        generated += 1
        logger.info("    Uploaded: %s", thumb_filename)

        # Update Release YAML with thumbnail filename
        content = wiki.get_page_content(page_title)
        if content:
            try:
                data = yaml.safe_load(content)
                if isinstance(data, dict) and not data.get('thumbnail'):
                    data['thumbnail'] = thumb_filename
                    new_content = yaml.dump(data, default_flow_style=False, allow_unicode=True)

                    import blue_railroad_import
                    v = blue_railroad_import.BOT_VERSION
                    summary = f'Add thumbnail (bot: {v})' if v != 'unknown' else 'Add thumbnail'
                    result = wiki.save_page(page_title, new_content, summary)
                    results.append(result)
            except yaml.YAMLError:
                pass

    logger.info("  Thumbnails: %d generated, %d failed", generated, failed)
    return results
