"""Where the bot finds a finalized draft's CID: its own YAML first, the edit summary as the fallback."""

from unittest.mock import patch

from blue_railroad_import import release_draft as rd

CID = 'QmT5VW5kJTdbcSYk4wZfb3ep8UbmjP4QGL3aCJAxNFZS6u'
TITLE = 'ReleaseDraft:21408374-b2cc-4bd5-83de-9822f3488bbc'


class FakeWiki:
    api_url = 'https://wiki.example/api.php'

    def __init__(self):
        self.saved = {}

    def page_exists(self, title):
        return title in self.saved

    def save_page(self, title, text, summary):
        self.saved[title] = text
        return rd.SaveResult(title, 'created', summary)


def run(data, history_cid=None):
    wiki = FakeWiki()
    with patch.object(rd, 'fetch_release_drafts', return_value=[{'title': TITLE, 'data': data}]), \
            patch.object(rd, 'find_cid_from_history', return_value=history_cid), \
            patch.object(rd, 'build_release_from_draft', return_value='title: x\n'):
        rd.process_release_drafts(wiki)
    return wiki.saved


def test_final_cid_in_the_yaml_makes_the_release_with_no_summary():
    """21 Nov: final_cid put on the draft by hand, with an empty edit summary."""
    data = {'type': 'video', 'status': 'finalized', 'final_cid': CID}
    assert f'Release:{CID}' in run(data, history_cid=None)


def test_the_summary_still_works_alone():
    assert f'Release:{CID}' in run({'type': 'video'}, history_cid=CID)


def test_a_draft_not_finalized_makes_nothing():
    assert run({'type': 'video', 'final_cid': CID}, history_cid=None) == {}
    assert run({'type': 'video', 'status': 'finalized', 'final_cid': '  '}, history_cid=None) == {}
