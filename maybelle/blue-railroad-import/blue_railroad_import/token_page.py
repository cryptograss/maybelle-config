"""Token page content generation."""

from .models import Token


def generate_token_page_content(token: Token) -> str:
    """Generate wikitext content for a token page."""
    lines = [
        "{{Blue Railroad Token",
        f"|token_id={token.token_id}",
        f"|song_id={token.song_id or ''}",
        f"|contract_version={'V2' if token.is_v2 else 'V1'}",
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
        "",  # Template handles [[Category:Blue Railroad Tokens]] and burned detection
    ])

    if token.is_v2:
        lines.append("[[Category:Blue Railroad V2 Tokens]]")

    return "\n".join(lines)
