"""Ensure Release pages exist for known IPFS CIDs.

When the importer processes tokens and submissions that have IPFS CIDs,
this module ensures corresponding Release: namespace pages exist on
PickiPedia with basic metadata.
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


def ensure_release_for_token(
    wiki: WikiClientProtocol,
    token: Token,
    submission_id: Optional[int] = None,
    verbose: bool = False,
) -> Optional[SaveResult]:
    """Ensure a Release page exists for a token's video CID.

    Returns None if token has no CID, or SaveResult with the action taken.
    """
    cid = token.ipfs_cid
    if not cid:
        return None

    page_title = f'Release:{cid}'

    if wiki.page_exists(page_title):
        return SaveResult(page_title, 'unchanged', 'Already exists')

    # Build metadata from what we know
    if submission_id is not None:
        title = f'Blue Railroad Submission {submission_id}'
        description = f'Video from Blue Railroad Submission #{submission_id}'
    else:
        title = f'Blue Railroad Token {token.token_id}'
        description = f'Video from Blue Railroad Token #{token.token_id}'

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

    Returns None if submission has no CID, or SaveResult with the action taken.
    """
    if not submission.has_cid:
        return None

    cid = submission.ipfs_cid
    page_title = f'Release:{cid}'

    if wiki.page_exists(page_title):
        return SaveResult(page_title, 'unchanged', 'Already exists')

    title = f'Blue Railroad Submission {submission.id}'
    description = f'Video from Blue Railroad Submission #{submission.id}'

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
