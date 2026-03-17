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
import urllib.request
import urllib.parse
import re
from typing import Optional

import yaml

from .wiki_client import WikiClientProtocol, SaveResult


NS_RELEASEDRAFT = 3006


def fetch_release_drafts(wiki, verbose: bool = False) -> list[dict]:
    """Fetch all ReleaseDraft pages and their content.

    Returns list of dicts with 'title' and 'data' (parsed YAML).
    """
    api_url = f"{wiki._api_url}?action=query&list=allpages&apnamespace={NS_RELEASEDRAFT}&aplimit=500&format=json"

    try:
        with urllib.request.urlopen(api_url, timeout=30) as response:
            result = json.loads(response.read().decode('utf-8'))
    except Exception as e:
        if verbose:
            print(f"  Failed to query ReleaseDraft pages: {e}")
        return []

    all_pages = result.get('query', {}).get('allpages', [])

    if verbose:
        print(f"  Found {len(all_pages)} ReleaseDraft page(s)")

    drafts = []
    for page_info in all_pages:
        title = page_info['title']
        content = wiki.get_page_content(title)
        if not content:
            continue

        try:
            data = yaml.safe_load(content)
            if not isinstance(data, dict):
                continue
        except yaml.YAMLError:
            continue

        drafts.append({
            'title': title,
            'data': data,
        })

    return drafts


def find_cid_from_history(wiki, page_title: str, verbose: bool = False) -> Optional[str]:
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
    release = {}
    draft_type = draft_data.get('type', 'content')

    if draft_type == 'album':
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

    elif draft_type == 'content':
        content = draft_data.get('content', {})
        if content.get('title'):
            release['title'] = content['title']
        if content.get('description'):
            release['description'] = content['description']
        if content.get('file_type'):
            release['file_type'] = content['file_type']
        if content.get('subsequent_to'):
            release['subsequent_to'] = content['subsequent_to']

    elif draft_type == 'blue-railroad':
        if draft_data.get('submission_id'):
            release['title'] = f"Blue Railroad Submission {draft_data['submission_id']}"
            release['description'] = f"Video from Blue Railroad Submission #{draft_data['submission_id']}"
        release['file_type'] = 'video/webm'

    if draft_data.get('blockheight'):
        release['blockheight'] = draft_data['blockheight']

    release['pinned_on'] = ['delivery-kid']

    return yaml.dump(release, default_flow_style=False, allow_unicode=True)


def process_release_drafts(
    wiki: WikiClientProtocol,
    verbose: bool = False,
) -> list[SaveResult]:
    """Process completed ReleaseDraft pages into Release pages.

    For each ReleaseDraft:
    1. Check edit history for a finalization CID
    2. If CID found and no Release:{CID} page exists, create it
    3. Skip drafts that haven't been finalized yet

    Returns list of SaveResult for Release pages created/enriched.
    """
    results = []

    if verbose:
        print("Processing ReleaseDraft pages...")

    drafts = fetch_release_drafts(wiki, verbose=verbose)

    for draft in drafts:
        title = draft['title']
        data = draft['data']

        # Try to find the CID from edit history
        cid = find_cid_from_history(wiki, title, verbose=verbose)

        if not cid:
            if verbose:
                print(f"  {title}: no CID found in history, skipping")
            continue

        release_title = f"Release:{cid}"

        if wiki.page_exists(release_title):
            if verbose:
                print(f"  {title}: Release page already exists ({release_title})")
            results.append(SaveResult(release_title, 'unchanged', 'Already exists'))
            continue

        # Build Release page from draft data
        release_yaml = build_release_from_draft(data)

        if verbose:
            print(f"  {title}: creating {release_title}")

        draft_type = data.get('type', 'content')
        summary = f"Release created from {draft_type} draft (via bot)"
        result = wiki.save_page(release_title, release_yaml, summary)
        results.append(result)

        if result.action == 'created':
            if verbose:
                print(f"    Created: {release_title}")
        elif result.action == 'error':
            if verbose:
                print(f"    ERROR: {result.message}")

    return results
