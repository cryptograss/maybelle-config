"""Chain data reading and token parsing."""

import json
from pathlib import Path
from typing import Iterator

from .models import Token, Source


def load_chain_data(path: Path) -> dict:
    """Load chain data JSON from file."""
    with open(path) as f:
        return json.load(f)


def parse_token(token_id: str, token_data: dict, source_key: str) -> Token:
    """Parse a single token from chain data."""

    def extract_value(data, key):
        """Extract value, handling array format from BigInt serialization."""
        val = data.get(key)
        if isinstance(val, list):
            return val[0] if val else None
        return val

    return Token(
        token_id=token_id,
        source_key=source_key,
        owner=token_data.get('owner', ''),
        owner_display=token_data.get('ownerDisplay', token_data.get('owner', '')),
        song_id=str(extract_value(token_data, 'songId')) if extract_value(token_data, 'songId') else None,
        date=extract_value(token_data, 'date'),
        uri=token_data.get('uri'),
        blockheight=extract_value(token_data, 'blockheight'),
        video_hash=token_data.get('videoHash'),
    )


def iter_tokens_from_source(chain_data: dict, source: Source) -> Iterator[Token]:
    """Iterate over tokens from a specific source in chain data."""
    source_data = chain_data.get(source.chain_data_key, {})

    for token_id, token_data in source_data.items():
        yield parse_token(token_id, token_data, source.chain_data_key)


def aggregate_tokens_from_sources(chain_data: dict, sources: list[Source]) -> dict[str, Token]:
    """
    Aggregate all tokens from all sources into a single dict.

    Keys are prefixed with source key to avoid collisions between
    V1 and V2 tokens with the same ID.
    """
    all_tokens = {}

    for source in sources:
        for token in iter_tokens_from_source(chain_data, source):
            # Use source-prefixed key to avoid collisions
            aggregate_key = f"{token.source_key}_{token.token_id}"
            all_tokens[aggregate_key] = token

    return all_tokens
