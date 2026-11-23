"""Reconcile IPFS pin state with Release pages.

Ensures every Release page's CID is pinned on delivery-kid,
and handles unpin/delete directives from the YAML.
"""

import json
import logging
import urllib.request
import urllib.parse
from typing import Optional

import yaml

from .wiki_client import WikiClientProtocol, SaveResult
from .ipfs_enrichment import get_all_releases

logger = logging.getLogger(__name__)


def _summary(msg: str) -> str:
    import blue_railroad_import
    v = blue_railroad_import.BOT_VERSION
    return f'{msg} (bot: {v})' if v != 'unknown' else msg


def get_pinned_cids(delivery_kid_url: str) -> set[str]:
    """Fetch the set of currently pinned CIDs from delivery-kid."""
    url = f"{delivery_kid_url}/local-pins"
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        return {p['cid'].lower() for p in data.get('pins', [])}
    except Exception as e:
        logger.error("Failed to fetch pins: %s", e)
        return set()


def pin_cid(cid: str, delivery_kid_url: str, api_key: str) -> bool:
    """Pin a CID via delivery-kid API."""
    url = f"{delivery_kid_url}/pin/{cid}"
    req = urllib.request.Request(url, method='POST', data=b'')
    req.add_header('X-API-Key', api_key)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status == 200
    except Exception as e:
        logger.error("  Failed to pin %s: %s", cid[:16], e)
        return False


def unpin_cid(cid: str, delivery_kid_url: str, api_key: str) -> bool:
    """Unpin a CID via delivery-kid API."""
    url = f"{delivery_kid_url}/unpin/{cid}"
    req = urllib.request.Request(url, method='DELETE')
    req.add_header('X-API-Key', api_key)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as e:
        if e.code == 500 and 'not pinned' in e.read().decode().lower():
            return True  # already unpinned
        logger.error("  Failed to unpin %s: %s", cid[:16], e)
        return False
    except Exception as e:
        logger.error("  Failed to unpin %s: %s", cid[:16], e)
        return False


def reconcile_pins(
    wiki: WikiClientProtocol,
    delivery_kid_url: str,
    delivery_kid_api_key: str,
) -> list[SaveResult]:
    """Reconcile IPFS pins with Release page state.

    For each Release page:
    1. If `delete: true` → unpin, update pinned_on, mark as deleted
    2. If `unpin: true` → unpin, update pinned_on, remove flag
    3. Otherwise → ensure pinned, update pinned_on if needed

    Returns list of SaveResult for pages that were modified.
    """
    results = []

    releases = get_all_releases(wiki.api_url)
    pinned_cids = get_pinned_cids(delivery_kid_url)

    logger.info("  %d releases, %d pinned CIDs", len(releases), len(pinned_cids))

    pinned_count = 0
    unpinned_count = 0
    already_ok = 0

    for release in releases:
        cid = release.get('ipfs_cid') or release.get('page_title', '')
        page_title = f"Release:{cid}"
        cid_lower = cid.lower()

        # Normalize CID for IPFS — CIDv1 may be capitalized by MediaWiki
        normalized_cid = cid
        if cid.startswith('Bafy'):
            normalized_cid = cid[0].lower() + cid[1:]

        # Read current page content
        content = wiki.get_page_content(page_title)
        if not content:
            continue

        try:
            data = yaml.safe_load(content)
            if not isinstance(data, dict):
                continue
        except yaml.YAMLError:
            continue

        # Handle delete directive
        if data.get('delete'):
            if cid_lower in pinned_cids:
                logger.info("  Unpinning deleted release: %s", cid[:16])
                unpin_cid(normalized_cid, delivery_kid_url, delivery_kid_api_key)
                unpinned_count += 1

            if data.get('pinned_on'):
                data['pinned_on'] = []
                new_content = yaml.dump(data, default_flow_style=False, allow_unicode=True)
                result = wiki.save_page(page_title, new_content, _summary('Release deleted: unpinned'))
                results.append(result)
            continue

        # Handle unpin directive
        if data.get('unpin'):
            if cid_lower in pinned_cids:
                logger.info("  Unpinning: %s", cid[:16])
                unpin_cid(normalized_cid, delivery_kid_url, delivery_kid_api_key)
                unpinned_count += 1

            del data['unpin']
            data['pinned_on'] = []
            new_content = yaml.dump(data, default_flow_style=False, allow_unicode=True)
            result = wiki.save_page(page_title, new_content, _summary('Unpinned by directive'))
            results.append(result)
            continue

        # Normal state: ensure pinned
        is_pinned = cid_lower in pinned_cids
        pinned_on = data.get('pinned_on') or []

        if is_pinned:
            # Pinned — make sure YAML reflects it
            if 'delivery-kid' not in pinned_on:
                data['pinned_on'] = ['delivery-kid']
                new_content = yaml.dump(data, default_flow_style=False, allow_unicode=True)
                result = wiki.save_page(page_title, new_content, _summary('Update pinned_on'))
                results.append(result)
            else:
                already_ok += 1
        else:
            # Not pinned — pin it
            logger.info("  Pinning: %s", cid[:16])
            if pin_cid(normalized_cid, delivery_kid_url, delivery_kid_api_key):
                pinned_count += 1
                if 'delivery-kid' not in pinned_on:
                    data['pinned_on'] = ['delivery-kid']
                    new_content = yaml.dump(data, default_flow_style=False, allow_unicode=True)
                    result = wiki.save_page(page_title, new_content, _summary('Pinned to delivery-kid'))
                    results.append(result)
            else:
                results.append(SaveResult(page_title, 'error', 'Failed to pin'))

    logger.info("  Pins: %d ok, %d pinned, %d unpinned", already_ok, pinned_count, unpinned_count)

    return results
