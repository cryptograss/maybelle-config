"""Media transcoding - audio (FLAC to OGG) and video (to HLS)."""

import asyncio
import json
import logging
import shutil
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, Callable, Awaitable

logger = logging.getLogger(__name__)


@dataclass
class TranscodeResult:
    success: bool
    output_path: Optional[Path] = None
    error: Optional[str] = None
    # Rich metadata about what was produced
    transcode_info: Optional[dict] = field(default=None)


# --- HLS video encoding profile -------------------------------------------
# AV1 + Opus, because both are royalty-free — the same reason this project
# reached for Coconut's AV1 in the first place. SVT-AV1 is now fast enough to
# do it here: measured ~0.81x realtime for 1080p at preset 10 on two threads,
# which beats the libx264 path this replaces and drops a third party out of
# the critical path entirely.
#
# AV1 and Opus CANNOT be carried in MPEG-TS segments. ffmpeg will happily
# write a .ts playlist whose streams probe back as `bin_data` — silently
# unplayable. fMP4/CMAF segments are mandatory here, not a stylistic choice.
HLS_SEGMENT_SECONDS = 6
AV1_PRESET = 10          # 0 = slowest/best .. 13 = fastest
AV1_CRF = 35             # SVT-AV1's CRF scale is not x264's; 35 is a sane streaming default
OPUS_BITRATE = "128k"

# A still shown before playback starts. Lives inside the pinned directory so
# it shares the release's CID, and being a JPEG it renders on browsers that
# cannot decode the video itself.
POSTER_FILENAME = "poster.jpg"
POSTER_MAX_OFFSET_SECONDS = 60.0

# Progress reporting. ffmpeg emits a block every ~0.5s at 1080p; forwarding all
# of them would flood the SSE stream and the on-page log, so updates are
# throttled to one update every this many seconds. The consumer is expected
# to treat these as replacing the previous line, not accumulating.
PROGRESS_MIN_INTERVAL_SECONDS = 2.0
FFMPEG_STDERR_TAIL_LINES = 40


def _out_time_seconds(fields: dict) -> Optional[float]:
    """Seconds of output written so far, from an ffmpeg -progress block.

    ffmpeg reports `out_time_us` and, for historical reasons, an `out_time_ms`
    that is also microseconds. Prefer the honestly-named one and fall back to
    the formatted timestamp.
    """
    for key in ("out_time_us", "out_time_ms"):
        raw = fields.get(key)
        if raw and raw not in ("N/A", "-"):
            try:
                return int(raw) / 1_000_000
            except ValueError:
                pass
    stamp = fields.get("out_time")
    if stamp and stamp not in ("N/A", "-"):
        try:
            hours, minutes, seconds = stamp.split(":")
            return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        except ValueError:
            pass
    return None


def _clock(seconds: float) -> str:
    """Compact human duration: 45s, 6m12s, 1h04m."""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m{seconds % 60:02d}s"
    return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"


def _format_progress(done: Optional[float], total: float,
                     fields: dict, elapsed: float) -> str:
    """One readable line describing where the encode has got to.

    Deliberately front-loads the percentage: it is the thing someone staring
    at a stalled-looking page actually wants to know.
    """
    if done is None or total <= 0:
        return f"Encoding — {_clock(elapsed)} elapsed"

    pct = max(0.0, min(100.0, done / total * 100.0))
    parts = [f"Encoding {pct:.0f}%", f"{_clock(done)} of {_clock(total)}"]

    speed_raw = (fields.get("speed") or "").rstrip("x")
    speed = None
    try:
        speed = float(speed_raw)
    except ValueError:
        pass

    if speed and speed > 0:
        parts.append(f"{speed:.2f}x")
        remaining = (total - done) / speed
        if remaining > 1:
            parts.append(f"~{_clock(remaining)} left")
    elif elapsed > 5 and done > 0:
        # No speed field yet — extrapolate from wall clock instead of going quiet.
        remaining = elapsed * (total - done) / done
        parts.append(f"~{_clock(remaining)} left")

    return " · ".join(parts)


def _frame_rate(probe: Optional[dict]) -> float:
    """Best-effort source frame rate, defaulting to 30 when unknowable.

    Used to size the GOP so keyframes land on segment boundaries; without
    that, HLS segments drift off the requested duration.
    """
    if not probe:
        return 30.0
    for stream in probe.get("streams", []):
        if stream.get("codec_type") != "video":
            continue
        for key in ("avg_frame_rate", "r_frame_rate"):
            val = stream.get(key)
            if not val or val in ("0/0", "0"):
                continue
            try:
                num, _, den = val.partition("/")
                fps = float(num) / float(den or 1)
                if fps > 0:
                    return fps
            except (ValueError, ZeroDivisionError):
                continue
    return 30.0


async def _write_poster(input_path: Path, output_dir: Path,
                        total_seconds: float,
                        trim_start: Optional[float] = None) -> Optional[str]:
    """Pull a representative still out of the video, next to the HLS output.

    Without one, a player shows an empty black rectangle until someone presses
    play — and on a browser that cannot decode AV1 it shows an empty black
    rectangle forever. A JPEG decodes everywhere, so even a viewer who cannot
    play the video sees what it is.

    Because the poster lands inside the directory that gets pinned, it travels
    with the release under the same CID. Costs perhaps 150KB against a video
    of several hundred megabytes.

    Failure is non-fatal and deliberately so: a missing thumbnail is a
    cosmetic loss, and must never fail an encode that otherwise worked.
    """
    poster = output_dir / POSTER_FILENAME

    # Ten percent in, capped — the opening seconds of a recording are usually
    # a lens cap, a black frame, or somebody still walking to their seat.
    offset = (trim_start or 0.0) + min(max(total_seconds * 0.1, 1.0),
                                       POSTER_MAX_OFFSET_SECONDS)

    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-ss", f"{offset:.3f}",
        "-i", str(input_path),
        # The thumbnail filter scores a window of frames and emits the most
        # representative, which avoids landing on a black or blurred one.
        "-vf", "thumbnail",
        "-frames:v", "1",
        "-q:v", "3",
        str(poster),
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0 or not poster.exists():
            logger.warning("poster frame not written: %s",
                           (stderr or b"").decode(errors="replace")[:200])
            return None
        return POSTER_FILENAME
    except Exception:
        logger.exception("poster frame failed")
        return None


async def _has_encoder(name: str) -> bool:
    """Check whether this ffmpeg build exposes a given encoder."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-hide_banner", "-encoders",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        return name.encode() in (stdout or b"")
    except Exception:
        return False


async def probe_video(path: Path) -> Optional[dict]:
    """Use ffprobe to get video file metadata."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_format", "-show_streams", str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            return None
        return json.loads(stdout.decode())
    except Exception:
        return None


async def transcode_flac_to_ogg(
    input_path: Path,
    output_path: Path,
    quality: int = 6,
    metadata: Optional[dict[str, str]] = None,
    progress_callback: Optional[Callable[[str], Awaitable[None]]] = None
) -> TranscodeResult:
    """
    Transcode a FLAC file to OGG Vorbis.

    Args:
        input_path: Path to input FLAC file
        output_path: Path for output OGG file
        quality: OGG quality (0-10, default 6 ≈ 192kbps)
        metadata: Optional dict of metadata tags to embed (KEY: VALUE)
        progress_callback: Optional async callback for progress updates

    Returns:
        TranscodeResult with success status and output path
    """
    if not input_path.exists():
        return TranscodeResult(success=False, error=f"Input file not found: {input_path}")

    # Check for ffmpeg
    if not shutil.which("ffmpeg"):
        return TranscodeResult(success=False, error="ffmpeg not found")

    # Ensure output directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if progress_callback:
        await progress_callback(f"Transcoding {input_path.name}")

    try:
        # Build ffmpeg command
        cmd = [
            "ffmpeg",
            "-i", str(input_path),
            "-c:a", "libvorbis",
            "-q:a", str(quality),
        ]

        # Add metadata tags
        if metadata:
            for key, value in metadata.items():
                cmd.extend(["-metadata", f"{key}={value}"])

        cmd.extend([
            "-y",  # Overwrite output
            str(output_path)
        ])

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            error_msg = stderr.decode() if stderr else "Unknown ffmpeg error"
            return TranscodeResult(success=False, error=error_msg)

        if not output_path.exists():
            return TranscodeResult(success=False, error="Output file not created")

        return TranscodeResult(success=True, output_path=output_path)

    except Exception as e:
        return TranscodeResult(success=False, error=str(e))


async def transcode_album_directory(
    input_dir: Path,
    output_dir: Path,
    progress_callback: Optional[Callable[[str], Awaitable[None]]] = None
) -> tuple[bool, list[Path], list[str]]:
    """
    Transcode all FLAC files in a directory to OGG.

    Args:
        input_dir: Directory containing FLAC files
        output_dir: Directory for OGG output
        progress_callback: Optional callback for progress updates

    Returns:
        Tuple of (success, list of output paths, list of errors)
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    flac_files = sorted(input_dir.glob("*.flac"))
    if not flac_files:
        flac_files = sorted(input_dir.glob("*.FLAC"))

    if not flac_files:
        return (False, [], ["No FLAC files found in directory"])

    outputs = []
    errors = []

    for i, flac_path in enumerate(flac_files):
        ogg_name = flac_path.stem + ".ogg"
        ogg_path = output_dir / ogg_name

        if progress_callback:
            await progress_callback(f"Transcoding {i+1}/{len(flac_files)}: {flac_path.name}")

        result = await transcode_flac_to_ogg(flac_path, ogg_path)

        if result.success and result.output_path:
            outputs.append(result.output_path)
        else:
            errors.append(f"{flac_path.name}: {result.error}")

    success = len(outputs) > 0 and len(errors) == 0
    return (success, outputs, errors)


async def transcode_video_to_hls(
    input_path: Path,
    output_dir: Path,
    progress_callback: Optional[Callable[[str, Optional[float]], Awaitable[None]]] = None,
    trim_start: Optional[float] = None,
    trim_end: Optional[float] = None,
) -> TranscodeResult:
    """
    Transcode a video file to HLS (HTTP Live Streaming) format.

    Creates a directory with master.m3u8, an init.mp4 and fMP4 segments,
    suitable for streaming via IPFS gateway.

    Output is AV1 video + Opus audio — both royalty-free. Note that this
    REQUIRES fMP4 segments; neither codec can be carried in MPEG-TS, and
    ffmpeg produces an unplayable playlist rather than erroring if you try.

    Args:
        input_path: Path to input video file
        output_dir: Directory to write HLS output (master.m3u8 + segments)
        progress_callback: Optional async callback for progress updates

    Returns:
        TranscodeResult with success status and output directory path
    """
    if not input_path.exists():
        return TranscodeResult(success=False, error=f"Input file not found: {input_path}")

    if not shutil.which("ffmpeg"):
        return TranscodeResult(success=False, error="ffmpeg not found")

    if not await _has_encoder("libsvtav1"):
        return TranscodeResult(
            success=False,
            error="ffmpeg has no libsvtav1 encoder; cannot produce royalty-free AV1 output",
        )

    output_dir.mkdir(parents=True, exist_ok=True)

    if progress_callback:
        await progress_callback(f"Transcoding {input_path.name} to AV1/Opus HLS", 0.0)

    try:
        # Build ffmpeg HLS command
        # - Multiple quality renditions for adaptive streaming
        # - 6-second segments
        # - master playlist
        master_playlist = output_dir / "master.m3u8"

        # Probe first: the GOP length is derived from the source frame rate so
        # that keyframes land exactly on segment boundaries. Without this the
        # encoder picks its own GOP and segments drift off HLS_SEGMENT_SECONDS.
        source_probe = await probe_video(input_path)
        fps = _frame_rate(source_probe)
        gop = max(1, int(round(fps * HLS_SEGMENT_SECONDS)))

        cmd = [
            "ffmpeg", "-y",
            # Machine-readable progress on stdout. Without this the encode is
            # a black box: for an hour-long source the caller sees "started"
            # and then nothing until it finishes, which is indistinguishable
            # from a hang.
            "-nostats",
            "-progress", "pipe:1",
        ]
        # Trim: -ss before -i for fast seek, -to after -i for end time
        if trim_start is not None:
            cmd.extend(["-ss", str(trim_start)])
        cmd.extend([
            "-i", str(input_path),
        ])
        if trim_end is not None:
            # -to is relative to -ss when -ss is before -i
            if trim_start is not None:
                cmd.extend(["-to", str(trim_end - trim_start)])
            else:
                cmd.extend(["-to", str(trim_end)])
        cmd.extend([
            # Video: AV1 via SVT-AV1 — royalty-free, and at preset 10 faster
            # than the libx264 profile it replaces.
            "-c:v", "libsvtav1",
            "-preset", str(AV1_PRESET),
            "-crf", str(AV1_CRF),
            "-g", str(gop),          # keyframe every segment, see above
            "-pix_fmt", "yuv420p",   # force 8-bit — 10-bit breaks some decoders
            # Audio: Opus — royalty-free, replaces AAC
            "-c:a", "libopus",
            "-b:a", OPUS_BITRATE,
            # HLS output. fMP4 segments are REQUIRED: MPEG-TS cannot carry
            # AV1 or Opus, and ffmpeg fails silently rather than loudly.
            "-f", "hls",
            "-hls_time", str(HLS_SEGMENT_SECONDS),
            "-hls_list_size", "0",   # keep all segments in the playlist
            "-hls_playlist_type", "vod",
            "-hls_segment_type", "fmp4",
            "-hls_fmp4_init_filename", "init.mp4",
            "-hls_segment_filename", str(output_dir / "segment_%05d.m4s"),
            str(master_playlist),
        ])

        # How much media we expect to encode, so percentages mean something.
        total_seconds = float((source_probe or {}).get("format", {}).get("duration", 0) or 0)
        if trim_start is not None or trim_end is not None:
            start = trim_start or 0.0
            end = trim_end if trim_end is not None else (total_seconds or 0.0)
            total_seconds = max(0.0, end - start)

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # stderr must be drained concurrently: if its pipe buffer fills while
        # we are busy reading stdout, ffmpeg blocks on write and the whole
        # encode deadlocks. Keep only the tail — ffmpeg is chatty and we only
        # ever want the last lines for an error message.
        stderr_tail: list[str] = []

        async def _drain_stderr() -> None:
            assert process.stderr is not None
            while True:
                line = await process.stderr.readline()
                if not line:
                    break
                stderr_tail.append(line.decode(errors="replace").rstrip())
                if len(stderr_tail) > FFMPEG_STDERR_TAIL_LINES:
                    del stderr_tail[0]

        drainer = asyncio.create_task(_drain_stderr())

        fields: dict[str, str] = {}
        last_emit = 0.0
        last_pct = -1.0
        started = time.monotonic()

        assert process.stdout is not None
        while True:
            raw = await process.stdout.readline()
            if not raw:
                break
            line = raw.decode(errors="replace").strip()
            key, sep, value = line.partition("=")
            if not sep:
                continue
            fields[key.strip()] = value.strip()

            # ffmpeg terminates each progress block with progress=continue|end
            if key.strip() != "progress":
                continue

            done_seconds = _out_time_seconds(fields)
            pct = None
            if total_seconds > 0 and done_seconds is not None:
                pct = max(0.0, min(100.0, done_seconds / total_seconds * 100.0))

            # Purely time-throttled. An earlier version also emitted on every
            # whole percent of movement, which fires once a second on short
            # clips and would put ~1500 lines on the page for an hour-long one.
            # Always emit ffmpeg's final block, throttle or not. Otherwise the
            # last thing the page shows is whatever percentage happened to fall
            # on a tick — "96% · ~2s left" — and it never visibly finishes.
            final = fields.get("progress") == "end"

            now = time.monotonic()
            if progress_callback and (final or (now - last_emit) >= PROGRESS_MIN_INTERVAL_SECONDS):
                last_emit = now
                if pct is not None:
                    last_pct = pct
                await progress_callback(
                    _format_progress(done_seconds, total_seconds, fields, now - started),
                    pct,
                )

            fields.clear()

        await process.wait()
        await drainer

        if process.returncode != 0:
            error_msg = "\n".join(stderr_tail) if stderr_tail else "ffmpeg failed with no stderr output"
            return TranscodeResult(
                success=False,
                error=f"ffmpeg exited {process.returncode}: {error_msg}",
            )

        if not master_playlist.exists():
            return TranscodeResult(success=False, error="master.m3u8 not created")

        # A still for the player to show before playback starts. Written after
        # the encode so a failure here cannot cost us a successful transcode.
        poster_name = await _write_poster(
            input_path, output_dir, total_seconds, trim_start
        )
        if progress_callback and poster_name:
            await progress_callback("Poster frame written", None)

        # Gather transcode metadata
        segments = sorted(output_dir.glob("segment_*.m4s"))
        segment_sizes = {s.name: s.stat().st_size for s in segments}
        total_output_size = sum(segment_sizes.values())

        # Probe the playlist, not a bare segment: an fMP4 .m4s carries no
        # codec configuration on its own (that lives in init.mp4), so probing
        # one directly reports nothing useful.
        output_probe = await probe_video(master_playlist) if segments else None
        output_streams = {}
        if output_probe:
            for stream in output_probe.get("streams", []):
                if stream["codec_type"] == "video":
                    output_streams["video"] = {
                        "codec": stream.get("codec_name"),
                        "profile": stream.get("profile"),
                        "pix_fmt": stream.get("pix_fmt"),
                        "width": stream.get("width"),
                        "height": stream.get("height"),
                    }
                elif stream["codec_type"] == "audio":
                    output_streams["audio"] = {
                        "codec": stream.get("codec_name"),
                        "sample_rate": stream.get("sample_rate"),
                        "channels": stream.get("channels"),
                    }

        # Source was probed up front for the GOP calculation; reuse it.
        source_info = {}
        if source_probe:
            fmt = source_probe.get("format", {})
            source_info["duration_seconds"] = float(fmt.get("duration", 0))
            source_info["size_bytes"] = int(fmt.get("size", 0))
            source_info["format"] = fmt.get("format_long_name")
            for stream in source_probe.get("streams", []):
                if stream["codec_type"] == "video":
                    source_info["video_codec"] = stream.get("codec_name")
                    source_info["pix_fmt"] = stream.get("pix_fmt")
                    source_info["width"] = stream.get("width")
                    source_info["height"] = stream.get("height")
                elif stream["codec_type"] == "audio":
                    source_info["audio_codec"] = stream.get("codec_name")

        transcode_info = {
            "method": "local-ffmpeg",
            "output_codec": output_streams.get("video", {}).get("codec"),
            "output_pix_fmt": output_streams.get("video", {}).get("pix_fmt"),
            "output_width": output_streams.get("video", {}).get("width"),
            "output_height": output_streams.get("video", {}).get("height"),
            "output_audio_codec": output_streams.get("audio", {}).get("codec"),
            "segment_count": len(segments),
            "total_output_size_bytes": total_output_size,
            "source": source_info,
            "royalty_free": True,
            # Relative to the pinned directory, so a player can build the URL
            # from the release CID alone. None when extraction failed.
            "poster": poster_name,
            "ffmpeg_settings": {
                "video_codec": "libsvtav1",
                "preset": AV1_PRESET,
                "crf": AV1_CRF,
                "gop": gop,
                "source_fps": round(fps, 3),
                "pix_fmt": "yuv420p",
                "audio_codec": "libopus",
                "audio_bitrate": OPUS_BITRATE,
                "hls_segment_duration": HLS_SEGMENT_SECONDS,
                "hls_segment_type": "fmp4",
            },
        }

        return TranscodeResult(
            success=True,
            output_path=output_dir,
            transcode_info=transcode_info,
        )

    except Exception as e:
        return TranscodeResult(success=False, error=str(e))
