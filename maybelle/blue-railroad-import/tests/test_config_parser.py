"""Tests for wiki config parsing."""

import pytest
from blue_railroad_import.config_parser import (
    parse_template_params,
    strip_pre_blocks,
    parse_config_from_wikitext,
    get_default_config,
)


class TestParseTemplateParams:
    """Tests for parse_template_params function."""

    def test_parses_simple_params(self):
        result = parse_template_params('page=Leaderboard|sort=count')
        assert result == {'page': 'Leaderboard', 'sort': 'count'}

    def test_handles_whitespace(self):
        result = parse_template_params('  page = Leaderboard  |  sort = count  ')
        assert result == {'page': 'Leaderboard', 'sort': 'count'}

    def test_handles_empty_string(self):
        result = parse_template_params('')
        assert result == {}

    def test_ignores_params_without_equals(self):
        result = parse_template_params('page=Leaderboard|positional|sort=count')
        assert result == {'page': 'Leaderboard', 'sort': 'count'}


class TestStripPreBlocks:
    """Tests for strip_pre_blocks function."""

    def test_removes_pre_content(self):
        text = 'Before <pre>inside</pre> After'
        assert strip_pre_blocks(text) == 'Before  After'

    def test_removes_multiline_pre(self):
        text = '''Before
<pre>
line1
line2
</pre>
After'''
        result = strip_pre_blocks(text)
        assert '<pre>' not in result
        assert 'line1' not in result
        assert 'Before' in result
        assert 'After' in result

    def test_handles_no_pre_blocks(self):
        text = 'No pre blocks here'
        assert strip_pre_blocks(text) == text


class TestParseConfigFromWikitext:
    """Tests for parse_config_from_wikitext function."""

    def test_parses_source_template(self):
        wikitext = '''
{{BlueRailroadSource
|name=Blue Railroad V1
|chain_data_key=blueRailroads
|network_id=10
|contract=0x123
}}
'''
        config = parse_config_from_wikitext(wikitext)
        assert len(config.sources) == 1
        assert config.sources[0].name == 'Blue Railroad V1'
        assert config.sources[0].chain_data_key == 'blueRailroads'

    def test_parses_leaderboard_template(self):
        wikitext = '''
{{BlueRailroadLeaderboard
|page=Blue Railroad Leaderboard
|title=Overall Leaderboard
|sort=count
}}
'''
        config = parse_config_from_wikitext(wikitext)
        assert len(config.leaderboards) == 1
        assert config.leaderboards[0].page == 'Blue Railroad Leaderboard'
        assert config.leaderboards[0].title == 'Overall Leaderboard'
        assert config.leaderboards[0].sort == 'count'

    def test_parses_leaderboard_with_filter(self):
        wikitext = '''
{{BlueRailroadLeaderboard
|page=Squats Leaderboard
|filter_song_id=5
}}
'''
        config = parse_config_from_wikitext(wikitext)
        assert config.leaderboards[0].filter_song_id == '5'

    def test_parses_multiple_sources_and_leaderboards(self):
        wikitext = '''
{{BlueRailroadSource|name=V1|chain_data_key=blueRailroads}}
{{BlueRailroadSource|name=V2|chain_data_key=blueRailroadV2s}}
{{BlueRailroadLeaderboard|page=Overall}}
{{BlueRailroadLeaderboard|page=Squats|filter_song_id=5}}
'''
        config = parse_config_from_wikitext(wikitext)
        assert len(config.sources) == 2
        assert len(config.leaderboards) == 2

    def test_ignores_templates_in_pre_blocks(self):
        wikitext = '''
<pre>
{{BlueRailroadSource|name=Example|chain_data_key=example}}
</pre>
{{BlueRailroadSource|name=Real|chain_data_key=real}}
'''
        config = parse_config_from_wikitext(wikitext)
        assert len(config.sources) == 1
        assert config.sources[0].name == 'Real'

    def test_returns_none_for_empty_config(self):
        wikitext = 'Just some text, no templates'
        assert parse_config_from_wikitext(wikitext) is None

    def test_adds_default_source_if_only_leaderboards(self):
        wikitext = '{{BlueRailroadLeaderboard|page=Test}}'
        config = parse_config_from_wikitext(wikitext)
        assert len(config.sources) == 1
        assert config.sources[0].chain_data_key == 'blueRailroads'


class TestGetDefaultConfig:
    """Tests for get_default_config function."""

    def test_returns_valid_config(self):
        config = get_default_config()
        assert len(config.sources) >= 1
        assert len(config.leaderboards) >= 1

    def test_default_source_is_v1(self):
        config = get_default_config()
        assert config.sources[0].chain_data_key == 'blueRailroads'
