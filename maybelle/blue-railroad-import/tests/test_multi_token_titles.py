"""Test that Release titles include all token IDs for shared CIDs."""

from unittest.mock import MagicMock
from blue_railroad_import.release_page import ensure_release_for_token, _enrich_existing
from blue_railroad_import.models import Token


class TestMultiTokenTitles:
    """Test the multi-token title format."""

    def _make_token(self, token_id, song_id='7', cid='QmTEST123'):
        return Token(
            token_id=str(token_id),
            source_key='blueRailroadV2s',
            owner='0x123',
            owner_display='test.eth',
            song_id=song_id,
            blockheight=12345,
            video_hash=None,
        )

    def test_single_token_title(self):
        """Single token should produce 'Song (Exercise) #N' format."""
        wiki = MagicMock()
        wiki.page_exists.return_value = False

        token = self._make_token(3, song_id='7')
        # Manually set ipfs_cid since we're not using video_hash
        token._test_cid = 'QmTEST123'
        # Override ipfs_cid property
        type(token).ipfs_cid = property(lambda self: 'QmTEST123')

        ensure_release_for_token(wiki, token, all_token_ids=[3])

        # Check that save_page was called with correct title in YAML
        call_args = wiki.save_page.call_args
        yaml_content = call_args[0][1]
        assert 'Blue Railroad Train (Squats) #3' in yaml_content

    def test_multi_token_title(self):
        """Multiple tokens sharing CID should produce '#3, #4' format."""
        wiki = MagicMock()
        wiki.page_exists.return_value = False

        token = self._make_token(3, song_id='7')
        type(token).ipfs_cid = property(lambda self: 'QmTEST123')

        ensure_release_for_token(wiki, token, all_token_ids=[3, 4])

        call_args = wiki.save_page.call_args
        yaml_content = call_args[0][1]
        assert 'Blue Railroad Train (Squats) #3, #4' in yaml_content

    def test_enrich_updates_title_when_different(self):
        """Enriching should update title from '#3' to '#3, #4'."""
        wiki = MagicMock()
        wiki.get_page_content.return_value = (
            "title: 'Blue Railroad Train (Squats) #3'\n"
            "release_type: blue-railroad\n"
            "file_type: video/webm\n"
        )

        result = _enrich_existing(
            wiki, 'Release:QmTEST', 'QmTEST',
            title='Blue Railroad Train (Squats) #3, #4',
            release_type='blue-railroad',
            file_type='video/webm',
        )

        # Should have called save_page because title differs
        assert wiki.save_page.called, "save_page should have been called"
        call_args = wiki.save_page.call_args
        yaml_content = call_args[0][1]
        assert '#3, #4' in yaml_content

    def test_enrich_unchanged_when_title_matches(self):
        """Enriching should not update when title already matches."""
        wiki = MagicMock()
        wiki.get_page_content.return_value = (
            "title: 'Blue Railroad Train (Squats) #3, #4'\n"
            "release_type: blue-railroad\n"
            "file_type: video/webm\n"
        )

        result = _enrich_existing(
            wiki, 'Release:QmTEST', 'QmTEST',
            title='Blue Railroad Train (Squats) #3, #4',
            release_type='blue-railroad',
            file_type='video/webm',
        )

        assert result.action == 'unchanged'
