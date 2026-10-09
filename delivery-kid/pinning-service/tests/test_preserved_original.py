"""A kept original must be recorded with the release that kept it.

"Keep original file" copies the upload aside instead of deleting it. Until
this, nothing said it had: not the release, not the audit, so the only way
to learn what was using the space was to go onto the server and look.
"""

from app.routes.content import preserved_original_record


def test_nothing_kept_means_nothing_recorded(tmp_path):
    assert preserved_original_record(tmp_path, "d1", ["missing.mov"]) is None
    assert preserved_original_record(tmp_path, "d1", []) is None


def test_the_record_says_where_what_and_how_big(tmp_path):
    (tmp_path / "IMG_8225.MOV").write_bytes(b"x" * 1000)
    (tmp_path / "notes.txt").write_bytes(b"y" * 24)
    record = preserved_original_record(tmp_path, "a9c19c7d", ["IMG_8225.MOV", "notes.txt"])
    assert record == {
        "location": "staging/originals/a9c19c7d",
        "files": ["IMG_8225.MOV", "notes.txt"],
        "size_bytes": 1024,
    }


def test_a_file_that_failed_to_copy_is_not_claimed(tmp_path):
    # The copy loop skips sources that vanished; the record must not claim them.
    (tmp_path / "kept.mov").write_bytes(b"z" * 10)
    record = preserved_original_record(tmp_path, "d", ["kept.mov", "lost.mov"])
    assert record["files"] == ["kept.mov"]
    assert record["size_bytes"] == 10
