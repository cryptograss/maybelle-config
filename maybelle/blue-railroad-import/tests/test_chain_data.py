"""Tests for chain data reading."""

import pytest
from blue_railroad_import.models import Source
from blue_railroad_import.chain_data import (
    parse_token,
    iter_tokens_from_source,
    aggregate_tokens_from_sources,
)


class TestParseToken:
    """Tests for parse_token function."""

    def test_parses_basic_v1_token(self):
        token_data = {
            'owner': '0x123',
            'ownerDisplay': 'alice.eth',
            'songId': '5',
            'date': 20260113,
            'uri': 'ipfs://QmXyz',
        }
        token = parse_token('1', token_data, 'blueRailroads')

        assert token.token_id == '1'
        assert token.source_key == 'blueRailroads'
        assert token.owner == '0x123'
        assert token.owner_display == 'alice.eth'
        assert token.song_id == '5'
        assert token.date == 20260113

    def test_parses_v2_token(self):
        token_data = {
            'owner': '0x456',
            'ownerDisplay': 'bob.eth',
            'songId': '5',
            'blockheight': 12345678,
            'videoHash': '0xabc123',
        }
        token = parse_token('5', token_data, 'blueRailroadV2s')

        assert token.is_v2 is True
        assert token.blockheight == 12345678
        assert token.video_hash == '0xabc123'

    def test_handles_bigint_array_format(self):
        """Chain data serializes BigInt as [value] arrays."""
        token_data = {
            'owner': '0x123',
            'songId': ['5'],  # Array format from BigInt
            'date': [20260113],
            'blockheight': [12345678],
        }
        token = parse_token('1', token_data, 'blueRailroads')

        assert token.song_id == '5'
        assert token.date == 20260113

    def test_uses_owner_as_display_fallback(self):
        token_data = {
            'owner': '0x123abc',
            # No ownerDisplay
        }
        token = parse_token('1', token_data, 'blueRailroads')

        assert token.owner_display == '0x123abc'


class TestIterTokensFromSource:
    """Tests for iter_tokens_from_source function."""

    def test_iterates_over_tokens(self):
        chain_data = {
            'blueRailroads': {
                '1': {'owner': '0x111'},
                '2': {'owner': '0x222'},
            }
        }
        source = Source(name='V1', chain_data_key='blueRailroads')

        tokens = list(iter_tokens_from_source(chain_data, source))

        assert len(tokens) == 2
        assert {t.token_id for t in tokens} == {'1', '2'}

    def test_returns_empty_for_missing_key(self):
        chain_data = {'otherKey': {}}
        source = Source(name='V1', chain_data_key='blueRailroads')

        tokens = list(iter_tokens_from_source(chain_data, source))

        assert tokens == []


class TestAggregateTokensFromSources:
    """Tests for aggregate_tokens_from_sources function."""

    def test_aggregates_from_multiple_sources(self):
        chain_data = {
            'blueRailroads': {
                '1': {'owner': '0x111'},
            },
            'blueRailroadV2s': {
                '5': {'owner': '0x555', 'blockheight': 123},
            },
        }
        sources = [
            Source(name='V1', chain_data_key='blueRailroads'),
            Source(name='V2', chain_data_key='blueRailroadV2s'),
        ]

        tokens = aggregate_tokens_from_sources(chain_data, sources)

        assert len(tokens) == 2
        assert 'blueRailroads_1' in tokens
        assert 'blueRailroadV2s_5' in tokens

    def test_prefixes_keys_to_avoid_collisions(self):
        """V1 and V2 might have same token IDs, keys prevent collision."""
        chain_data = {
            'blueRailroads': {
                '1': {'owner': '0x111'},
            },
            'blueRailroadV2s': {
                '1': {'owner': '0x222', 'blockheight': 123},  # Same ID!
            },
        }
        sources = [
            Source(name='V1', chain_data_key='blueRailroads'),
            Source(name='V2', chain_data_key='blueRailroadV2s'),
        ]

        tokens = aggregate_tokens_from_sources(chain_data, sources)

        assert len(tokens) == 2  # Both preserved, not overwritten
        assert tokens['blueRailroads_1'].owner == '0x111'
        assert tokens['blueRailroadV2s_1'].owner == '0x222'
