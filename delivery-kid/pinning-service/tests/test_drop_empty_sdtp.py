"""An empty sdtp box in an init segment stops iPhone Safari playing the stream.

ffmpeg 7.1 writes one into every video rendition's init segment. Skyler's
iPhone 17 played a release's exact stream once only those 12 bytes were cut
(8-9 Nov 2026), so transcode removes them.
"""
from pathlib import Path

from app.services.transcode import _drop_empty_sdtp


def box(kind: bytes, payload: bytes = b"") -> bytes:
    return (8 + len(payload)).to_bytes(4, "big") + kind + payload


EMPTY_SDTP = box(b"sdtp", b"\x00\x00\x00\x00")  # version, flags; no entries


def init_segment(video_sdtp: bytes, audio_sdtp: bytes = b"") -> bytes:
    def trak(handler: bytes, sdtp: bytes) -> bytes:
        stbl = box(b"stbl", box(b"stsd", b"\x00" * 8) + box(b"stts", b"\x00" * 8) + sdtp
                   + box(b"stsc", b"\x00" * 8))
        return box(b"trak", box(b"tkhd", b"\x00" * 84)
                   + box(b"mdia", box(b"hdlr", b"\x00" * 8 + handler + b"\x00" * 13)
                         + box(b"minf", box(b"vmhd", b"\x00" * 12) + stbl)))
    moov = box(b"moov", box(b"mvhd", b"\x00" * 100) + trak(b"vide", video_sdtp)
               + trak(b"soun", audio_sdtp) + box(b"mvex", box(b"trex", b"\x00" * 24)))
    return box(b"ftyp", b"iso5\x00\x00\x02\x00iso5iso6av01mp41") + moov


def sizes_add_up(data: bytes, start: int = 0, end: int | None = None) -> bool:
    end = len(data) if end is None else end
    offset = start
    while offset < end:
        size = int.from_bytes(data[offset:offset + 4], "big")
        kind = data[offset + 4:offset + 8]
        if size < 8 or offset + size > end:
            return False
        if kind in (b"moov", b"trak", b"mdia", b"minf", b"stbl"):
            if not sizes_add_up(data, offset + 8, offset + size):
                return False
        offset += size
    return offset == end


def test_removes_the_empty_sdtp_and_fixes_every_parent(tmp_path: Path):
    path = tmp_path / "init_2.mp4"
    path.write_bytes(init_segment(EMPTY_SDTP))

    assert _drop_empty_sdtp(path) is True

    after = path.read_bytes()
    assert after == init_segment(b"")
    assert b"sdtp" not in after
    assert sizes_add_up(after)


def test_removes_one_from_each_track(tmp_path: Path):
    path = tmp_path / "init_0.mp4"
    path.write_bytes(init_segment(EMPTY_SDTP, EMPTY_SDTP))

    assert _drop_empty_sdtp(path) is True
    assert path.read_bytes() == init_segment(b"", b"")


def test_leaves_a_file_without_sdtp_untouched(tmp_path: Path):
    path = tmp_path / "init_3.mp4"
    original = init_segment(b"")
    path.write_bytes(original)

    assert _drop_empty_sdtp(path) is False
    assert path.read_bytes() == original


def test_leaves_an_sdtp_with_entries_alone(tmp_path: Path):
    # Only an empty one is known to break playback; one describing samples is
    # left as it is.
    path = tmp_path / "init_1.mp4"
    original = init_segment(box(b"sdtp", b"\x00\x00\x00\x00" + b"\x20\x10"))
    path.write_bytes(original)

    assert _drop_empty_sdtp(path) is False
    assert path.read_bytes() == original


def test_leaves_a_malformed_file_alone(tmp_path: Path):
    path = tmp_path / "init_9.mp4"
    original = b"\x00\x00\x00\x04moov" + EMPTY_SDTP
    path.write_bytes(original)

    assert _drop_empty_sdtp(path) is False
    assert path.read_bytes() == original
