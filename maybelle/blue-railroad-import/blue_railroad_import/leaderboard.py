"""Leaderboard generation from aggregated token data."""

from typing import Optional

from .models import Token, LeaderboardConfig, OwnerStats


# Map song IDs to exercise names
EXERCISE_MAP = {
    '5': 'Squats ([[Blue Railroad Train]])',
    '6': 'Pushups ([[Nine Pound Hammer]])',
    '7': 'Squats ([[Blue Railroad Train]]) (legacy)',
    '10': 'Army Crawls ([[Ginseng Sullivan]])',
}


def filter_tokens(
    tokens: dict[str, Token],
    filter_song_id: Optional[str] = None,
    filter_owner: Optional[str] = None,
) -> dict[str, Token]:
    """Filter tokens by song ID and/or owner."""
    result = {}

    for key, token in tokens.items():
        # Apply song filter
        if filter_song_id:
            if token.song_id != filter_song_id:
                continue

        # Apply owner filter
        if filter_owner:
            if token.owner.lower() != filter_owner.lower():
                continue

        result[key] = token

    return result


def calculate_owner_stats(tokens: dict[str, Token]) -> dict[str, OwnerStats]:
    """Calculate aggregated statistics per owner."""
    stats: dict[str, OwnerStats] = {}

    for key, token in tokens.items():
        if not token.owner:
            continue

        owner_addr = token.owner

        if owner_addr not in stats:
            stats[owner_addr] = OwnerStats(
                address=owner_addr,
                display_name=token.owner_display,
            )

        # Use date or blockheight for sorting
        date_val = token.date if token.date else (token.blockheight or 0)
        stats[owner_addr].add_token(token.token_id, date_val)

    return stats


def sort_owners(stats: dict[str, OwnerStats], sort_by: str) -> list[str]:
    """Sort owner addresses by specified criteria."""
    if sort_by == 'newest':
        return sorted(stats.keys(), key=lambda a: stats[a].newest_date, reverse=True)
    elif sort_by == 'oldest':
        return sorted(stats.keys(), key=lambda a: stats[a].oldest_date or float('inf'))
    else:  # 'count' (default)
        return sorted(stats.keys(), key=lambda a: stats[a].token_count, reverse=True)


def generate_leaderboard_content(
    tokens: dict[str, Token],
    config: LeaderboardConfig,
) -> str:
    """Generate wikitext content for a leaderboard page."""

    # Filter tokens
    filtered = filter_tokens(
        tokens,
        filter_song_id=config.filter_song_id,
        filter_owner=config.filter_owner,
    )

    # Calculate stats
    owner_stats = calculate_owner_stats(filtered)

    # Sort owners
    sorted_owners = sort_owners(owner_stats, config.sort)

    # Build page content
    lines = [
        f"'''{config.title}''' tracks ownership of [[Blue Railroad]] NFT tokens.",
    ]

    if config.description:
        lines.append("")
        lines.append(config.description)

    if config.filter_song_id:
        exercise_name = EXERCISE_MAP.get(config.filter_song_id, f"Exercise ID {config.filter_song_id}")
        lines.append("")
        lines.append(f"'''Exercise:''' {exercise_name}")

    lines.extend([
        "",
        "''This page is automatically generated. See [[PickiPedia:BlueRailroadConfig|bot configuration]] to modify.''",
        "",
        "== Statistics ==",
        f"* '''Total Tokens:''' {len(filtered)}",
        f"* '''Total Holders:''' {len(owner_stats)}",
        "",
        "== Leaderboard ==",
        '{| class="wikitable sortable"',
        "! Rank !! Holder !! Tokens !! Token IDs",
    ])

    for rank, owner_addr in enumerate(sorted_owners, 1):
        stats = owner_stats[owner_addr]

        # Format token links
        sorted_ids = sorted(stats.token_ids, key=lambda x: int(x) if x.isdigit() else 0)
        token_links = [f"[[Blue Railroad Token {tid}|#{tid}]]" for tid in sorted_ids]
        token_links_str = ", ".join(token_links)

        # Format holder (just display name for now, could add SMW lookup later)
        holder_display = stats.display_name

        lines.append("|-")
        lines.append(f"| {rank} || {holder_display} || {stats.token_count} || {token_links_str}")

    lines.extend([
        "|}",
        "",
        "[[Category:Blue Railroad]]",
        "[[Category:Leaderboards]]",
    ])

    return "\n".join(lines)
