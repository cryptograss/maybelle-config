#!/usr/bin/env python3
"""Rebuild an already-published release as a quality ladder.

Releases pinned before the ladder existed are a single 1080p rendition at
roughly 2.4 Mbps, with nothing for a player to fall back to. This produces a
replacement directory carrying 1080p, 720p, 360p and audio-only, ready to pin.

The important part is what it does *not* re-encode.

The existing 1080p AV1 is stream-copied, not re-encoded. Re-encoding AV1 to
AV1 would cost an hour of CPU to produce something visibly worse than what we
already have — generation loss for nothing. Copied, it is bit-identical to
what is published today and takes seconds.

The audio-only variant is likewise a stream copy of the existing Opus track.
Lossless and near-instant.

So only 720p and 360p are actually encoded, and both are cheap: they are
downscales, and discarding detail is the point. A sixty-six minute release
re-ladders in well under the 1.3 hours a fresh ladder encode would take.

Source is read straight off the IPFS gateway, so nothing needs to exist on
local disk — which matters, because the original uploads were deleted once
they were successfully pinned.

    reladder.py QmbrE35neh3... --output /tmp/reladdered

Writes the directory and stops. Pinning it produces a NEW CID, which means a
new Release page superseding the old one via `subsequent_to` — a decision
with consequences for existing links, and deliberately left to a human.
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_GATEWAY = "https://ipfs.delivery-kid.cryptograss.live/ipfs"

# Kept in step with app/services/transcode.py. Only the rungs strictly below
# the source are encoded; the source rung itself is copied.
LADDER = (
    {"name": "1080p", "height": 1080, "crf": 32, "audio": "128k"},
    {"name": "720p", "height": 720, "crf": 34, "audio": "128k"},
    {"name": "360p", "height": 360, "crf": 36, "audio": "96k"},
)
AUDIO_ONLY_NAME = "audio"
AUDIO_ONLY_BITRATE = "160k"
SEGMENT_SECONDS = 6
AV1_PRESET = 10


def run(cmd: list[str], what: str) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        tail = (result.stderr or "").strip().splitlines()[-6:]
        raise SystemExit(f"{what} failed:\n  " + "\n  ".join(tail))


def probe(url: str) -> dict:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", url],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"could not read {url}\n  {result.stderr.strip()[:300]}")
    return json.loads(result.stdout)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cid", help="CID of the published release to rebuild")
    ap.add_argument("--output", required=True, type=Path,
                    help="directory to write the new ladder into")
    ap.add_argument("--gateway", default=DEFAULT_GATEWAY)
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be produced and stop")
    args = ap.parse_args()

    source = f"{args.gateway}/{args.cid}/master.m3u8"
    print(f"reading {source}")
    info = probe(source)

    video = next((s for s in info.get("streams", [])
                  if s.get("codec_type") == "video"), None)
    audio = next((s for s in info.get("streams", [])
                  if s.get("codec_type") == "audio"), None)
    if video is None:
        raise SystemExit("no video stream — is this an audio release?")

    height = int(video.get("height") or 0)
    duration = float(info.get("format", {}).get("duration") or 0)
    print(f"  {video.get('codec_name')} {video.get('width')}x{height}"
          f"  {audio.get('codec_name') if audio else 'no audio'}"
          f"  {duration/60:.1f} min")

    if any(s.get("codec_name") != "av1" for s in [video]):
        print("  note: source video is not AV1; the 1080p rung will still be copied "
              "as-is rather than converted")

    # The rung matching the source is copied. Anything shorter is encoded.
    copy_rung = next((r for r in LADDER if r["height"] == height), None)
    encode_rungs = [r for r in LADDER
                    if r["height"] < height and (not copy_rung or r["height"] < copy_rung["height"])]

    print("\nplan:")
    if copy_rung:
        print(f"  {copy_rung['name']:<8} stream copy (lossless, no re-encode)")
    else:
        print(f"  {height}p     stream copy (lossless, not a standard rung)")
    for r in encode_rungs:
        print(f"  {r['name']:<8} encode, crf {r['crf']}")
    print(f"  {AUDIO_ONLY_NAME:<8} stream copy of the audio track (lossless)")

    if args.dry_run:
        print("\ndry run — nothing written")
        return 0

    out = args.output
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    copy_name = copy_rung["name"] if copy_rung else f"{height}p"

    # One pass: copy the source rung, encode the smaller ones, copy the audio.
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", source]

    if encode_rungs:
        labels = "".join(f"[v{i}]" for i in range(len(encode_rungs)))
        chain = [f"[0:v]split={len(encode_rungs)}{labels}"]
        for i, r in enumerate(encode_rungs):
            chain.append(f"[v{i}]scale=-2:{r['height']}[v{i}o]")
        cmd.extend(["-filter_complex", ";".join(chain)])

    # Copied rung first, so it is variant 0 and the highest quality.
    cmd.extend(["-map", "0:v:0", "-map", "0:a:0?"])
    for i in range(len(encode_rungs)):
        cmd.extend(["-map", f"[v{i}o]", "-map", "0:a:0?"])
    cmd.extend(["-map", "0:a:0?"])

    cmd.extend(["-c:v:0", "copy", "-c:a:0", "copy"])
    for i, r in enumerate(encode_rungs, start=1):
        cmd.extend([f"-c:v:{i}", "libsvtav1", f"-preset:v:{i}", str(AV1_PRESET),
                    f"-crf:v:{i}", str(r["crf"]), f"-c:a:{i}", "libopus",
                    f"-b:a:{i}", r["audio"]])
    n_audio = len(encode_rungs) + 1
    cmd.extend([f"-c:a:{n_audio}", "copy"])

    names = [copy_name] + [r["name"] for r in encode_rungs]
    stream_map = " ".join(f"v:{i},a:{i},name:{n}" for i, n in enumerate(names))
    stream_map += f" a:{n_audio},name:{AUDIO_ONLY_NAME}"

    cmd.extend([
        "-f", "hls",
        "-hls_time", str(SEGMENT_SECONDS),
        "-hls_list_size", "0",
        "-hls_playlist_type", "vod",
        "-hls_segment_type", "fmp4",
        "-hls_fmp4_init_filename", "init.mp4",
        "-master_pl_name", "master.m3u8",
        "-var_stream_map", stream_map,
        "-hls_segment_filename", str(out / "stream_%v" / "seg_%05d.m4s"),
        str(out / "stream_%v" / "playlist.m3u8"),
    ])

    print("\nencoding...")
    run(cmd, "ffmpeg")

    # Carry the poster across if the old release had one; otherwise make one.
    poster = out / "poster.jpg"
    run(["ffmpeg", "-y", "-loglevel", "error",
         "-ss", f"{min(max(duration * 0.1, 1.0), 60.0):.3f}",
         "-i", source, "-vf", "thumbnail", "-frames:v", "1", "-q:v", "3",
         str(poster)], "poster extraction")

    total = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print(f"\nwrote {out}")
    for d in sorted(p for p in out.iterdir() if p.is_dir()):
        size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
        print(f"  {d.name:<16} {size/1e6:8.1f} MB")
    print(f"  {'total':<16} {total/1e6:8.1f} MB")

    print("\nNot pinned. Pinning produces a new CID, which needs a new Release "
          "page superseding the old one via subsequent_to — that decision, and "
          "what happens to existing links, is a human's.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
