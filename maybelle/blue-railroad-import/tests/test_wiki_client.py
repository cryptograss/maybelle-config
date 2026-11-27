"""Tests for wiki client operations."""

import pytest

from blue_railroad_import.wiki_client import (
    parse_smw_token_response,
    TokenInfo,
    DryRunClient,
)


# Realistic SMW API response fixture based on actual PickiPedia data
SMW_RESPONSE_TWO_TOKENS = {
    "query": {
        "printrequests": [
            {"label": "", "key": "", "redi": "", "typeid": "_wpg", "mode": 2},
            {"label": "Token ID", "key": "Token_ID", "redi": "", "typeid": "_wpg", "mode": 1},
            {"label": "Owner Address", "key": "Owner_Address", "redi": "", "typeid": "_wpg", "mode": 1},
            {"label": "Owner", "key": "Owner", "redi": "", "typeid": "_wpg", "mode": 1},
        ],
        "results": {
            "Blue Railroad Token 10": {
                "printouts": {
                    "Token ID": [{"fulltext": "10", "fullurl": "https://pickipedia.xyz/wiki/10", "namespace": 0, "exists": "", "displaytitle": ""}],
                    "Owner Address": [{"fulltext": "0x4f84b3650Dbf651732a41647618E7fF94A633F09", "fullurl": "https://pickipedia.xyz/wiki/0x4f84b3650Dbf651732a41647618E7fF94A633F09", "namespace": 0, "exists": "", "displaytitle": ""}],
                    "Owner": [{"fulltext": "Justin Myles Holmes", "fullurl": "https://pickipedia.xyz/wiki/Justin_Myles_Holmes", "namespace": 0, "exists": "1", "displaytitle": ""}],
                },
                "fulltext": "Blue Railroad Token 10",
                "fullurl": "https://pickipedia.xyz/wiki/Blue_Railroad_Token_10",
                "namespace": 0,
                "exists": "1",
                "displaytitle": "",
            },
            "Blue Railroad Token 11": {
                "printouts": {
                    "Token ID": [{"fulltext": "11", "fullurl": "https://pickipedia.xyz/wiki/11", "namespace": 0, "exists": "", "displaytitle": ""}],
                    "Owner Address": [{"fulltext": "0x5da9E9c29365959f1DE138Ba62c19274F7eccF4F", "fullurl": "https://pickipedia.xyz/wiki/0x5da9E9c29365959f1DE138Ba62c19274F7eccF4F", "namespace": 0, "exists": "", "displaytitle": ""}],
                    "Owner": [{"fulltext": "Skyler Golden", "fullurl": "https://pickipedia.xyz/wiki/Skyler_Golden", "namespace": 0, "exists": "1", "displaytitle": ""}],
                },
                "fulltext": "Blue Railroad Token 11",
                "fullurl": "https://pickipedia.xyz/wiki/Blue_Railroad_Token_11",
                "namespace": 0,
                "exists": "1",
                "displaytitle": "",
            },
        },
        "serializer": "SMW\\Serializers\\QueryResultSerializer",
        "version": 2,
        "meta": {"hash": "fd6ae4a3e90e71dd0bb2ddeaf2648f9e", "count": 2, "offset": 0, "source": "", "time": "0.008727"},
    }
}

SMW_RESPONSE_EMPTY = {
    "query": {
        "printrequests": [
            {"label": "", "key": "", "redi": "", "typeid": "_wpg", "mode": 2},
        ],
        "results": {},
        "serializer": "SMW\\Serializers\\QueryResultSerializer",
        "version": 2,
        "meta": {"hash": "abc123", "count": 0, "offset": 0, "source": "", "time": "0.001"},
    }
}

SMW_RESPONSE_MISSING_OWNER = {
    "query": {
        "results": {
            "Blue Railroad Token 5": {
                "printouts": {
                    "Token ID": [{"fulltext": "5"}],
                    "Owner Address": [{"fulltext": "0xabc123"}],
                    "Owner": [],  # No owner display name
                },
                "fulltext": "Blue Railroad Token 5",
            },
        },
    }
}

SMW_RESPONSE_MISSING_TOKEN_ID = {
    "query": {
        "results": {
            "Blue Railroad Token X": {
                "printouts": {
                    "Token ID": [],  # Missing token ID
                    "Owner Address": [{"fulltext": "0xdef456"}],
                    "Owner": [{"fulltext": "Someone"}],
                },
                "fulltext": "Blue Railroad Token X",
            },
        },
    }
}


class TestParseSMWTokenResponse:
    """Tests for the SMW response parser."""

    def test_parses_multiple_tokens(self):
        """Parser extracts all tokens from a multi-result response."""
        tokens = parse_smw_token_response(SMW_RESPONSE_TWO_TOKENS)

        assert len(tokens) == 2

        # Find tokens by ID (order not guaranteed)
        token_10 = next(t for t in tokens if t.token_id == '10')
        token_11 = next(t for t in tokens if t.token_id == '11')

        assert token_10.owner_address == '0x4f84b3650Dbf651732a41647618E7fF94A633F09'
        assert token_10.owner_display == 'Justin Myles Holmes'

        assert token_11.owner_address == '0x5da9E9c29365959f1DE138Ba62c19274F7eccF4F'
        assert token_11.owner_display == 'Skyler Golden'

    def test_returns_empty_list_for_no_results(self):
        """Parser returns empty list when no tokens match."""
        tokens = parse_smw_token_response(SMW_RESPONSE_EMPTY)
        assert tokens == []

    def test_falls_back_to_address_when_owner_missing(self):
        """Parser uses owner_address as display name if Owner is empty."""
        tokens = parse_smw_token_response(SMW_RESPONSE_MISSING_OWNER)

        assert len(tokens) == 1
        assert tokens[0].token_id == '5'
        assert tokens[0].owner_address == '0xabc123'
        assert tokens[0].owner_display == '0xabc123'  # Falls back to address

    def test_skips_entries_without_token_id(self):
        """Parser skips results that don't have a token ID."""
        tokens = parse_smw_token_response(SMW_RESPONSE_MISSING_TOKEN_ID)
        assert tokens == []

    def test_handles_malformed_response_gracefully(self):
        """Parser handles missing keys without crashing."""
        # Completely empty response
        tokens = parse_smw_token_response({})
        assert tokens == []

        # Missing query key
        tokens = parse_smw_token_response({"something": "else"})
        assert tokens == []

        # Missing results key
        tokens = parse_smw_token_response({"query": {}})
        assert tokens == []

    def test_handles_none_in_printouts(self):
        """Parser handles None values in printout lists."""
        data = {
            "query": {
                "results": {
                    "Token": {
                        "printouts": {
                            "Token ID": [{"fulltext": "99"}],
                            "Owner Address": None,  # Shouldn't happen but be defensive
                            "Owner": [{"fulltext": "Test"}],
                        },
                    },
                },
            }
        }
        # This should not crash
        tokens = parse_smw_token_response(data)
        assert len(tokens) == 1
        assert tokens[0].owner_address == ''


class TestDryRunClientQueryTokensByCid:
    """Tests for DryRunClient.query_tokens_by_cid."""

    def test_returns_mock_data_when_provided(self):
        """Client returns mock data for matching CIDs."""
        mock_tokens = {
            'QmTestCid': [
                TokenInfo(token_id='10', owner_address='0x123', owner_display='alice'),
            ]
        }
        client = DryRunClient(mock_cid_tokens=mock_tokens)

        result = client.query_tokens_by_cid('QmTestCid')

        assert len(result) == 1
        assert result[0].token_id == '10'

    def test_returns_empty_for_unmatched_cid(self):
        """Client returns empty list for CIDs not in mock data."""
        mock_tokens = {
            'QmOtherCid': [TokenInfo(token_id='5', owner_address='0x999', owner_display='x')]
        }
        client = DryRunClient(mock_cid_tokens=mock_tokens)

        result = client.query_tokens_by_cid('QmNonexistent')

        assert result == []

    def test_returns_empty_for_empty_cid(self):
        """Client returns empty list for empty/None CID."""
        client = DryRunClient()

        assert client.query_tokens_by_cid('') == []
        assert client.query_tokens_by_cid(None) == []

    def test_returns_empty_when_no_mock_and_no_wiki(self):
        """Client returns empty when no mock data and no wiki connection."""
        client = DryRunClient()

        result = client.query_tokens_by_cid('QmSomeCid')

        assert result == []
