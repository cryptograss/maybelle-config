"""HDR footage must be converted to SDR, not merely stripped to 8 bits.

Sky's phone records Dolby Vision. The encoder outputs 8-bit yuv420p, which
is deliberate — 10-bit breaks some decoders — but until this was fixed the
BT.2020 and PQ labels came through untouched. 8-bit PQ is wrong twice: PQ
needs ten bits or gradients band, and a player that believes the labels
renders for a display the viewer does not have.

Nothing published was affected: every release so far was SDR. The first
Dolby Vision upload would have been the one to look flat, with a successful
encode and nothing to complain about.
"""

import asyncio
import shutil
import subprocess

import pytest

from app.services import transcode

needs_encoder = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")
         and "libsvtav1" in subprocess.run(["ffmpeg", "-hide_banner", "-encoders"],
                                           capture_output=True, text=True).stdout),
    reason="needs ffmpeg with libsvtav1",
)


def _colour(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=pix_fmt,color_primaries,color_transfer,color_space",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True).stdout.strip().splitlines()
    return out[0].split(",") if out else []


def _hdr_clip(path):
    """10-bit, BT.2020, PQ — an HDR10 clip, which is what Dolby Vision
    profile 8 carries as its base layer."""
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=1",
         "-pix_fmt", "yuv420p10le", "-c:v", "libsvtav1", "-preset", "12", "-crf", "40",
         "-svtav1-params",
         "color-primaries=9:transfer-characteristics=16:matrix-coefficients=9",
         str(path)], check=True, capture_output=True)
    return path


def _sdr_clip(path):
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=1",
         "-c:v", "libx264", "-preset", "ultrafast", str(path)],
        check=True, capture_output=True)
    return path


class TestDetection:
    def test_pq_is_hdr(self):
        probe = {"streams": [{"codec_type": "video", "color_transfer": "smpte2084"}]}
        assert transcode._hdr_transfer(probe) == "smpte2084"

    def test_hlg_is_hdr(self):
        probe = {"streams": [{"codec_type": "video", "color_transfer": "arib-std-b67"}]}
        assert transcode._hdr_transfer(probe) == "arib-std-b67"

    def test_bt709_is_not(self):
        probe = {"streams": [{"codec_type": "video", "color_transfer": "bt709"}]}
        assert transcode._hdr_transfer(probe) is None

    def test_unlabelled_is_not(self):
        assert transcode._hdr_transfer({"streams": [{"codec_type": "video"}]}) is None
        assert transcode._hdr_transfer(None) is None

    def test_audio_first_files_still_find_the_video(self):
        probe = {"streams": [{"codec_type": "audio", "color_transfer": "smpte2084"},
                             {"codec_type": "video", "color_transfer": "bt709"}]}
        assert transcode._hdr_transfer(probe) is None


@needs_encoder
class TestEndToEnd:
    def test_hdr_is_published_as_sdr_and_labelled_as_sdr(self, tmp_path):
        src = _hdr_clip(tmp_path / "hdr.mp4")
        assert _colour(src)[2] == "smpte2084", "fixture is not HDR"

        out = tmp_path / "out"
        result = asyncio.run(transcode.transcode_video_to_hls(src, out))
        assert result.success, result.error

        rung = sorted(out.glob("stream_*p/playlist.m3u8"))[0]
        pix, primaries, transfer, space = _colour(rung)
        assert pix == "yuv420p"
        # The labels must follow the pixels. Leaving PQ on 8-bit output is
        # the bug this test exists for.
        assert transfer == "bt709", f"still tagged {transfer}"
        assert primaries == "bt709" and space == "bt709"

        assert result.transcode_info["tone_mapped"] is True
        assert result.transcode_info["source_transfer"] == "smpte2084"

    def test_sdr_is_left_alone(self, tmp_path):
        src = _sdr_clip(tmp_path / "sdr.mp4")
        out = tmp_path / "out"
        result = asyncio.run(transcode.transcode_video_to_hls(src, out))
        assert result.success, result.error
        assert result.transcode_info["tone_mapped"] is False
        assert result.transcode_info["source_transfer"] == "sdr"

    def test_the_poster_is_tone_mapped_too(self, tmp_path):
        """The poster is pulled from the source, not from the encode, so it
        needs the same treatment — otherwise the still a reader sees before
        pressing play is the wrong one.

        It turns out to be worse than wrong: without tone mapping the JPEG
        encoder refuses 10-bit HDR frames outright, so an HDR upload got no
        poster at all. Tone mapping fixes that as a side effect.
        """
        src = _hdr_clip(tmp_path / "hdr.mp4")
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        plain = asyncio.run(transcode._write_poster(src, tmp_path / "a", 1.0, None,
                                                    hdr=False))
        mapped = asyncio.run(transcode._write_poster(src, tmp_path / "b", 1.0, None,
                                                     hdr=True))
        assert mapped, "HDR source produced no poster even with tone mapping"

        if plain:
            def luma(directory, name):
                raw = subprocess.run(
                    ["ffmpeg", "-v", "error", "-i", str(directory / name),
                     "-vf", "format=gray,scale=32:18", "-f", "rawvideo", "-"],
                    capture_output=True).stdout
                return sum(raw) / len(raw)
            assert luma(tmp_path / "a", plain) != luma(tmp_path / "b", mapped)
