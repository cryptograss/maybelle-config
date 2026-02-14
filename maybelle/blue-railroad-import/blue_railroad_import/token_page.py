"""Token page content generation."""

import re
from typing import Optional

from .models import Token
from .thumbnail import get_thumbnail_filename, check_maybelle_pinned


def generate_template_call(
    token: Token,
    maybelle_pinned: bool = False,
    submission_id: Optional[int] = None,
) -> str:
    """Generate just the template call for a token."""
    thumbnail = get_thumbnail_filename(token.ipfs_cid) if token.ipfs_cid else ''

    lines = [
        "{{Blue Railroad Token",
        f"|token_id={token.token_id}",
        f"|song_id={token.song_id or ''}",
        f"|contract_version={'V2' if token.is_v2 else 'V1'}",
        f"|thumbnail={thumbnail}",
        f"|maybelle_pinned={'yes' if maybelle_pinned else 'no'}",
    ]

    # Version-specific fields
    if token.is_v2:
        lines.append(f"|blockheight={token.blockheight or ''}")
        lines.append(f"|video_hash={token.video_hash or ''}")
    else:
        lines.append(f"|date={token.formatted_date or ''}")
        lines.append(f"|date_raw={token.date or ''}")

    lines.extend([
        f"|owner={token.owner}",
        f"|owner_display={token.owner_display}",
        f"|uri={token.uri or ''}",
        f"|uri_type={'ipfs' if token.ipfs_cid else 'unknown'}",
        f"|ipfs_cid={token.ipfs_cid or ''}",
        f"|submission_id={submission_id or ''}",
        "}}",
    ])

    return "\n".join(lines)


def generate_token_page_content(
    token: Token,
    submission_id: Optional[int] = None,
) -> str:
    """Generate wikitext content for a new token page."""
    maybelle_pinned = check_maybelle_pinned(token.ipfs_cid)
    lines = [generate_template_call(token, maybelle_pinned, submission_id), ""]

    if token.is_v2:
        lines.append("[[Category:Blue Railroad V2 Tokens]]")

    return "\n".join(lines)


def update_existing_page(
    existing_content: str,
    token: Token,
    submission_id: Optional[int] = None,
) -> Optional[tuple[str, str]]:
    """Update only the template call in existing page content.

    Preserves all user content outside the template.
    Returns (new_content, reason) if update needed, None if no update needed.
    """
    # Extract the existing template call
    template_pattern = r'\{\{Blue Railroad Token\s*\n(?:\|[^\n]*\n)*\}\}'
    match = re.search(template_pattern, existing_content)

    if not match:
        # No template found - shouldn't happen, but fall back to full replace
        return generate_token_page_content(token, submission_id), "template not found"

    old_template = match.group(0)

    # Parse existing values from the template
    owner_match = re.search(r'\|owner=([^\n|]+)', old_template)
    existing_owner = owner_match.group(1).strip() if owner_match else None

    pinned_match = re.search(r'\|maybelle_pinned=([^\n|]+)', old_template)
    existing_pinned = pinned_match.group(1).strip() if pinned_match else None

    submission_match = re.search(r'\|submission_id=([^\n|]+)', old_template)
    existing_submission = submission_match.group(1).strip() if submission_match else None

    # Check current maybelle status
    maybelle_pinned = check_maybelle_pinned(token.ipfs_cid)
    new_pinned_str = 'yes' if maybelle_pinned else 'no'
    new_submission_str = str(submission_id) if submission_id else ''

    # Determine if update needed and why
    reasons = []
    if existing_owner != token.owner:
        reasons.append("ownership changed")
    if existing_pinned != new_pinned_str:
        reasons.append(f"maybelle pin {'confirmed' if maybelle_pinned else 'lost'}")
    if existing_submission != new_submission_str:
        reasons.append(f"submission link {'added' if submission_id else 'removed'}")

    if not reasons:
        return None  # No update needed

    # Replace just the template, keep everything else
    new_template = generate_template_call(token, maybelle_pinned, submission_id)
    new_content = existing_content[:match.start()] + new_template + existing_content[match.end():]
    return new_content, ", ".join(reasons)
