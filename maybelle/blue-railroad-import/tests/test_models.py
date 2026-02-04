"""Tests for data models."""

import pytest
from blue_railroad_import.models import Token, OwnerStats


class TestToken:
    """Tests for Token model."""

    def test_v1_token_is_not_v2(self):
        token = Token(
            token_id='1',
            source_key='blueRailroads',
            owner='0x123',
            owner_display='alice.eth',
            date=20260113,
            uri='ipfs://QmXyz123',
        )
        assert token.is_v2 is False

    def test_v2_token_is_v2(self):
        token = Token(
            token_id='5',
            source_key='blueRailroadV2s',
            owner='0x456',
            owner_display='bob.eth',
            blockheight=12345678,
            video_hash='0xabc123',
        )
        assert token.is_v2 is True

    def test_formatted_date_from_yyyymmdd(self):
        token = Token(
            token_id='1',
            source_key='blueRailroads',
            owner='0x123',
            owner_display='alice.eth',
            date=20260113,
        )
        assert token.formatted_date == '2026-01-13'

    def test_formatted_date_from_unix_timestamp(self):
        token = Token(
            token_id='1',
            source_key='blueRailroads',
            owner='0x123',
            owner_display='alice.eth',
            date=1705685808,  # 2024-01-19
        )
        assert token.formatted_date == '2024-01-19'

    def test_formatted_date_returns_none_when_missing(self):
        token = Token(
            token_id='1',
            source_key='blueRailroads',
            owner='0x123',
            owner_display='alice.eth',
        )
        assert token.formatted_date is None

    def test_ipfs_cid_from_v1_uri(self):
        token = Token(
            token_id='1',
            source_key='blueRailroads',
            owner='0x123',
            owner_display='alice.eth',
            uri='ipfs://QmXyz123abc',
        )
        assert token.ipfs_cid == 'QmXyz123abc'

    def test_ipfs_cid_from_v2_video_hash(self):
        """V2 video hash is converted to a CIDv0 (Qm...)."""
        token = Token(
            token_id='5',
            source_key='blueRailroadV2s',
            owner='0x456',
            owner_display='bob.eth',
            blockheight=12345678,
            # Full 32-byte hash (64 hex chars)
            video_hash='0x' + 'ab' * 32,
        )
        # video_hash_to_cidv0 converts bytes32 -> multihash -> base58
        assert token.ipfs_cid == 'QmZtnFaddFtzGNT8BxdHVbQrhSFdq1pWxud5z4fA4kxfDt'
        assert token.ipfs_cid.startswith('Qm')  # CIDv0 format

    def test_ipfs_cid_none_for_empty_v2_hash(self):
        token = Token(
            token_id='5',
            source_key='blueRailroadV2s',
            owner='0x456',
            owner_display='bob.eth',
            blockheight=12345678,
            video_hash='0x' + '0' * 64,  # Empty bytes32
        )
        assert token.ipfs_cid is None


class TestOwnerStats:
    """Tests for OwnerStats model."""

    def test_add_token_increments_count(self):
        stats = OwnerStats(address='0x123', display_name='alice.eth')
        stats.add_token('1', 100)
        stats.add_token('2', 200)
        assert stats.token_count == 2

    def test_add_token_tracks_ids(self):
        stats = OwnerStats(address='0x123', display_name='alice.eth')
        stats.add_token('1', 100)
        stats.add_token('5', 200)
        assert stats.token_ids == ['1', '5']

    def test_add_token_tracks_newest_date(self):
        stats = OwnerStats(address='0x123', display_name='alice.eth')
        stats.add_token('1', 100)
        stats.add_token('2', 300)
        stats.add_token('3', 200)
        assert stats.newest_date == 300

    def test_add_token_tracks_oldest_date(self):
        stats = OwnerStats(address='0x123', display_name='alice.eth')
        stats.add_token('1', 300)
        stats.add_token('2', 100)
        stats.add_token('3', 200)
        assert stats.oldest_date == 100

    def test_add_token_with_none_date(self):
        stats = OwnerStats(address='0x123', display_name='alice.eth')
        stats.add_token('1', None)
        assert stats.token_count == 1
        assert stats.newest_date == 0
        assert stats.oldest_date == 0
