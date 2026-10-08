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


def _surround_clip(path, channels="5.1", size="320x180"):
    pan = "|".join(f"c{i}=c0" for i in range(6))
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=30:duration=1",
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


class TestWhereSurroundGoes:
    def test_low_rungs_downmix_surround(self):
        assert transcode._downmix_for({"height": 360}, 6)

    def test_high_rungs_keep_it(self):
        assert not transcode._downmix_for({"height": 720}, 6)
        assert not transcode._downmix_for({"height": 1080}, 6)

    def test_stereo_and_mono_are_never_downmixed(self):
        assert not transcode._downmix_for({"height": 360}, 2)
        assert not transcode._downmix_for({"height": 360}, 1)
        assert not transcode._downmix_for({"height": 360}, None)


@needs_encoder
class TestEndToEnd:
    def test_surround_survives_and_is_recorded(self, tmp_path):
        src = _surround_clip(tmp_path / "surround.mp4")
        assert _layout(src)[1] == 6, "fixture is not 5.1"

        result = asyncio.run(transcode.transcode_video_to_hls(src, tmp_path / "out"))
        assert result.success, result.error

        track = result.transcode_info["audio_tracks"][0]
        assert track["channels"] == 6

        # This fixture is 320x180, so its only video rung is 360p — which
        # carries stereo now. The surround survives in the audio-only
        # rendition, at a surround bitrate rather than a stereo one.
        out = tmp_path / "out"
        assert _layout(out / "stream_audio" / "playlist.m3u8") == ("opus", 6)
        assert _layout(out / "stream_360p" / "playlist.m3u8") == ("opus", 2)
        assert result.transcode_info["stereo_rungs"] == ["360p"]

    def test_surround_is_kept_on_the_rungs_that_can_carry_it(self, tmp_path):
        """The real ladder: 720p keeps all six channels, 360p gets stereo.

        Justin's question — someone asking for the smallest picture is not
        asking for surround — and the answer to it. The upload itself is
        deleted after publishing unless "Keep original file" was ticked, so
        the higher rungs are where the surround has to survive.
        """
        src = _surround_clip(tmp_path / "surround.mp4", size="1280x720")
        result = asyncio.run(transcode.transcode_video_to_hls(src, tmp_path / "out"))
        assert result.success, result.error

        out = tmp_path / "out"
        assert _layout(out / "stream_720p" / "playlist.m3u8") == ("opus", 6)
        assert _layout(out / "stream_360p" / "playlist.m3u8") == ("opus", 2)
        assert _layout(out / "stream_audio" / "playlist.m3u8") == ("opus", 6)
        assert result.transcode_info["stereo_rungs"] == ["360p"]

    def test_several_tracks_get_a_stereo_group_for_the_low_rung(self, tmp_path):
        """Shared tracks can't downmix per rung, so 360p gets its own group.

        A surround camera mic beside a mono overdub: 720p references the
        full tracks, 360p references stereo copies of both.
        """
        src = tmp_path / "two.mp4"
        pan = "|".join(f"c{i}=c0" for i in range(6))
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y",
             "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=1",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
             "-f", "lavfi", "-i", "sine=frequency=880:duration=1",
             "-filter_complex", f"[1:a]pan=5.1|{pan}[surr]",
             "-map", "0:v", "-map", "[surr]", "-map", "2:a",
             "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(src)],
            check=True, capture_output=True)

        result = asyncio.run(transcode.transcode_video_to_hls(src, tmp_path / "out"))
        assert result.success, result.error

        out = tmp_path / "out"
        master = (out / "master.m3u8").read_text()
        # ffmpeg prefixes group ids with "group_".
        assert 'RESOLUTION=1280x720,AUDIO="group_aud"' in master
        assert 'RESOLUTION=640x360,AUDIO="group_aud_stereo"' in master
        assert _layout(out / "stream_track1" / "playlist.m3u8") == ("opus", 6)
        assert _layout(out / "stream_track1_stereo" / "playlist.m3u8") == ("opus", 2)
        assert _layout(out / "stream_track2_stereo" / "playlist.m3u8")[1] in (1, 2)
        assert result.transcode_info["stereo_rungs"] == ["360p"]

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
        assert result.transcode_info["stereo_rungs"] == [], \
            "a stereo upload was treated as surround"
