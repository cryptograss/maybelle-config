"""Chain data reading and token parsing."""

import json
from pathlib import Path
from typing import Iterator, Optional

from .models import Token, Source


def load_chain_data(path: Path) -> dict:
    """Load chain data JSON from file."""
    with open(path) as f:
        return json.load(f)


def load_ens_mapping(chain_data: dict) -> dict[str, str]:
    """Extract ENS name → address mapping from chain data.

    The ensToAddress mapping is included in chainData.json by arthel's
    chain data fetch, providing forward-resolved ENS names to their
    current addresses.

    Args:
        chain_data: The loaded chain data dict

    Returns:
        Dict mapping lowercase ENS names to addresses, or empty dict if
        the mapping isn't present.
    """
    return chain_data.get('ensToAddress', {})


def resolve_ens_to_address(
    ens_name: str,
    ens_mapping: dict[str, str],
) -> Optional[str]:
    """Resolve an ENS name to an address using the mapping.

    Args:
        ens_name: The ENS name (e.g., 'justinholmes.eth')
        ens_mapping: The ENS→address mapping dict

    Returns:
        The resolved address, or None if not found in mapping.
    """
    return ens_mapping.get(ens_name.lower())


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

    Keys are just token IDs (not source-prefixed) since each token ID
    should map to exactly one wiki page. V2 tokens take precedence over
    V1 tokens with the same ID, reflecting the migration model where
    a migrated token's V2 version is canonical.
    """
    all_tokens = {}

    for source in sources:
        for token in iter_tokens_from_source(chain_data, source):
            token_key = token.token_id
            existing = all_tokens.get(token_key)

            # V2 tokens take precedence over V1
            if existing is None:
                all_tokens[token_key] = token
            elif token.is_v2 and not existing.is_v2:
                # New token is V2, existing is V1 - replace with V2
                all_tokens[token_key] = token
            # else: keep existing (either same version or existing is V2)

    return all_tokens
