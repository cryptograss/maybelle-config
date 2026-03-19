"""Process completed ReleaseDraft pages into Release pages.

Queries the ReleaseDraft namespace (3006) for drafts that have been
finalized (pinned to IPFS). For each one that doesn't already have a
corresponding Release page, creates it.

The edit summary of the finalization edit contains the CID
(e.g. "Finalized: pinned to IPFS as bafybeif..."), which is the
primary way we discover the CID for a completed draft.

This module is the sole creator of Release pages — browser JS and
Special pages only create ReleaseDraft pages.
"""

import json
import logging
import urllib.request
import urllib.parse
import re
from typing import Optional

import yaml

from .wiki_client import WikiClientProtocol, SaveResult

logger = logging.getLogger(__name__)


NS_RELEASEDRAFT = 3006


# -- Draft type classes --
# Each draft type knows how to build its own Release page YAML.
# The type field is set by whichever Special page or bot created the draft:
#   Special:DeliverRecord       → type: record
#   Special:DeliverOtherContent → type: other
#   Special:DeliverVideo        → type: video
#   Blue Railroad bot           → type: blue-railroad


class DraftType:
    """Base class for draft type handlers."""

    name: str = 'unknown'

    def build_release(self, draft_data: dict) -> dict:
        """Build Release page fields from draft data. Override in subclasses."""
        return {}


class RecordDraft(DraftType):
    """Album, EP, single — any collection of tracks."""

    name = 'record'

    def build_release(self, draft_data: dict) -> dict:
        release = {}
        album = draft_data.get('album', {})
        artist = album.get('artist', '')
        title = album.get('title', '')
        version = album.get('version', '')

        full_title = f"{artist} - {title}" if artist and title else title or ''
        if version:
            full_title += f" ({version})"

        if full_title:
            release['title'] = full_title
        if album.get('description'):
            release['description'] = album['description']

        return release


class BlueRailroadDraft(DraftType):
    """Video from a Blue Railroad exercise submission.

    Created by Special:DeliverBlueRailroad or the on-chain submission bot.
    Content block contains exercise, venue, recorder, notes, participants.
    """

    name = 'blue-railroad'

    def build_release(self, draft_data: dict) -> dict:
        release = {}
        content = draft_data.get('content', {})

        exercise = content.get('exercise', '')
        if exercise:
            release['title'] = exercise
        elif draft_data.get('submission_id'):
            release['title'] = f"Blue Railroad Submission {draft_data['submission_id']}"

        release['file_type'] = content.get('file_type', 'video')

        if content.get('venue'):
            release['venue'] = content['venue']
        if content.get('recorder'):
            release['recorder'] = content['recorder']
        if content.get('notes'):
            release['description'] = content['notes']
        if content.get('participants'):
            release['participants'] = content['participants']
        if draft_data.get('submission_id'):
            release['submission_id'] = draft_data['submission_id']

        return release


class OtherDraft(DraftType):
    """Catch-all for uploads that aren't records or Blue Railroad submissions."""

    name = 'other'

    def build_release(self, draft_data: dict) -> dict:
        release = {}
        content = draft_data.get('content', {})
        if content.get('title'):
            release['title'] = content['title']
        if content.get('description'):
            release['description'] = content['description']
        if content.get('file_type'):
            release['file_type'] = content['file_type']
        if content.get('subsequent_to'):
            release['subsequent_to'] = content['subsequent_to']
        return release


class VideoDraft(OtherDraft):
    """Video upload with venue and performer metadata."""

    name = 'video'

    def build_release(self, draft_data: dict) -> dict:
        release = super().build_release(draft_data)
        content = draft_data.get('content', {})
        if content.get('venue'):
            release['venue'] = content['venue']
        if content.get('performers'):
            release['performers'] = content['performers']
        return release


DRAFT_TYPES: dict[str, DraftType] = {
    'record': RecordDraft(),
    'album': RecordDraft(),  # legacy alias
    'blue-railroad': BlueRailroadDraft(),
    'video': VideoDraft(),
    'other': OtherDraft(),
    'content': OtherDraft(),  # legacy alias
}


def get_draft_handler(draft_data: dict) -> DraftType:
    """Get the appropriate handler for a draft's type."""
    type_name = draft_data.get('type', 'other')
    return DRAFT_TYPES.get(type_name, OtherDraft())


def fetch_release_drafts(wiki) -> list[dict]:
    """Fetch all ReleaseDraft pages and their content.

    Returns list of dicts with 'title' and 'data' (parsed YAML).
    """
    api_url = f"{wiki._api_url}?action=query&list=allpages&apnamespace={NS_RELEASEDRAFT}&aplimit=500&format=json"

    try:
        with urllib.request.urlopen(api_url, timeout=30) as response:
            result = json.loads(response.read().decode('utf-8'))
    except Exception as e:
        logger.warning("Failed to query ReleaseDraft pages: %s", e)
        return []

    all_pages = result.get('query', {}).get('allpages', [])
    logger.info("  Found %d ReleaseDraft page(s)", len(all_pages))

    drafts = []
    content_calls = 0
    for page_info in all_pages:
        title = page_info['title']
        content = wiki.get_page_content(title)
        content_calls += 1
        if not content:
            continue

        try:
            data = yaml.safe_load(content)
            if not isinstance(data, dict):
                logger.warning("  %s: YAML parsed but not a dict, skipping", title)
                continue
        except yaml.YAMLError as e:
            logger.warning("  %s: invalid YAML, skipping: %s", title, e)
            continue

        drafts.append({
            'title': title,
            'data': data,
        })

    logger.info("  ReleaseDraft API calls: 1 allpages + %d content = %d total", content_calls, 1 + content_calls)

    return drafts


def find_cid_from_history(wiki, page_title: str) -> Optional[str]:
    """Try to find a CID from the page's edit history.

    Looks for edit summaries like "Finalized: pinned to IPFS as bafybeif..."
    or "Transcoding submitted: job coconut-..."
    """
    # Use the revisions API to get recent edit summaries
    encoded_title = urllib.parse.quote(page_title)
    url = (
        f"{wiki._api_url}?action=query&titles={encoded_title}"
        f"&prop=revisions&rvprop=comment&rvlimit=10&format=json"
    )

    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            data = json.loads(response.read().decode('utf-8'))
    except Exception:
        return None

    pages = data.get('query', {}).get('pages', {})
    for page_data in pages.values():
        revisions = page_data.get('revisions', [])
        for rev in revisions:
            comment = rev.get('comment', '')
            # Match "Finalized: pinned to IPFS as {cid}"
            match = re.search(r'pinned to IPFS as (\S+)', comment)
            if match:
                return match.group(1)

    return None


def build_release_from_draft(draft_data: dict) -> str:
    """Build Release page YAML from a ReleaseDraft's data."""
    handler = get_draft_handler(draft_data)
    release = handler.build_release(draft_data)

    if draft_data.get('blockheight'):
        release['blockheight'] = draft_data['blockheight']
    if draft_data.get('upload_blockheight'):
        release['upload_blockheight'] = draft_data['upload_blockheight']

    release['pinned_on'] = ['delivery-kid']

    return yaml.dump(release, default_flow_style=False, allow_unicode=True)


def process_release_drafts(
    wiki: WikiClientProtocol,
) -> list[SaveResult]:
    """Process completed ReleaseDraft pages into Release pages.

    For each ReleaseDraft:
    1. Check edit history for a finalization CID
    2. If CID found and no Release:{CID} page exists, create it
    3. Skip drafts that haven't been finalized yet

    Returns list of SaveResult for Release pages created/enriched.
    """
    results = []

    logger.info("Processing ReleaseDraft pages...")

    drafts = fetch_release_drafts(wiki)

    history_calls = 0
    for draft in drafts:
        title = draft['title']
        data = draft['data']

        # Try to find the CID from edit history
        cid = find_cid_from_history(wiki, title)
        history_calls += 1

        if not cid:
            logger.info("  %s: no CID found in history, skipping", title)
            continue

        release_title = f"Release:{cid}"

        if wiki.page_exists(release_title):
            logger.info("  %s: Release page already exists (%s)", title, release_title)
            results.append(SaveResult(release_title, 'unchanged', 'Already exists'))
            continue

        # Build Release page from draft data
        release_yaml = build_release_from_draft(data)

        logger.info("  %s: creating %s", title, release_title)

        handler = get_draft_handler(data)
        summary = f"Release created from {handler.name} draft (via bot)"
        result = wiki.save_page(release_title, release_yaml, summary)
        results.append(result)

        if result.action == 'created':
            logger.info("    Created: %s", release_title)
        elif result.action == 'error':
            logger.error("    ERROR: %s", result.message)

    n_drafts = len(drafts)
    total = 1 + n_drafts + history_calls
    logger.info("  ReleaseDraft API calls: 1 allpages + %d content + %d history = %d total",
                n_drafts, history_calls, total)

    return results
