"""A file with more than one audio track must still encode.

Found on ReleaseDraft:50881aeb — "Melodica overdub for 4Masks", an iPhone .mov
whose video was stream #0:2, with two audio tracks and four `mebx` data
streams. The encoder mapped `0:a?` (every audio stream) while -var_stream_map
named a fixed set of outputs, so ffmpeg exited 234 having printed only its
input listing. The upload was fine; the encode was not.
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


def _make(path, audio_tracks, extra_data_stream=False):
    """A short clip with the given number of audio tracks."""
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


def _audio_streams(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a",
                          "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True).stdout
    return len([line for line in out.splitlines() if line.strip()])


@needs_encoder
@pytest.mark.parametrize("tracks", [1, 2, 3])
def test_encodes_whatever_the_audio_track_count(tmp_path, tracks):
    src = _make(tmp_path / f"{tracks}track.mp4", tracks)
    assert _audio_streams(src) == tracks

    result = asyncio.run(transcode.transcode_video_to_hls(src, tmp_path / f"out{tracks}"))
    assert result.success, result.error


@needs_encoder
def test_extra_tracks_do_not_multiply_the_variants(tmp_path):
    """Only the first audio track is published, whatever the source carries."""
    src = _make(tmp_path / "two.mp4", 2)
    out = tmp_path / "out"
    result = asyncio.run(transcode.transcode_video_to_hls(src, out))
    assert result.success, result.error

    # One directory per rung, plus exactly one audio-only stream.
    streams = sorted(p.name for p in out.glob("stream_*"))
    assert streams.count("stream_audio") == 1
    variants = [v["name"] for v in result.transcode_info["variants"]]
    assert variants.count("audio") == 1
    assert len(streams) == len(variants)
