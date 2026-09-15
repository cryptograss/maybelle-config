"""The quality ladder must size renditions by the short side of the picture.

Found on Release:QmdgxqVbPpLu1Rp8Sn46BqQzRbbpnwrYG3X2FTFfUVGDvQ, an upright
iPhone clip (1920x1080 coded, rotated -90) whose "1080p" rendition came out
608x1080, and whose Release page reported the 360p size and the audio
stream's byte count as the release's.
"""

import asyncio
import json
import shutil
import subprocess

import pytest

from app.services import transcode
from app.services.transcode import _display_size, _ladder_for, _scale_filter


def _probe(width, height, rotation=None, tag=None):
    stream = {"codec_type": "video", "width": width, "height": height}
    if rotation is not None:
        stream["side_data_list"] = [{"side_data_type": "Display Matrix", "rotation": rotation}]
    if tag is not None:
        stream["tags"] = {"rotate": str(tag)}
    return {"streams": [{"codec_type": "audio"}, stream]}


class TestDisplaySize:
    def test_landscape_unrotated(self):
        assert _display_size(_probe(1920, 1080)) == (1920, 1080)

    def test_iphone_portrait_display_matrix(self):
        assert _display_size(_probe(1920, 1080, rotation=-90)) == (1080, 1920)

    def test_older_rotate_tag(self):
        assert _display_size(_probe(1920, 1080, tag=90)) == (1080, 1920)

    def test_upside_down_keeps_orientation(self):
        assert _display_size(_probe(1920, 1080, rotation=180)) == (1920, 1080)

    def test_natively_portrait_frames(self):
        assert _display_size(_probe(1080, 1920)) == (1080, 1920)

    def test_unknown(self):
        assert _display_size(None) is None
        assert _display_size({"streams": [{"codec_type": "audio"}]}) is None


class TestLadder:
    def test_portrait_1080_gets_every_rung(self):
        assert [r["name"] for r in _ladder_for(1080)] == ["1080p", "720p", "360p"]

    def test_small_source_is_not_upscaled(self):
        assert [r["name"] for r in _ladder_for(720)] == ["720p", "360p"]

    def test_scale_filter_follows_orientation(self):
        assert _scale_filter(1080, portrait=True) == "scale=1080:-2"
        assert _scale_filter(1080, portrait=False) == "scale=-2:1080"


needs_encoder = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")
         and "libsvtav1" in subprocess.run(["ffmpeg", "-hide_banner", "-encoders"],
                                           capture_output=True, text=True).stdout),
    reason="needs ffmpeg with libsvtav1",
)


def _stream_size(path):
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams",
         "-select_streams", "v:0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    s = json.loads(out)["streams"][0]
    return s["width"], s["height"]


@needs_encoder
def test_rotated_phone_clip_end_to_end(tmp_path):
    # A landscape-coded clip carrying a -90 display rotation, the way an iPhone
    # records upright video.
    coded = tmp_path / "coded.mp4"
    source = tmp_path / "IMG_0001.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30:duration=1",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         "-c:v", "libsvtav1", "-preset", "12", "-c:a", "libopus", "-shortest", str(coded)],
        check=True,
    )
    subprocess.run(
        ["ffmpeg", "-v", "error", "-display_rotation", "90", "-i", str(coded),
         "-c", "copy", str(source)],
        check=True,
    )
    assert _display_size(asyncio.run(transcode.probe_video(source))) == (1080, 1920)

    out = tmp_path / "hls"
    result = asyncio.run(transcode.transcode_video_to_hls(source, out))
    assert result.success, result.error

    assert _stream_size(out / "stream_1080p" / "playlist.m3u8") == (1080, 1920)
    assert _stream_size(out / "stream_720p" / "playlist.m3u8") == (720, 1280)
    assert _stream_size(out / "stream_360p" / "playlist.m3u8") == (360, 640)

    info = result.transcode_info
    assert (info["output_width"], info["output_height"]) == (1080, 1920)
    # A one-second clip once got no poster: the seek landed on the last frame.
    assert info["poster"] == "poster.jpg"
    assert _stream_size(out / "poster.jpg") == (1080, 1920)
    on_disk = sum(p.stat().st_size for p in out.glob("stream_*/seg_*.m4s"))
    assert info["total_output_size_bytes"] == on_disk
    # The audio-only variant alone must not account for the whole release.
    audio_only = sum(p.stat().st_size for p in out.glob("stream_audio/seg_*.m4s"))
    assert info["total_output_size_bytes"] > audio_only
