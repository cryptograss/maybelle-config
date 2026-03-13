"""Tests for torrent enrichment module."""

import json
import pytest
from unittest.mock import patch, MagicMock

from blue_railroad_import.torrent_enrichment import (
    append_torrent_fields,
    enrich_releases,
)
from blue_railroad_import.wiki_client import DryRunClient


SAMPLE_TRACKERS = [
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.tracker.cl:1337/announce",
]


class TestAppendTorrentFields:
    """Test that torrent fields are appended without reformatting existing YAML."""

    def test_appends_to_simple_yaml(self):
        existing = "title: My Album\nipfs_cid: QmTest123\n"
        result = append_torrent_fields(existing, "abc123hash", SAMPLE_TRACKERS)

        # Original content preserved exactly
        assert result.startswith("title: My Album\nipfs_cid: QmTest123\n")
        # New fields appended
        assert "bittorrent_infohash: abc123hash" in result
        assert "udp://tracker.opentrackr.org:1337/announce" in result

    def test_preserves_key_order(self):
        """Existing keys must stay in their original order, not get alphabetized."""
        existing = "title: Z Album\nartist: A Person\nipfs_cid: QmTest\n"
        result = append_torrent_fields(existing, "deadbeef", SAMPLE_TRACKERS)

        # Find positions of original keys — they must be in original order
        title_pos = result.index("title: Z Album")
        artist_pos = result.index("artist: A Person")
        ipfs_pos = result.index("ipfs_cid: QmTest")
        assert title_pos < artist_pos < ipfs_pos

    def test_preserves_comments_and_whitespace(self):
        """Comments and extra whitespace in the original should survive."""
        existing = "# This is a release\ntitle: Test\n\nipfs_cid: QmFoo\n"
        result = append_torrent_fields(existing, "hash123", SAMPLE_TRACKERS)

        assert "# This is a release" in result
        assert result.startswith("# This is a release\ntitle: Test\n\nipfs_cid: QmFoo")

    def test_preserves_list_formatting(self):
        """Existing list fields (like pinned_on) should keep their formatting."""
        existing = "title: Test\npinned_on:\n  - delivery-kid\n  - pinata\nipfs_cid: QmBar\n"
        result = append_torrent_fields(existing, "hash456", SAMPLE_TRACKERS)

        # Original list format preserved
        assert "pinned_on:\n  - delivery-kid\n  - pinata" in result

    def test_skips_if_already_has_infohash(self):
        existing = "title: Test\nbittorrent_infohash: existinghash\nipfs_cid: QmTest\n"
        result = append_torrent_fields(existing, "newhash", SAMPLE_TRACKERS)

        # Should return unchanged
        assert result == existing
        assert "newhash" not in result

    def test_handles_no_trailing_newline(self):
        existing = "title: Test\nipfs_cid: QmTest"
        result = append_torrent_fields(existing, "hash789", SAMPLE_TRACKERS)

        assert "bittorrent_infohash: hash789" in result
        # Should not have double newlines from poor joining
        assert "\n\n\n" not in result

    def test_handles_empty_content(self):
        result = append_torrent_fields("", "hash000", SAMPLE_TRACKERS)
        # Empty string parses as None in YAML, not a dict — should return unchanged
        assert result == ""

    def test_handles_non_dict_yaml(self):
        result = append_torrent_fields("just a string", "hash000", SAMPLE_TRACKERS)
        assert result == "just a string"

    def test_handles_invalid_yaml(self):
        result = append_torrent_fields("{{broken: [yaml", "hash000", SAMPLE_TRACKERS)
        assert result == "{{broken: [yaml"

    def test_result_is_valid_yaml(self):
        """The final result should parse as valid YAML with all fields present."""
        import yaml

        existing = "title: My Album\nipfs_cid: QmTest123\npinned_on:\n  - delivery-kid\n"
        result = append_torrent_fields(existing, "abc123hash", SAMPLE_TRACKERS)

        parsed = yaml.safe_load(result)
        assert parsed["title"] == "My Album"
        assert parsed["ipfs_cid"] == "QmTest123"
        assert parsed["pinned_on"] == ["delivery-kid"]
        assert parsed["bittorrent_infohash"] == "abc123hash"
        assert parsed["bittorrent_trackers"] == SAMPLE_TRACKERS

    def test_idempotent(self):
        """Running append twice should not duplicate fields."""
        existing = "title: Test\nipfs_cid: QmTest\n"
        first = append_torrent_fields(existing, "hash123", SAMPLE_TRACKERS)
        second = append_torrent_fields(first, "hash123", SAMPLE_TRACKERS)
        assert first == second


class TestEnrichReleases:
    """Test the full enrichment flow with mocked external calls."""

    def _mock_releases_response(self, releases):
        """Create a mock urllib response for the releaselist API."""
        body = json.dumps({"releases": releases, "count": len(releases)}).encode()
        mock_response = MagicMock()
        mock_response.read.return_value = body
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        return mock_response

    def _mock_torrent_response(self, cid, infohash, trackers):
        """Create a mock urllib response for the torrent endpoint."""
        body = json.dumps({
            "success": True,
            "cid": cid,
            "infohash": infohash,
            "trackers": trackers,
            "file_count": 5,
            "total_size": 100000,
            "piece_length": 262144,
        }).encode()
        mock_response = MagicMock()
        mock_response.read.return_value = body
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        return mock_response

    @patch("blue_railroad_import.torrent_enrichment.urllib.request.urlopen")
    @patch("blue_railroad_import.torrent_enrichment.urllib.request.Request")
    def test_enriches_release_page(self, mock_request_cls, mock_urlopen):
        """Full flow: query releases, generate torrent, update wiki page."""
        existing_yaml = "title: Test Album\nipfs_cid: QmTestCID\n"

        wiki = DryRunClient(existing_pages={
            "Release:QmTestCID": existing_yaml,
        })

        releases = [{"page_title": "QmTestCID", "ipfs_cid": "QmTestCID", "title": "Test Album"}]

        # First call: releaselist API, second call: torrent endpoint
        mock_urlopen.side_effect = [
            self._mock_releases_response(releases),
            self._mock_torrent_response("QmTestCID", "deadbeef123", SAMPLE_TRACKERS),
        ]

        results = enrich_releases(
            wiki=wiki,
            wiki_api_url="https://pickipedia.xyz/api.php",
            delivery_kid_url="https://delivery-kid.cryptograss.live",
            delivery_kid_api_key="test-key",
        )

        assert len(results) == 1
        assert results[0].action in ("updated", "created")

        # Check what was saved
        assert len(wiki.saved_pages) == 1
        saved_title, saved_content, saved_summary = wiki.saved_pages[0]
        assert saved_title == "Release:QmTestCID"
        assert "bittorrent_infohash: deadbeef123" in saved_content
        # Original content preserved
        assert saved_content.startswith("title: Test Album\nipfs_cid: QmTestCID\n")

    @patch("blue_railroad_import.torrent_enrichment.urllib.request.urlopen")
    def test_no_releases_missing_torrent(self, mock_urlopen):
        """When no releases need enrichment, nothing happens."""
        wiki = DryRunClient()
        mock_urlopen.return_value = self._mock_releases_response([])

        results = enrich_releases(
            wiki=wiki,
            wiki_api_url="https://pickipedia.xyz/api.php",
            delivery_kid_url="https://delivery-kid.cryptograss.live",
            delivery_kid_api_key="test-key",
        )

        assert results == []
        assert wiki.saved_pages == []

    @patch("blue_railroad_import.torrent_enrichment.urllib.request.urlopen")
    @patch("blue_railroad_import.torrent_enrichment.urllib.request.Request")
    def test_torrent_generation_failure(self, mock_request_cls, mock_urlopen):
        """When delivery-kid fails, record error and continue."""
        wiki = DryRunClient(existing_pages={
            "Release:QmTest1": "title: Album 1\nipfs_cid: QmTest1\n",
        })

        releases = [{"page_title": "QmTest1", "ipfs_cid": "QmTest1", "title": "Album 1"}]

        error_body = json.dumps({
            "success": False, "cid": "QmTest1",
            "error": "Could not fetch CID from IPFS",
        }).encode()
        error_response = MagicMock()
        error_response.read.return_value = error_body
        error_response.__enter__ = lambda s: s
        error_response.__exit__ = MagicMock(return_value=False)

        mock_urlopen.side_effect = [
            self._mock_releases_response(releases),
            error_response,
        ]

        results = enrich_releases(
            wiki=wiki,
            wiki_api_url="https://pickipedia.xyz/api.php",
            delivery_kid_url="https://delivery-kid.cryptograss.live",
            delivery_kid_api_key="test-key",
        )

        assert len(results) == 1
        assert results[0].action == "error"
        assert wiki.saved_pages == []

    @patch("blue_railroad_import.torrent_enrichment.urllib.request.urlopen")
    @patch("blue_railroad_import.torrent_enrichment.urllib.request.Request")
    def test_skips_already_enriched(self, mock_request_cls, mock_urlopen):
        """If page already has infohash (race condition), skip gracefully."""
        existing_yaml = "title: Test\nipfs_cid: QmTest\nbittorrent_infohash: existing\n"

        wiki = DryRunClient(existing_pages={
            "Release:QmTest": existing_yaml,
        })

        releases = [{"page_title": "QmTest", "ipfs_cid": "QmTest", "title": "Test"}]

        mock_urlopen.side_effect = [
            self._mock_releases_response(releases),
            self._mock_torrent_response("QmTest", "newhash", SAMPLE_TRACKERS),
        ]

        results = enrich_releases(
            wiki=wiki,
            wiki_api_url="https://pickipedia.xyz/api.php",
            delivery_kid_url="https://delivery-kid.cryptograss.live",
            delivery_kid_api_key="test-key",
        )

        assert len(results) == 1
        assert results[0].action == "unchanged"
        assert wiki.saved_pages == []
