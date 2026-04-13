"""Ensure Release pages exist for known IPFS CIDs.

When the importer processes tokens and submissions that have IPFS CIDs,
this module ensures corresponding Release: namespace pages exist on
PickiPedia with basic metadata, and enriches existing pages that are
missing metadata like file_type.
"""

import json
import logging
import re
import urllib.request
import urllib.parse
from typing import Optional

import yaml

from .wiki_client import WikiClientProtocol, SaveResult
from .models import Token, Submission

# Song ID → (song_name, exercise_name)
SONG_EXERCISES = {
    '5': ('Blue Railroad Train', 'Squats'),
    '6': ('Nine Pound Hammer', 'Pushups'),
    '7': ('Blue Railroad Train', 'Squats'),  # legacy
    '10': ('Ginseng Sullivan', 'Army Crawls'),
}

logger = logging.getLogger(__name__)


def build_release_yaml(
    cid: str,
    title: Optional[str] = None,
    description: Optional[str] = None,
    file_type: Optional[str] = None,
    release_type: Optional[str] = None,
) -> str:
    """Build YAML content for a Release page."""
    data = {}
    if title:
        data['title'] = title
    data['ipfs_cid'] = cid
    if release_type:
        data['release_type'] = release_type
    if description:
        data['description'] = description
    if file_type:
        data['file_type'] = file_type

    return yaml.dump(data, default_flow_style=False, allow_unicode=True)


def _parse_existing_yaml(content: str) -> dict:
    """Try to parse existing page content as YAML.

    Returns parsed dict, or empty dict if parsing fails
    (e.g. page is wikitext, not YAML).
    """
    if not content or not content.strip():
        return {}
    try:
        data = yaml.safe_load(content)
        if isinstance(data, dict):
            return data
        return {}
    except yaml.YAMLError:
        return {}


def _enrich_existing(
    wiki: WikiClientProtocol,
    page_title: str,
    cid: str,
    title: Optional[str] = None,
    description: Optional[str] = None,
    file_type: Optional[str] = None,
    release_type: Optional[str] = None,
) -> SaveResult:
    """Check if an existing Release page needs enrichment.

    Updates the page if it's missing file_type or other metadata
    that we can provide.
    """
    existing_content = wiki.get_page_content(page_title)
    existing_data = _parse_existing_yaml(existing_content)

    # If we can't parse the existing content, don't overwrite it
    if not existing_data and existing_content and existing_content.strip():
        return SaveResult(page_title, 'unchanged', 'Existing content not YAML, skipping')

    # Check what's missing
    needs_update = False
    if file_type and not existing_data.get('file_type'):
        existing_data['file_type'] = file_type
        needs_update = True
    if release_type and not existing_data.get('release_type'):
        existing_data['release_type'] = release_type
        needs_update = True
    if title and '#' in title:
        existing_title = existing_data.get('title', '')
        # Update title if it doesn't already have the "#<id>" format
        if existing_title != title and '#' not in existing_title:
            existing_data['title'] = title
            needs_update = True
    elif title and not existing_data.get('title'):
        existing_data['title'] = title
        needs_update = True
    if description and not existing_data.get('description'):
        existing_data['description'] = description
        needs_update = True
    if not existing_data.get('ipfs_cid'):
        existing_data['ipfs_cid'] = cid
        needs_update = True

    if not needs_update:
        return SaveResult(page_title, 'unchanged', 'Already has metadata')

    yaml_content = yaml.dump(existing_data, default_flow_style=False, allow_unicode=True)

    logger.info("  Enriching release page: %s", page_title)

    summary = 'Enrich release metadata (via Blue Railroad import)'
    return wiki.save_page(page_title, yaml_content, summary)


def ensure_release_for_token(
    wiki: WikiClientProtocol,
    token: Token,
    submission_id: Optional[int] = None,
    all_token_ids: Optional[list[int]] = None,
) -> Optional[SaveResult]:
    """Ensure a Release page exists for a token's video CID.

    If the page exists but is missing metadata (like file_type),
    enriches it with what we know.

    Args:
        all_token_ids: All token IDs that share this CID (for multi-token titles).

    Returns None if token has no CID, or SaveResult with the action taken.
    """
    cid = token.ipfs_cid
    if not cid:
        return None

    page_title = f'Release:{cid}'

    # Build title: "<song name> (<exercise>) #<id>, #<id>"
    song_exercise = SONG_EXERCISES.get(token.song_id) if token.song_id else None
    token_ids = all_token_ids or [int(token.token_id)]

    if song_exercise:
        song_name, exercise = song_exercise
        id_str = ', '.join(f'#{tid}' for tid in token_ids)
        title = f'{song_name} ({exercise}) {id_str}'
        description = f'Blue Railroad {id_str} — {exercise} to {song_name}'
    elif submission_id is not None:
        title = f'Blue Railroad Submission {submission_id}'
        description = f'Video from Blue Railroad Submission #{submission_id}'
    else:
        title = f'Blue Railroad Token {token.token_id}'
        description = f'Video from Blue Railroad Token #{token.token_id}'

    if wiki.page_exists(page_title):
        return _enrich_existing(
            wiki, page_title, cid,
            title=title, description=description,
            file_type='video/webm',
            release_type='blue-railroad',
        )

    yaml_content = build_release_yaml(
        cid=cid,
        title=title,
        description=description,
        file_type='video/webm',
        release_type='blue-railroad',
    )

    logger.info("  Creating release page: %s", page_title)

    summary = f'Create release for {title} (via Blue Railroad import)'
    return wiki.save_page(page_title, yaml_content, summary)


def convert_releases_to_yaml(wiki) -> list[SaveResult]:
    """Convert Release pages from wikitext to release-yaml content model.

    Queries all pages in the Release namespace (3004), checks their content
    model, and re-saves any wikitext pages as release-yaml. Preserves
    existing content where possible, parsing it as YAML metadata.

    Args:
        wiki: MWClientWrapper instance (needs .site access)

    Returns:
        List of SaveResult for each page processed.
    """
    results = []

    # Query all Release pages with their content model
    api_url = f"{wiki.api_url}?action=query&list=allpages&apnamespace=3004&aplimit=500&format=json"
    with urllib.request.urlopen(api_url, timeout=30) as response:
        data = json.loads(response.read().decode('utf-8'))

    all_pages = data.get('query', {}).get('allpages', [])

    logger.info("Found %d Release pages", len(all_pages))

    for page_info in all_pages:
        title = page_info['title']

        # Check content model via page info query
        info_url = (
            f"{wiki.api_url}?action=query&titles={urllib.parse.quote(title)}"
            f"&prop=info&format=json"
        )
        with urllib.request.urlopen(info_url, timeout=30) as response:
            info_data = json.loads(response.read().decode('utf-8'))

        page_data = next(iter(info_data['query']['pages'].values()))
        content_model = page_data.get('contentmodel', 'unknown')

        if content_model == 'release-yaml':
            logger.info("  Already release-yaml: %s", title)
            results.append(SaveResult(title, 'unchanged', 'Already release-yaml'))
            continue

        logger.info("  Converting: %s (was %s)", title, content_model)

        # Read existing content
        existing_content = wiki.get_page_content(title)

        # Extract CID from title (after "Release:" prefix)
        cid = title.split(':', 1)[1] if ':' in title else title

        # Try to parse existing content as YAML to preserve metadata
        existing_data = _parse_existing_yaml(existing_content) if existing_content else {}

        # If YAML parsing got nothing, check for Bot_proposes template
        # Format: # {{Bot_proposes|Title Here|by=Magent}}
        if not existing_data and existing_content:
            match = re.search(
                r'\{\{Bot_proposes\|([^|]+)\|',
                existing_content,
            )
            if match:
                extracted_title = match.group(1).strip()
                existing_data['title'] = extracted_title
                logger.info("    Extracted title from Bot_proposes: %s", extracted_title)

        # Ensure ipfs_cid is set
        if not existing_data.get('ipfs_cid'):
            existing_data['ipfs_cid'] = cid

        yaml_content = yaml.dump(existing_data, default_flow_style=False, allow_unicode=True)

        try:
            page = wiki.site.pages[title]
            page.save(
                yaml_content,
                summary='Convert to release-yaml content model',
                contentmodel='release-yaml',
            )
            results.append(SaveResult(title, 'updated', f'Converted from {content_model}'))
        except Exception as e:
            results.append(SaveResult(title, 'error', str(e)))

    return results


def fix_bot_proposes_pages(
    wiki: WikiClientProtocol,
) -> list[SaveResult]:
    """Replace Bot_proposes wikitext with proper YAML on Release pages.

    Some Release pages already have content model 'release-yaml' but their
    content is still wikitext like '# {{Bot_proposes|Title|by=Magent}}'.
    The YAML parser sees this as a comment (returns None), so enrichment
    skips them. This function replaces that content with proper YAML.

    Args:
        wiki: Wiki client instance

    Returns:
        List of SaveResult for each page processed.
    """
    results = []

    # Query all Release pages
    url = f"{wiki.api_url}?action=query&list=allpages&apnamespace=3004&aplimit=500&format=json"
    with urllib.request.urlopen(url, timeout=30) as response:
        data = json.loads(response.read().decode('utf-8'))

    all_pages = data.get('query', {}).get('allpages', [])

    logger.info("Found %d Release pages, checking for Bot_proposes content...", len(all_pages))

    for page_info in all_pages:
        title = page_info['title']
        content = wiki.get_page_content(title)

        if not content:
            continue

        # Check if this page has Bot_proposes template content
        match = re.search(r'\{\{Bot_proposes\|([^|]+)\|', content)
        if not match:
            continue

        extracted_title = match.group(1).strip()
        cid = title.split(':', 1)[1] if ':' in title else title

        # Skip titles that are just template defaults
        if extracted_title == 'Optional metadata':
            logger.info("  Skipping %s (template default, no real title)", title)
            # Still replace with minimal YAML so enrichment can process it
            new_content = yaml.dump(
                {'ipfs_cid': cid},
                default_flow_style=False, allow_unicode=True,
            )
        else:
            new_content = yaml.dump(
                {'title': extracted_title, 'ipfs_cid': cid},
                default_flow_style=False, allow_unicode=True,
            )

        logger.info("  %s", title)
        logger.info("    Was: %s", content.strip()[:80])
        logger.info("    Now: %s", new_content.strip()[:80])

        summary = f'Replace Bot_proposes wikitext with YAML (title: {extracted_title[:40]})'
        result = wiki.save_page(title, new_content, summary)
        results.append(result)

    logger.info("Processed %d Bot_proposes pages", len(results))

    return results


def clear_torrent_fields(
    wiki: WikiClientProtocol,
) -> list[SaveResult]:
    """Remove bittorrent_infohash and bittorrent_trackers from all Release pages.

    Used when the torrent format changes (e.g. switching to single-file format)
    so that the enrichment job regenerates all infohashes.

    Preserves all other YAML fields by string manipulation — removes lines
    starting with 'bittorrent_infohash:' and 'bittorrent_trackers:' plus
    any continuation lines (list items starting with '  - ').
    """
    results = []

    url = f"{wiki.api_url}?action=query&list=allpages&apnamespace=3004&aplimit=500&format=json"
    with urllib.request.urlopen(url, timeout=30) as response:
        data = json.loads(response.read().decode('utf-8'))

    all_pages = data.get('query', {}).get('allpages', [])

    logger.info("Found %d Release pages, checking for torrent fields...", len(all_pages))

    for page_info in all_pages:
        title = page_info['title']
        content = wiki.get_page_content(title)

        if not content or 'bittorrent_infohash' not in content:
            continue

        # Remove torrent fields by filtering lines
        new_lines = []
        skip_list_items = False
        for line in content.split('\n'):
            if line.startswith(('bittorrent_infohash:', 'bittorrent_trackers:', 'bittorrent_webseeds:')):
                skip_list_items = line.startswith(('bittorrent_trackers:', 'bittorrent_webseeds:'))
                continue
            if skip_list_items and (line.startswith('- ') or line.startswith('  - ')):
                continue
            skip_list_items = False
            new_lines.append(line)

        new_content = '\n'.join(new_lines)
        # Clean up trailing whitespace
        new_content = new_content.rstrip('\n') + '\n'

        if new_content == content:
            continue

        logger.info("  Clearing torrent fields from %s", title)

        summary = 'Clear BitTorrent metadata for regeneration'
        result = wiki.save_page(title, new_content, summary)
        results.append(result)

    logger.info("Cleared torrent fields from %d pages", len(results))

    return results


def ensure_release_for_submission(
    wiki: WikiClientProtocol,
    submission: Submission,
) -> Optional[SaveResult]:
    """Ensure a Release page exists for a submission's CID.

    If the page exists but is missing metadata, enriches it.

    Returns None if submission has no CID, or SaveResult with the action taken.
    """
    if not submission.has_cid:
        return None

    cid = submission.ipfs_cid
    page_title = f'Release:{cid}'

    title = f'Blue Railroad Submission {submission.id}'
    description = f'Video from Blue Railroad Submission #{submission.id}'

    if wiki.page_exists(page_title):
        return _enrich_existing(
            wiki, page_title, cid,
            title=title, description=description,
            file_type='video/webm',
            release_type='blue-railroad',
        )

    yaml_content = build_release_yaml(
        cid=cid,
        title=title,
        description=description,
        file_type='video/webm',
        release_type='blue-railroad',
    )

    logger.info("  Creating release page: %s", page_title)

    summary = f'Create release for {title} (via Blue Railroad import)'
    return wiki.save_page(page_title, yaml_content, summary)
