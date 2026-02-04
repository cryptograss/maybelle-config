"""Tests for leaderboard generation."""

import pytest
from blue_railroad_import.models import Token, LeaderboardConfig
from blue_railroad_import.leaderboard import (
    filter_tokens,
    calculate_owner_stats,
    sort_owners,
    generate_leaderboard_content,
)


@pytest.fixture
def sample_tokens():
    """Sample token data for testing."""
    return {
        'v1_1': Token(
            token_id='1',
            source_key='blueRailroads',
            owner='0xAlice',
            owner_display='alice.eth',
            song_id='5',
            date=100,
        ),
        'v1_2': Token(
            token_id='2',
            source_key='blueRailroads',
            owner='0xAlice',
            owner_display='alice.eth',
            song_id='5',
            date=200,
        ),
        'v1_3': Token(
            token_id='3',
            source_key='blueRailroads',
            owner='0xBob',
            owner_display='bob.eth',
            song_id='6',
            date=150,
        ),
        'v2_5': Token(
            token_id='5',
            source_key='blueRailroadV2s',
            owner='0xAlice',
            owner_display='alice.eth',
            song_id='5',
            blockheight=300,
        ),
    }


class TestFilterTokens:
    """Tests for filter_tokens function."""

    def test_no_filter_returns_all(self, sample_tokens):
        result = filter_tokens(sample_tokens)
        assert len(result) == 4

    def test_filters_by_song_id(self, sample_tokens):
        result = filter_tokens(sample_tokens, filter_song_id='5')
        assert len(result) == 3
        assert all(t.song_id == '5' for t in result.values())

    def test_filters_by_owner(self, sample_tokens):
        result = filter_tokens(sample_tokens, filter_owner='0xBob')
        assert len(result) == 1
        assert list(result.values())[0].owner == '0xBob'

    def test_owner_filter_is_case_insensitive(self, sample_tokens):
        result = filter_tokens(sample_tokens, filter_owner='0xbob')  # lowercase
        assert len(result) == 1

    def test_combines_filters(self, sample_tokens):
        result = filter_tokens(sample_tokens, filter_song_id='5', filter_owner='0xAlice')
        assert len(result) == 3  # Alice has 3 song_id=5 tokens


class TestCalculateOwnerStats:
    """Tests for calculate_owner_stats function."""

    def test_calculates_token_counts(self, sample_tokens):
        stats = calculate_owner_stats(sample_tokens)
        assert stats['0xAlice'].token_count == 3
        assert stats['0xBob'].token_count == 1

    def test_tracks_token_ids(self, sample_tokens):
        stats = calculate_owner_stats(sample_tokens)
        assert set(stats['0xAlice'].token_ids) == {'1', '2', '5'}

    def test_tracks_newest_date(self, sample_tokens):
        stats = calculate_owner_stats(sample_tokens)
        # Alice's newest is v2 blockheight 300
        assert stats['0xAlice'].newest_date == 300

    def test_tracks_oldest_date(self, sample_tokens):
        stats = calculate_owner_stats(sample_tokens)
        # Alice's oldest is v1 date 100
        assert stats['0xAlice'].oldest_date == 100

    def test_preserves_display_name(self, sample_tokens):
        stats = calculate_owner_stats(sample_tokens)
        assert stats['0xAlice'].display_name == 'alice.eth'


class TestSortOwners:
    """Tests for sort_owners function."""

    def test_sort_by_count_descending(self, sample_tokens):
        stats = calculate_owner_stats(sample_tokens)
        sorted_owners = sort_owners(stats, 'count')
        assert sorted_owners[0] == '0xAlice'  # 3 tokens
        assert sorted_owners[1] == '0xBob'    # 1 token

    def test_sort_by_newest(self, sample_tokens):
        stats = calculate_owner_stats(sample_tokens)
        sorted_owners = sort_owners(stats, 'newest')
        assert sorted_owners[0] == '0xAlice'  # newest=300

    def test_sort_by_oldest(self, sample_tokens):
        stats = calculate_owner_stats(sample_tokens)
        sorted_owners = sort_owners(stats, 'oldest')
        assert sorted_owners[0] == '0xAlice'  # oldest=100


class TestGenerateLeaderboardContent:
    """Tests for generate_leaderboard_content function."""

    def test_generates_valid_wikitext(self, sample_tokens):
        config = LeaderboardConfig(page='Test Leaderboard', title='Test')
        content = generate_leaderboard_content(sample_tokens, config)

        assert "'''Test'''" in content
        assert 'wikitable sortable' in content
        assert '[[Category:Blue Railroad]]' in content

    def test_includes_statistics(self, sample_tokens):
        config = LeaderboardConfig(page='Test')
        content = generate_leaderboard_content(sample_tokens, config)

        assert "'''Total Tokens:''' 4" in content
        assert "'''Total Holders:''' 2" in content

    def test_includes_token_links(self, sample_tokens):
        config = LeaderboardConfig(page='Test')
        content = generate_leaderboard_content(sample_tokens, config)

        assert '[[Blue Railroad Token 1|#1]]' in content
        assert '[[Blue Railroad Token 5|#5]]' in content

    def test_includes_exercise_name_for_filtered(self, sample_tokens):
        config = LeaderboardConfig(page='Test', filter_song_id='5')
        content = generate_leaderboard_content(sample_tokens, config)

        assert 'Squats' in content
        assert 'Blue Railroad Train' in content

    def test_filters_tokens_before_generating(self, sample_tokens):
        config = LeaderboardConfig(page='Test', filter_song_id='6')
        content = generate_leaderboard_content(sample_tokens, config)

        # Only Bob has song_id=6
        assert "'''Total Tokens:''' 1" in content
        assert "'''Total Holders:''' 1" in content
        assert 'bob.eth' in content


class TestLeaderboardAggregation:
    """
    Critical tests for the aggregation bug that caused the bot loop.

    The PHP version generated leaderboards inside the per-source loop,
    so V1 and V2 tokens weren't combined - the second source would
    overwrite the first's leaderboard with different content.
    """

    def test_aggregated_tokens_include_all_sources(self, sample_tokens):
        """Leaderboard includes BOTH V1 and V2 tokens."""
        config = LeaderboardConfig(page='Test')
        content = generate_leaderboard_content(sample_tokens, config)

        # Alice's total across V1 and V2
        assert '| 1 || alice.eth || 3 ||' in content

    def test_token_ids_sorted_numerically(self, sample_tokens):
        """Token IDs in leaderboard are sorted numerically."""
        config = LeaderboardConfig(page='Test')
        content = generate_leaderboard_content(sample_tokens, config)

        # Alice's tokens listed as 1, 2, 5 in numeric order
        alice_row = [line for line in content.split('\n') if 'alice.eth' in line][0]
        assert '#1]], [[Blue Railroad Token 2|#2]], [[Blue Railroad Token 5|#5]]' in alice_row

    def test_idempotent_content_generation(self, sample_tokens):
        """Same input always produces same output."""
        config = LeaderboardConfig(page='Test', sort='count')

        content1 = generate_leaderboard_content(sample_tokens, config)
        content2 = generate_leaderboard_content(sample_tokens, config)

        assert content1 == content2
