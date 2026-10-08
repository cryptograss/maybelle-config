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

# The real thing, fetched from the gateway: one of the eleven orphan pins,
# a directory holding nothing but this 485-byte file.
REAL_REMNANT = {
    "title": "learning terrapin outside Denver venue",
    "uploaded_by": "wiki:JMyles",
    "created_at": "2026-06-01T17:45:22.570717+00:00",
    "transcode": {
        "method": "coconut",
        "output_codec": "av1",
        "output_audio_codec": "opus",
        "qualities": [],
        "variants": {},
        "total_output_size_bytes": 0,
        "coconut_settings": {"video_codec": "av1", "audio_codec": "opus",
                             "audio_bitrate": "128k",
                             "hls_segment_duration": 6},
    },
}


class TestCoconutRemnants:
    """Five kilobytes of known-dead paperwork is not an action item.

    Keeping it in the same warning box as a real fault is how the nine
    false "unrecorded publishes" sat unexamined for weeks.
    """

    def test_the_real_remnant_is_recognised(self):
        assert audit.is_coconut_remnant(REAL_REMNANT)

    def test_an_untitled_remnant_is_still_one(self):
        meta = {k: v for k, v in REAL_REMNANT.items() if k != "title"}
        assert audit.is_coconut_remnant(meta)

    def test_a_local_ffmpeg_pin_is_not_a_remnant(self):
        assert not audit.is_coconut_remnant(
            {"transcode": {"method": "local-ffmpeg",
                           "total_output_size_bytes": 0,
                           "qualities": [], "variants": {}}})

    def test_a_coconut_run_that_produced_media_is_a_real_finding(self):
        """The point of the filter is "no media". A Coconut pin holding
        actual output is an orphan worth someone's attention."""
        meta = {"transcode": {"method": "coconut",
                              "total_output_size_bytes": 20809992,
                              "qualities": ["1080p"], "variants": {"1080p": {}}}}
        assert not audit.is_coconut_remnant(meta)

    def test_zero_bytes_but_listed_qualities_is_not_dismissed(self):
        meta = {"transcode": {"method": "coconut",
                              "total_output_size_bytes": 0,
                              "qualities": ["1080p"], "variants": {}}}
        assert not audit.is_coconut_remnant(meta)

    def test_a_pin_with_no_metadata_at_all_is_not_dismissed(self):
        # Unreadable is not the same as known-dead. These stay in the list.
        assert not audit.is_coconut_remnant(None)
        assert not audit.is_coconut_remnant({})
        assert not audit.is_coconut_remnant({"transcode": None})

    def test_the_split_keeps_real_orphans_in_the_list(self):
        metadata = {
            "QmCoconut": REAL_REMNANT,
            "QmReal": {"transcode": {"method": "local-ffmpeg",
                                     "total_output_size_bytes": 999,
                                     "qualities": ["1080p"], "variants": {}}},
            "QmUnknown": None,
        }
        remaining, remnants = audit.split_coconut_remnants(
            ["QmCoconut", "QmReal", "QmUnknown"], metadata)
        assert remaining == ["QmReal", "QmUnknown"]
        assert [r["cid"] for r in remnants] == ["QmCoconut"]
        assert remnants[0]["title"] == "learning terrapin outside Denver venue"
        assert remnants[0]["uploaded_by"] == "wiki:JMyles"

    def test_remnants_are_reported_with_who_and_when(self, capsys):
        audit.print_coconut_remnants(
            [{"cid": "QmA", "title": "learning terrapin outside Denver venue",
              "uploaded_by": "wiki:JMyles",
              "created_at": "2026-06-01T17:45:22.570717+00:00"},
             {"cid": "QmB", "title": None, "uploaded_by": "wiki:SkymanJenkins",
              "created_at": "2026-09-17T13:15:33.854069+00:00"}],
            sizes={"QmA": 485, "QmB": 485})
        out = capsys.readouterr().out
        assert "COCONUT REMNANTS (2" in out
        assert "nothing to " in out and "reclaim" in out
        assert "2026-06-01" in out and "JMyles" in out
        assert "(no title recorded)" in out
        # Oldest first, so the list reads as a history.
        assert out.index("2026-06-01") < out.index("2026-09-17")

    def test_nothing_printed_when_there_are_none(self, capsys):
        audit.print_coconut_remnants([], sizes={})
        assert capsys.readouterr().out == ""

    def test_metadata_is_not_refetched_for_the_correlation(self, monkeypatch):
        """One ssh per orphan, not two. The correlation reuses what the
        split already read."""
        def no_fetch(cid):
            raise AssertionError("metadata was read twice for the same pin")

        monkeypatch.setattr(audit, "fetch_pin_metadata", no_fetch)
        matches = audit.correlate_unrecorded_publishes(
            ["QmA"], ["68bf6490-1629-4863-97a7-d3bf3ce34e35"],
            metadata={"QmA": REAL_REMNANT})
        assert isinstance(matches, list)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


class TestKeptOriginals:
    """The biggest things delivery-kid keeps on purpose, and the only ones
    the audit never looked at."""

    def test_the_listing_parses_and_skips_noise(self):
        out = "a9c19c7d-cca8 2048000 1\nbroken line\nace258b9 500 2\nbad x y\n"
        assert audit.parse_originals(out) == [
            {"id": "a9c19c7d-cca8", "size_kb": 2048000, "files": 1},
            {"id": "ace258b9", "size_kb": 500, "files": 2},
        ]
        assert audit.parse_originals("") == []

    def test_an_original_with_no_draft_page_is_set_apart(self):
        kept, orphaned = audit.split_originals(
            [{"id": "A9C19C7D", "size_kb": 1, "files": 1},
             {"id": "gone", "size_kb": 1, "files": 1}],
            ["a9c19c7d"])
        assert [o["id"] for o in kept] == ["A9C19C7D"]
        assert [o["id"] for o in orphaned] == ["gone"]

    def test_they_are_listed_largest_first_with_a_total(self, capsys):
        small = {"id": "small", "size_kb": 10, "files": 1}
        big = {"id": "big", "size_kb": 3 * 1024 * 1024, "files": 2}
        audit.print_originals([small], [big])
        out = capsys.readouterr().out
        assert "KEPT ORIGINALS (2," in out
        assert out.index("big") < out.index("small")
        assert "no ReleaseDraft page" in out.split("big", 1)[1].split("\n", 1)[0]
        assert "no ReleaseDraft page" not in out.split("small", 1)[1].split("\n", 1)[0]

    def test_nothing_printed_when_nothing_is_kept(self, capsys):
        audit.print_originals([], [])
        assert capsys.readouterr().out == ""

    def test_only_orphans_raise_the_warning_box(self):
        # Keeping an original is deliberate. One nobody can trace is not.
        assert "Orphan originals" in poster.PROBLEM_LABELS
        assert "Kept originals" not in poster.PROBLEM_LABELS
