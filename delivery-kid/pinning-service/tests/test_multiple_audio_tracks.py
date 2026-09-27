"""Whatever audio the uploader gave us should come out the other side.

Found on ReleaseDraft:50881aeb — "Melodica overdub for 4Masks", an iPhone .mov
with two audio tracks, which made ffmpeg exit 234 having printed only its
input listing. The first fix published the first track and dropped the rest;
Justin pointed out that this throws away what somebody uploaded, and for an
overdub the second take may be the whole point.

A silent video failed outright for the same underlying reason: the command
named an audio output that did not exist.
"""

import asyncio
import re
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


def _make(path, audio_tracks):
    """A short clip carrying exactly this many audio tracks."""
    cmd = ["ffmpeg", "-v", "error", "-y",
           "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=1"]
    for n in range(audio_tracks):
        cmd += ["-f", "lavfi", "-i", f"sine=frequency={440 + n * 220}:duration=1"]
    cmd += ["-map", "0:v"]
    for n in range(audio_tracks):
        cmd += ["-map", f"{n + 1}:a"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(path)]
    subprocess.run(cmd, check=True, capture_output=True)
    return path


def _segments(directory):
    return sorted(p.name for p in directory.glob("stream_*")
                  if list(p.glob("seg_*.m4s")))


class TestListingAudioStreams:
    def test_positions_count_audio_streams_only(self):
        """``-map 0:a:N`` counts among audio streams, not all streams. An
        iPhone file is video, audio, audio, then several mebx metadata
        streams; its second audio track is 0:a:1, not 0:2."""
        probe = {"streams": [
            {"codec_type": "video", "codec_name": "hevc"},
            {"codec_type": "audio", "codec_name": "aac", "channels": 2},
            {"codec_type": "audio", "codec_name": "none",
             "codec_tag_string": "apac", "channels": 4},
            {"codec_type": "data", "codec_name": "none", "codec_tag_string": "mebx"},
        ]}
        streams = transcode._audio_streams(probe)
        assert [s["position"] for s in streams] == [0, 1]
        assert streams[1]["codec_tag"] == "apac"
        assert streams[1]["channels"] == 4

    def test_no_audio_is_an_empty_list(self):
        assert transcode._audio_streams(
            {"streams": [{"codec_type": "video", "codec_name": "h264"}]}) == []
        assert transcode._audio_streams(None) == []


@needs_encoder
class TestDecodeTest:
    """What is publishable is decided by trying to decode it, not by reading
    the codec name. Sky's spatial audio track (APAC) probes as "none", but
    the next surprise might be a codec ffmpeg names confidently and still
    cannot decode — and guessing is how this bug arrived twice.
    """

    def test_a_readable_track_passes(self, tmp_path):
        src = _make(tmp_path / "one.mp4", 1)
        streams = transcode._audio_streams(
            asyncio.run(transcode.probe_video(src)))
        usable, skipped = asyncio.run(transcode._decodable_audio(src, streams))
        assert usable == [0]
        assert skipped == []

    def test_both_readable_tracks_pass(self, tmp_path):
        src = _make(tmp_path / "two.mp4", 2)
        streams = transcode._audio_streams(
            asyncio.run(transcode.probe_video(src)))
        usable, skipped = asyncio.run(transcode._decodable_audio(src, streams))
        assert usable == [0, 1]

    def test_a_track_that_cannot_be_read_is_skipped_with_a_reason(self, tmp_path):
        """Pointed at a stream that does not exist, the decode test must
        report rather than raise — the same path a real undecodable track
        takes. APAC itself cannot be synthesised here: ffmpeg decodes by
        content, so a forged codec tag is still decoded correctly.
        """
        src = _make(tmp_path / "one.mp4", 1)
        phantom = [{"position": 7, "codec_name": "none",
                    "codec_tag": "apac", "channels": 4}]
        usable, skipped = asyncio.run(transcode._decodable_audio(src, phantom))
        assert usable == []
        assert len(skipped) == 1
        assert skipped[0]["codec_tag"] == "apac"
        assert skipped[0]["reason"]          # carries ffmpeg's own words

    def test_a_silent_file_has_nothing_to_test(self, tmp_path):
        src = _make(tmp_path / "silent.mp4", 0)
        streams = transcode._audio_streams(asyncio.run(transcode.probe_video(src)))
        assert streams == []
        assert asyncio.run(transcode._decodable_audio(src, streams)) == ([], [])


@needs_encoder
@pytest.mark.parametrize("tracks", [1, 2, 3])
def test_encodes_whatever_the_audio_track_count(tmp_path, tracks):
    src = _make(tmp_path / f"{tracks}track.mp4", tracks)
    result = asyncio.run(transcode.transcode_video_to_hls(src, tmp_path / f"out{tracks}"))
    assert result.success, result.error


@needs_encoder
def test_a_silent_video_publishes(tmp_path):
    """No audio at all used to fail with "Conversion failed!" and nothing else."""
    src = _make(tmp_path / "silent.mp4", 0)
    out = tmp_path / "out"
    result = asyncio.run(transcode.transcode_video_to_hls(src, out))
    assert result.success, result.error
    assert result.transcode_info["audio_tracks"] == []
    # No audio-only rendition promised for a file with no audio in it.
    assert "stream_audio" not in _segments(out)
    assert not any(v["name"] == "audio" for v in result.transcode_info["variants"])


@needs_encoder
def test_every_track_is_published_not_just_the_first(tmp_path):
    src = _make(tmp_path / "two.mp4", 2)
    out = tmp_path / "out"
    result = asyncio.run(transcode.transcode_video_to_hls(src, out))
    assert result.success, result.error

    # Both tracks have real media on disk, not just an entry in a playlist.
    dirs = _segments(out)
    assert "stream_track1" in dirs and "stream_track2" in dirs

    # And both are offered to the player as alternate renditions, so the
    # viewer can watch the video while choosing which take they hear.
    master = (out / "master.m3u8").read_text()
    assert len(re.findall(r"EXT-X-MEDIA:TYPE=AUDIO", master)) == 2

    tracks = result.transcode_info["audio_tracks"]
    assert [t["name"] for t in tracks] == ["track1", "track2"]
    assert tracks[0]["default"] and not tracks[1]["default"]


@needs_encoder
def test_audio_only_survives_the_alternate_renditions(tmp_path):
    """ffmpeg drops the audio-only rendition from the master once a group is
    in play. It is the whole point for anyone on a tether, so it is added
    back — and must point at media that exists."""
    src = _make(tmp_path / "two.mp4", 2)
    out = tmp_path / "out"
    result = asyncio.run(transcode.transcode_video_to_hls(src, out))
    assert result.success, result.error

    master = (out / "master.m3u8").read_text()
    assert "stream_audio/playlist.m3u8" in master
    assert "stream_audio" in _segments(out)

    # The bandwidth claimed for it should be measured, not invented.
    entry = [line for line in master.splitlines()
             if line.startswith("#EXT-X-STREAM-INF") and "opus" in line]
    assert entry, master
    bandwidth = int(re.search(r"BANDWIDTH=(\d+)", entry[0]).group(1))
    assert 1_000 < bandwidth < 1_000_000, bandwidth
