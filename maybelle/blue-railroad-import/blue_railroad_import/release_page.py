"""Ensure Release pages exist for known IPFS CIDs.

When the importer processes tokens and submissions that have IPFS CIDs,
this module ensures corresponding Release: namespace pages exist on
PickiPedia with basic metadata, and enriches existing pages that are
missing metadata like file_type.
"""

import yaml
from typing import Optional

from .wiki_client import WikiClientProtocol, SaveResult
from .models import Token, Submission


def build_release_yaml(
    cid: str,
    title: Optional[str] = None,
    description: Optional[str] = None,
    file_type: Optional[str] = None,
) -> str:
    """Build YAML content for a Release page."""
    data = {}
    if title:
        data['title'] = title
    data['ipfs_cid'] = cid
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
    verbose: bool = False,
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
    if title and not existing_data.get('title'):
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

    if verbose:
        print(f"  Enriching release page: {page_title}")

    summary = 'Enrich release metadata (via Blue Railroad import)'
    return wiki.save_page(page_title, yaml_content, summary)


def ensure_release_for_token(
    wiki: WikiClientProtocol,
    token: Token,
    submission_id: Optional[int] = None,
    verbose: bool = False,
) -> Optional[SaveResult]:
    """Ensure a Release page exists for a token's video CID.

    If the page exists but is missing metadata (like file_type),
    enriches it with what we know.

    Returns None if token has no CID, or SaveResult with the action taken.
    """
    cid = token.ipfs_cid
    if not cid:
        return None

    page_title = f'Release:{cid}'

    # Build metadata from what we know
    if submission_id is not None:
        title = f'Blue Railroad Submission {submission_id}'
        description = f'Video from Blue Railroad Submission #{submission_id}'
    else:
        title = f'Blue Railroad Token {token.token_id}'
        description = f'Video from Blue Railroad Token #{token.token_id}'

    if wiki.page_exists(page_title):
        return _enrich_existing(
            wiki, page_title, cid,
            title=title, description=description,
            file_type='video/webm', verbose=verbose,
        )

    yaml_content = build_release_yaml(
        cid=cid,
        title=title,
        description=description,
        file_type='video/webm',
    )

    if verbose:
        print(f"  Creating release page: {page_title}")

    summary = f'Create release for {title} (via Blue Railroad import)'
    return wiki.save_page(page_title, yaml_content, summary)


def ensure_release_for_submission(
    wiki: WikiClientProtocol,
    submission: Submission,
    verbose: bool = False,
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
            file_type='video/webm', verbose=verbose,
        )

    yaml_content = build_release_yaml(
        cid=cid,
        title=title,
        description=description,
        file_type='video/webm',
    )

    if verbose:
        print(f"  Creating release page: {page_title}")

    summary = f'Create release for {title} (via Blue Railroad import)'
    return wiki.save_page(page_title, yaml_content, summary)
