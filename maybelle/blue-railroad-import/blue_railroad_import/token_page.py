"""Token page content generation."""

import re
from typing import Optional

from .models import Token
from .thumbnail import get_thumbnail_filename


def generate_template_call(token: Token) -> str:
    """Generate just the template call for a token."""
    thumbnail = get_thumbnail_filename(token.ipfs_cid) if token.ipfs_cid else ''

    lines = [
        "{{Blue Railroad Token",
        f"|token_id={token.token_id}",
        f"|song_id={token.song_id or ''}",
        f"|contract_version={'V2' if token.is_v2 else 'V1'}",
        f"|thumbnail={thumbnail}",
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
        "}}",
    ])

    return "\n".join(lines)


def generate_token_page_content(token: Token) -> str:
    """Generate wikitext content for a new token page."""
    lines = [generate_template_call(token), ""]

    if token.is_v2:
        lines.append("[[Category:Blue Railroad V2 Tokens]]")

    return "\n".join(lines)


def update_existing_page(existing_content: str, token: Token) -> Optional[str]:
    """Update only the template call in existing page content.

    Preserves all user content outside the template.
    Returns None if no update is needed (owner unchanged).
    """
    # Extract the existing template call
    template_pattern = r'\{\{Blue Railroad Token\s*\n(?:\|[^\n]*\n)*\}\}'
    match = re.search(template_pattern, existing_content)

    if not match:
        # No template found - shouldn't happen, but fall back to full replace
        return generate_token_page_content(token)

    # Parse existing owner from the template
    owner_match = re.search(r'\|owner=([^\n|]+)', match.group(0))
    existing_owner = owner_match.group(1).strip() if owner_match else None

    # Only update if owner changed
    if existing_owner == token.owner:
        return None  # No update needed

    # Replace just the template, keep everything else
    new_template = generate_template_call(token)
    return existing_content[:match.start()] + new_template + existing_content[match.end():]
