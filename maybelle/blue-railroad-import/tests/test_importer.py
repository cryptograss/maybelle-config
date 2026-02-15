"""Tests for the main importer."""

import json
import pytest
from pathlib import Path
from tempfile import NamedTemporaryFile

from blue_railroad_import.importer import BlueRailroadImporter, ImportResults
from blue_railroad_import.wiki_client import DryRunClient, SaveResult


@pytest.fixture
def chain_data_file(tmp_path):
    """Create a temporary chain data file."""
    data = {
        'blueRailroads': {
            '1': {
                'owner': '0xAlice',
                'ownerDisplay': 'alice.eth',
                'songId': '5',
                'date': 20260113,
                'uri': 'ipfs://QmV1Token1',
            },
            '2': {
                'owner': '0xBob',
                'ownerDisplay': 'bob.eth',
                'songId': '5',
                'date': 20260114,
                'uri': 'ipfs://QmV1Token2',
            },
        },
        'blueRailroadV2s': {
            '5': {
                'owner': '0xAlice',
                'ownerDisplay': 'alice.eth',
                'songId': '5',
                'blockheight': 12345678,
                'videoHash': '0xabc123',
            },
        },
    }

    file_path = tmp_path / 'chainData.json'
    file_path.write_text(json.dumps(data))
    return file_path


@pytest.fixture
def wiki_config_content():
    """Sample wiki config page content."""
    return '''
== Configuration ==

{{BlueRailroadSource
|name=Blue Railroad V1
|chain_data_key=blueRailroads
}}

{{BlueRailroadSource
|name=Blue Railroad V2
|chain_data_key=blueRailroadV2s
}}

{{BlueRailroadLeaderboard
|page=Blue Railroad Leaderboard
|title=Overall Leaderboard
|sort=count
}}

{{BlueRailroadLeaderboard
|page=Blue Railroad Squats Leaderboard
|filter_song_id=5
}}
'''


class TestImportResults:
    """Tests for ImportResults tracking."""

    def test_tracks_token_results(self):
        results = ImportResults()
        results.token_pages.append(SaveResult('Token 1', 'created'))
        results.token_pages.append(SaveResult('Token 2', 'updated'))
        results.token_pages.append(SaveResult('Token 3', 'unchanged'))
        results.token_pages.append(SaveResult('Token 4', 'error', 'failed'))

        assert len(results.token_pages_created) == 1
        assert results.token_pages_created[0].page_title == 'Token 1'
        assert len(results.token_pages_updated) == 1
        assert len(results.token_pages_unchanged) == 1
        assert len(results.token_pages_error) == 1
        assert len(results.errors) == 1

    def test_tracks_leaderboard_results(self):
        results = ImportResults()
        results.leaderboard_pages.append(SaveResult('LB1', 'created'))
        results.leaderboard_pages.append(SaveResult('LB2', 'unchanged'))

        assert len(results.leaderboard_pages_created) == 1
        assert len(results.leaderboard_pages_unchanged) == 1


class TestBlueRailroadImporter:
    """Tests for BlueRailroadImporter."""

    def test_loads_config_from_wiki(self, chain_data_file, wiki_config_content):
        wiki = DryRunClient(existing_pages={
            'PickiPedia:BlueRailroadConfig': wiki_config_content,
        })
        importer = BlueRailroadImporter(wiki, chain_data_file)

        config = importer.load_config()

        assert len(config.sources) == 2
        assert len(config.leaderboards) == 2

    def test_uses_default_config_when_page_missing(self, chain_data_file):
        wiki = DryRunClient(existing_pages={})
        importer = BlueRailroadImporter(wiki, chain_data_file)

        config = importer.load_config()

        assert len(config.sources) >= 1
        assert len(config.leaderboards) >= 1

    def test_aggregates_tokens_from_all_sources(self, chain_data_file, wiki_config_content):
        wiki = DryRunClient(existing_pages={
            'PickiPedia:BlueRailroadConfig': wiki_config_content,
        })
        importer = BlueRailroadImporter(wiki, chain_data_file)

        config = importer.load_config()
        chain_data = importer.load_chain_data()
        tokens = importer.load_tokens(chain_data, config)

        # 3 tokens total: 2 from V1, 1 from V2
        assert len(tokens) == 3

    def test_run_creates_token_pages(self, chain_data_file, wiki_config_content):
        wiki = DryRunClient(existing_pages={
            'PickiPedia:BlueRailroadConfig': wiki_config_content,
        })
        importer = BlueRailroadImporter(wiki, chain_data_file)

        results = importer.run()

        # 3 token pages created
        assert len(results.token_pages_created) == 3

        # Verify save_page() was called for each token
        saved_titles = [title for title, _, _ in wiki.saved_pages]
        assert 'Blue Railroad Token 1' in saved_titles
        assert 'Blue Railroad Token 2' in saved_titles
        assert 'Blue Railroad Token 5' in saved_titles

    def test_run_creates_leaderboards(self, chain_data_file, wiki_config_content):
        wiki = DryRunClient(existing_pages={
            'PickiPedia:BlueRailroadConfig': wiki_config_content,
        })
        importer = BlueRailroadImporter(wiki, chain_data_file)

        results = importer.run()

        # 2 leaderboards created
        assert len(results.leaderboard_pages_created) == 2

        saved_titles = [title for title, _, _ in wiki.saved_pages]
        assert 'Blue Railroad Leaderboard' in saved_titles
        assert 'Blue Railroad Squats Leaderboard' in saved_titles

    def test_skips_unchanged_pages(self, chain_data_file, wiki_config_content):
        # Pre-populate with existing content
        wiki = DryRunClient(existing_pages={
            'PickiPedia:BlueRailroadConfig': wiki_config_content,
        })

        # First run to get the content
        importer = BlueRailroadImporter(wiki, chain_data_file)
        importer.run()

        # Get the generated content
        generated_pages = {title: content for title, content, _ in wiki.saved_pages}

        # Second run with pre-existing content
        wiki2 = DryRunClient(existing_pages={
            'PickiPedia:BlueRailroadConfig': wiki_config_content,
            **generated_pages,
        })
        importer2 = BlueRailroadImporter(wiki2, chain_data_file)
        results2 = importer2.run()

        # All pages match existing content — nothing to update
        assert len(results2.token_pages_created) == 0
        assert len(results2.token_pages_updated) == 0
        assert len(results2.token_pages_unchanged) == 3
        assert len(results2.leaderboard_pages_unchanged) == 2


class TestLeaderboardAggregationIntegration:
    """
    Integration test for the critical aggregation fix.

    This tests the exact scenario that caused the bot loop:
    multiple sources must be aggregated BEFORE generating leaderboards,
    not generate separate leaderboards per source.
    """

    def test_leaderboard_includes_all_sources(self, chain_data_file, wiki_config_content):
        wiki = DryRunClient(existing_pages={
            'PickiPedia:BlueRailroadConfig': wiki_config_content,
        })
        importer = BlueRailroadImporter(wiki, chain_data_file)
        importer.run()

        # Find the overall leaderboard content
        leaderboard_content = None
        for title, content, _ in wiki.saved_pages:
            if title == 'Blue Railroad Leaderboard':
                leaderboard_content = content
                break

        assert leaderboard_content is not None

        # Alice holds 2 tokens (1 from V1, 1 from V2)
        # The row format is: | rank || holder || count || token links
        assert '| 1 || alice.eth || 2 ||' in leaderboard_content

        # Token IDs from both sources appear in the leaderboard
        assert '#1]]' in leaderboard_content  # V1 token
        assert '#5]]' in leaderboard_content  # V2 token

    def test_multiple_runs_produce_identical_output(self, chain_data_file, wiki_config_content):
        """Idempotency: two runs with identical input produce identical output."""
        wiki1 = DryRunClient(existing_pages={
            'PickiPedia:BlueRailroadConfig': wiki_config_content,
        })
        wiki2 = DryRunClient(existing_pages={
            'PickiPedia:BlueRailroadConfig': wiki_config_content,
        })

        importer1 = BlueRailroadImporter(wiki1, chain_data_file)
        importer2 = BlueRailroadImporter(wiki2, chain_data_file)

        importer1.run()
        importer2.run()

        # Get leaderboard content from both runs
        lb1 = next(c for t, c, _ in wiki1.saved_pages if t == 'Blue Railroad Leaderboard')
        lb2 = next(c for t, c, _ in wiki2.saved_pages if t == 'Blue Railroad Leaderboard')

        assert lb1 == lb2
