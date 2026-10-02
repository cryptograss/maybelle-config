"""Surround audio must not be squeezed into a stereo-sized bitrate.

Nothing had ever tested audio that wasn't one or two channels. A 5.1 take
published fine — and got 128k for six channels, about 21k each, which
sounds like it. A successful encode that quietly sounds bad is the same
shape of bug as the HDR one: nothing to complain about, nothing to see.

Mono and stereo must be byte-for-byte unaffected. Almost every upload is
one of those, and this change is not an excuse to re-tune them.
"""

import asyncio
import json
import shutil
import subprocess

import pytest

from app.services import transcode

needs_encoder = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="needs ffmpeg",
)


def _layout(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
         "stream=channels,codec_name", "-of", "json", str(path)],
        capture_output=True, text=True).stdout
    streams = json.loads(out).get("streams") or [{}]
    return streams[0].get("codec_name"), streams[0].get("channels")


def _surround_clip(path, channels="5.1"):
    pan = "|".join(f"c{i}=c0" for i in range(6))
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=1",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         "-af", f"pan={channels}|{pan}",
         "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(path)],
        check=True, capture_output=True)
    return path


class TestTheRule:
    def test_stereo_is_left_exactly_alone(self):
        assert transcode._audio_bitrate("128k", 2) == "128k"
        assert transcode._audio_bitrate("96k", 2) == "96k"

    def test_mono_is_left_exactly_alone(self):
        # Tempting to halve it. Not this change's business.
        assert transcode._audio_bitrate("128k", 1) == "128k"

    def test_unknown_channel_count_changes_nothing(self):
        assert transcode._audio_bitrate("128k", None) == "128k"
        assert transcode._audio_bitrate("128k", 0) == "128k"

    def test_surround_gets_more(self):
        # 6 × 48 = 288, over the ceiling, so 256.
        assert transcode._audio_bitrate("128k", 6) == "256k"

    def test_three_channels_scale_rather_than_jumping_to_the_ceiling(self):
        assert transcode._audio_bitrate("128k", 3) == "144k"

    def test_it_never_lowers_what_the_rung_asked_for(self):
        # The audio-only rendition is already 160k; three channels want 144k.
        assert transcode._audio_bitrate("160k", 3) == "160k"

    def test_absurd_channel_counts_stay_capped(self):
        assert transcode._audio_bitrate("128k", 24) == "256k"


@needs_encoder
class TestEndToEnd:
    def test_surround_survives_and_is_recorded(self, tmp_path):
        src = _surround_clip(tmp_path / "surround.mp4")
        assert _layout(src)[1] == 6, "fixture is not 5.1"

        result = asyncio.run(transcode.transcode_video_to_hls(src, tmp_path / "out"))
        assert result.success, result.error

        track = result.transcode_info["audio_tracks"][0]
        assert track["channels"] == 6
        assert track["bitrate"] == "256k", "5.1 was published at a stereo rate"

        # All six channels still there — the fix is more bits, not a downmix.
        # Discarding channels would be the same mistake as publishing only
        # the first audio track.
        rung = sorted((tmp_path / "out").glob("stream_*p/playlist.m3u8"))[0]
        codec, channels = _layout(rung)
        assert codec == "opus"
        assert channels == 6

    def test_stereo_output_is_unchanged_by_all_this(self, tmp_path):
        src = tmp_path / "stereo.mp4"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y",
             "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=1",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
             "-ac", "2", "-c:v", "libx264", "-preset", "ultrafast",
             "-c:a", "aac", str(src)],
            check=True, capture_output=True)

        result = asyncio.run(transcode.transcode_video_to_hls(src, tmp_path / "out"))
        assert result.success, result.error
        track = result.transcode_info["audio_tracks"][0]
        assert track["channels"] == 2
        assert track["bitrate"] == transcode.VIDEO_LADDER[-1]["audio"]
