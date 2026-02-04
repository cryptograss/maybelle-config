"""Parse bot configuration from wiki page content."""

import re
from typing import Optional

from .models import BotConfig, Source, LeaderboardConfig


def parse_template_params(param_str: str) -> dict[str, str]:
    """Parse pipe-separated template parameters."""
    params = {}
    parts = param_str.split('|')

    for part in parts:
        if '=' in part:
            key, value = part.split('=', 1)
            params[key.strip()] = value.strip()

    return params


def strip_pre_blocks(text: str) -> str:
    """Remove content inside <pre> tags to avoid matching example templates."""
    return re.sub(r'<pre>.*?</pre>', '', text, flags=re.DOTALL)


def parse_config_from_wikitext(wikitext: str) -> Optional[BotConfig]:
    """
    Parse bot configuration from wiki page wikitext.

    Looks for {{BlueRailroadSource|...}} and {{BlueRailroadLeaderboard|...}} templates.
    """
    # Strip pre blocks to avoid matching documentation examples
    text = strip_pre_blocks(wikitext)

    config = BotConfig()

    # Parse {{BlueRailroadSource|...}} templates
    source_pattern = r'\{\{BlueRailroadSource\s*\n?((?:[^{}]|\{[^{]|\}[^}])*)\}\}'
    for match in re.finditer(source_pattern, text, re.DOTALL):
        params = parse_template_params(match.group(1))
        if params:
            config.sources.append(Source(
                name=params.get('name', params.get('chain_data_key', 'Unknown')),
                chain_data_key=params.get('chain_data_key', 'blueRailroads'),
                network_id=params.get('network_id', '10'),
                contract=params.get('contract', ''),
            ))

    # Parse {{BlueRailroadLeaderboard|...}} templates
    leaderboard_pattern = r'\{\{BlueRailroadLeaderboard\s*\n?((?:[^{}]|\{[^{]|\}[^}])*)\}\}'
    for match in re.finditer(leaderboard_pattern, text, re.DOTALL):
        params = parse_template_params(match.group(1))
        if params.get('page'):
            config.leaderboards.append(LeaderboardConfig(
                page=params['page'],
                title=params.get('title', ''),
                description=params.get('description', ''),
                filter_song_id=params.get('filter_song_id') or None,
                filter_owner=params.get('filter_owner') or None,
                sort=params.get('sort', 'count'),
            ))

    # Return None if no config found (triggers default)
    if not config.sources and not config.leaderboards:
        return None

    # Ensure at least one source if leaderboards defined
    if not config.sources:
        config.sources.append(Source(
            name='Blue Railroad (Optimism)',
            chain_data_key='blueRailroads',
            network_id='10',
            contract='0xCe09A2d0d0BDE635722D8EF31901b430E651dB52',
        ))

    return config


def get_default_config() -> BotConfig:
    """Return default configuration when wiki page is unavailable."""
    return BotConfig(
        sources=[
            Source(
                name='Blue Railroad V1 (Optimism)',
                chain_data_key='blueRailroads',
                network_id='10',
                contract='0xCe09A2d0d0BDE635722D8EF31901b430E651dB52',
            ),
            Source(
                name='Blue Railroad V2 (Optimism)',
                chain_data_key='blueRailroadV2s',
                network_id='10',
                contract='0x7C3aEBcD477C591EbCde3bC247B3A9531814B4B7',
            ),
        ],
        leaderboards=[
            LeaderboardConfig(
                page='Blue Railroad Leaderboard',
                title='Blue Railroad Leaderboard',
                description='Overall token holdings across all exercises',
                sort='count',
            ),
        ],
    )
