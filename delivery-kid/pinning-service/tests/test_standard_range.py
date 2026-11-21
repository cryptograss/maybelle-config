"""Every rendition comes out in standard (16-235) range, whatever the source.

Phones often record full range: Justin's Pixel writes yuvj420p H.264. The
encoder carried that range straight into the AV1, and on Skyler's iPhone 17
and slvrbckt's iPhone the full-range AV1 played its sound over a black
picture, in Safari's own HLS player and in hls.js alike (#interfaces and
#general, 8 Nov 2026). Standard range is what players expect.
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


def _range(path):
    return subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=color_range", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True).stdout.strip().splitlines()[0]


def _clip(path, pix_fmt, extra=()):
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=1",
         "-pix_fmt", pix_fmt, *extra, "-c:v", "libx264", "-preset", "ultrafast", str(path)],
        check=True, capture_output=True)
    return path


@needs_encoder
class TestStandardRange:
    def test_a_full_range_phone_clip_is_published_in_standard_range(self, tmp_path):
        src = _clip(tmp_path / "pixel.mp4", "yuvj420p", ("-color_range", "pc"))
        assert _range(src) == "pc", "fixture is not full range"

        out = tmp_path / "out"
        result = asyncio.run(transcode.transcode_video_to_hls(src, out))
        assert result.success, result.error

        rungs = sorted(out.glob("stream_*p/playlist.m3u8"))
        assert rungs
        for rung in rungs:
            assert _range(rung) == "tv", f"{rung.parent.name} is still full range"

    def test_a_standard_range_clip_stays_standard(self, tmp_path):
        src = _clip(tmp_path / "plain.mp4", "yuv420p", ("-color_range", "tv"))
        out = tmp_path / "out"
        result = asyncio.run(transcode.transcode_video_to_hls(src, out))
        assert result.success, result.error
        for rung in sorted(out.glob("stream_*p/playlist.m3u8")):
            assert _range(rung) == "tv"
