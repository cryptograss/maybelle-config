"""The audit must not recommend deleting the record of a successful upload.

Two bugs, found by reading an audit that had been posted to the wiki for
weeks:

1. ``allpages(3006)`` returns a draft's subpages alongside the drafts.
   ``ReleaseDraft:<id>/diagnostics`` has no staging directory and was never
   finalized on its own, so every successful publish produced a
   "DEAD WIKI DRAFT ... safe to delete from wiki" line pointing at the only
   surviving account of that upload — and the same nine pages were
   double-counted as "unrecorded publishes" in the warning box.

2. The audit prints some references in link syntax itself, and the wiki
   poster then wrapped them again, putting
   ``[[ReleaseDraft:[[ReleaseDraft:<uuid>|<uuid>]]]]`` on the page.

These scripts are named with hyphens and are run, not imported, so they are
loaded here by path.
"""

import importlib.util
import sys
import urllib.error
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent


def _load(filename, module_name):
    spec = importlib.util.spec_from_file_location(module_name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


audit = _load("audit-storage.py", "audit_storage")
poster = _load("post-audit-to-wiki.py", "post_audit_to_wiki")


class TestSubpagesAreNotDrafts:
    """The filter itself, applied the way main() applies it."""

    @staticmethod
    def _split(pages):
        return ([p for p in pages if "/" not in p],
                [p for p in pages if "/" in p])

    def test_diagnostics_pages_are_not_drafts(self):
        pages = [
            "a9c19c7d-cca8-4695-aa65-21d9eae947fe",
            "a9c19c7d-cca8-4695-aa65-21d9eae947fe/diagnostics",
            "ace258b9-c2a5-4ac9-8a9f-552191e9f26f",
            "ace258b9-c2a5-4ac9-8a9f-552191e9f26f/diagnostics",
        ]
        drafts, subpages = self._split(pages)
        assert drafts == [
            "a9c19c7d-cca8-4695-aa65-21d9eae947fe",
            "ace258b9-c2a5-4ac9-8a9f-552191e9f26f",
        ]
        assert len(subpages) == 2

    def test_a_draft_with_no_subpage_is_untouched(self):
        drafts, subpages = self._split(["112de224-050d-4374-988f-4280424e2293"])
        assert drafts == ["112de224-050d-4374-988f-4280424e2293"]
        assert subpages == []

    def test_the_published_melodica_draft_is_not_called_dead(self, monkeypatch):
        """The case that prompted this: a draft that published on 28
        September, whose diagnostics page the audit called safe to delete."""
        published = "a9c19c7d-cca8-4695-aa65-21d9eae947fe"
        monkeypatch.setattr(audit, "page_comments",
                            lambda title: [
                                "Finalized: pinned to IPFS as QmU2rS8zrVcGPL"
                                "2tz7jhKE6KdNj5mAy7FMFpY9iX9x54ms"])
        drafts, _ = self._split([published, f"{published}/diagnostics"])
        # Staging is cleaned on a successful finalize, so the draft itself
        # legitimately has none. Its subpage must not appear as a second,
        # never-finalized draft on top of that.
        result = audit.audit_drafts(drafts, staging_drafts=[], abandoned={})
        assert result["dead_wiki_drafts"] == []
        assert result["finalized_gone"] == [published]


class TestAFailedLookupIsNotAVerdict:
    """"Could not check" must never render as "safe to delete".

    The classification decides whether the report recommends deleting a
    page. The lookup used to be wrapped in a bare except that fell through
    to False, so one wiki hiccup could recommend deleting the only record
    of a published release.
    """

    def test_the_pin_record_is_matched_whatever_its_casing(self, monkeypatch):
        monkeypatch.setattr(audit, "page_comments",
                            lambda title: ["FINALIZED: Pinned To IPFS as Qm..."])
        result = audit.audit_drafts(["a9c19c7d-cca8-4695-aa65-21d9eae947fe"],
                                    staging_drafts=[], abandoned={})
        assert result["dead_wiki_drafts"] == []

    def test_a_draft_with_no_pin_record_is_dead(self, monkeypatch):
        monkeypatch.setattr(audit, "page_comments", lambda title: ["Draft saved"])
        result = audit.audit_drafts(["68bf6490-1629-4863-97a7-d3bf3ce34e35"],
                                    staging_drafts=[], abandoned={})
        assert result["dead_wiki_drafts"] == ["68bf6490-1629-4863-97a7-d3bf3ce34e35"]
        assert result["unknown_drafts"] == []

    def test_an_unreadable_page_is_undetermined_not_dead(self, monkeypatch):
        def boom(title):
            raise urllib.error.URLError("connection reset")

        monkeypatch.setattr(audit, "page_comments", boom)
        result = audit.audit_drafts(["68bf6490-1629-4863-97a7-d3bf3ce34e35"],
                                    staging_drafts=[], abandoned={})
        assert result["dead_wiki_drafts"] == [], "a failed read recommended deletion"
        assert result["unknown_drafts"] == ["68bf6490-1629-4863-97a7-d3bf3ce34e35"]

    def test_the_undetermined_bucket_is_printed(self, monkeypatch, capsys):
        """A bucket nobody prints is a bucket nobody acts on."""
        audit.print_draft_audit(
            {"orphan_drafts": [], "stalled_drafts": [], "dead_wiki_drafts": [],
             "finalized_gone": [], "abandoned_drafts": [],
             "unknown_drafts": ["68bf6490-1629-4863-97a7-d3bf3ce34e35"]},
            wiki_count=1, staging_count=0)
        out = capsys.readouterr().out
        assert "UNDETERMINED" in out
        assert "68bf6490-1629-4863-97a7-d3bf3ce34e35" in out
        assert "Do not delete" in out


class TestLinkifyLeavesExistingLinksAlone:
    def test_an_already_linked_draft_is_not_wrapped_twice(self):
        text = "  [[ReleaseDraft:a9c19c7d-cca8-4695-aa65-21d9eae947fe]] — unmatched"
        out = poster.linkify_audit(text)
        assert "[[ReleaseDraft:[[" not in out
        assert out == text

    def test_a_bare_uuid_still_gets_linked(self):
        out = poster.linkify_audit("  a9c19c7d-cca8-4695-aa65-21d9eae947fe (5K, 25d old)")
        assert "[[ReleaseDraft:a9c19c7d-cca8-4695-aa65-21d9eae947fe|" in out

    def test_a_bare_cid_still_gets_linked(self):
        out = poster.linkify_audit("  QmU2rS8zrVcGPL2tz7jhKE6KdNj5mAy7FMFpY9iX9x54ms")
        assert "[[Release:QmU2rS8zrVcGPL2tz7jhKE6KdNj5mAy7FMFpY9iX9x54ms|" in out

    def test_mixed_lines_are_handled_in_one_pass(self):
        text = ("ORPHAN PINS (1):\n"
                "  QmU2rS8zrVcGPL2tz7jhKE6KdNj5mAy7FMFpY9iX9x54ms\n"
                "  [[ReleaseDraft:a9c19c7d-cca8-4695-aa65-21d9eae947fe]] — already linked\n")
        out = poster.linkify_audit(text)
        assert out.count("[[Release:Qm") == 1
        assert "[[ReleaseDraft:[[" not in out

    def test_no_placeholder_leaks_into_the_page(self):
        text = "[[Release:QmU2rS8zrVcGPL2tz7jhKE6KdNj5mAy7FMFpY9iX9x54ms|label]]"
        out = poster.linkify_audit(text)
        assert "\x00" not in out
        assert out == text

class TestPinSizes:
    """Eleven addresses with no sizes is not a finding, it is a shrug.

    These cannot be exercised against the live node from here — production
    is no-touch and `dag stat` runs on delivery-kid — so what is tested is
    the parsing and the reporting, which is where the mistakes would be.
    """

    def test_json_totalsize(self):
        assert audit.parse_dag_size('{"TotalSize":20809992,"NumBlocks":84}') == 20809992

    def test_json_size_only(self):
        # Older kubo used Size for the same number.
        assert audit.parse_dag_size('{"Size":1234,"NumBlocks":2}') == 1234

    def test_plain_text_line(self):
        assert audit.parse_dag_size("Size: 20809992, NumBlocks: 84") == 20809992

    def test_plain_text_with_thousands_separators(self):
        assert audit.parse_dag_size("Size: 20,809,992") == 20809992

    def test_cumulative_size_from_files_stat(self):
        assert audit.parse_dag_size('{"CumulativeSize": 4096}') == 4096

    def test_nothing_is_none_not_zero(self):
        # Zero would total up as though the pin were empty; unknown must
        # stay unknown or the summary lies.
        assert audit.parse_dag_size("") is None
        assert audit.parse_dag_size(None) is None
        assert audit.parse_dag_size("Error: merkledag: not found") is None

    def test_sizes_are_reported_largest_first_with_a_total(self, capsys):
        audit.print_pin_audit(
            {"orphan_pins": ["QmSmall", "QmBig", "QmUnknown"],
             "orphan_pin_sizes": {"QmSmall": 5 * 1024 * 1024,
                                  "QmBig": 900 * 1024 * 1024,
                                  "QmUnknown": None},
             "deleted": [], "retired": [], "missing_pins": [],
             "deliberately_unpinned": [], "cleanup_pending": []},
            release_count=66)
        out = capsys.readouterr().out
        assert "905M total" in out, out
        big = out.index("QmBig")
        small = out.index("QmSmall")
        assert big < small, "largest orphan should be listed first"
        assert "size unknown" in out

    def test_an_unmeasurable_list_still_prints(self, capsys):
        """If every lookup fails the report must degrade, not crash."""
        audit.print_pin_audit(
            {"orphan_pins": ["QmA"], "orphan_pin_sizes": {"QmA": None},
             "deleted": [], "retired": [], "missing_pins": [],
             "deliberately_unpinned": [], "cleanup_pending": []},
            release_count=1)
        out = capsys.readouterr().out
        assert "ORPHAN PINS (1)" in out
        assert "total" not in out.split("ORPHAN PINS")[1].split("\n")[0]

    def test_no_orphans_means_no_ssh(self, monkeypatch):
        def no_ssh(*args, **kwargs):
            raise AssertionError("should not reach for the node with nothing to ask")

        monkeypatch.setattr(audit, "ssh", no_ssh)
        assert audit.fetch_pin_sizes([]) == {}

    def test_one_ssh_for_the_whole_batch(self, monkeypatch):
        calls = []

        def fake_ssh(host, command):
            calls.append(command)
            return ("QmA\t{\"TotalSize\":10}\n"
                    "QmB\t{\"TotalSize\":20}\n")

        monkeypatch.setattr(audit, "ssh", fake_ssh)
        sizes = audit.fetch_pin_sizes(["QmA", "QmB"])
        assert sizes == {"QmA": 10, "QmB": 20}
        assert len(calls) == 1, "one round trip, not one per pin"

    def test_a_pin_the_node_says_nothing_about_stays_unknown(self, monkeypatch):
        monkeypatch.setattr(audit, "ssh",
                            lambda host, cmd: "QmA\t{\"TotalSize\":10}\n")
        sizes = audit.fetch_pin_sizes(["QmA", "QmMissing"])
        assert sizes["QmA"] == 10
        assert sizes["QmMissing"] is None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
