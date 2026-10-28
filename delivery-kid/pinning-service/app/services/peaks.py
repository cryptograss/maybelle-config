"""Waveform peaks for a pinned audio file, for Rabbithole's timeline editor.

The editor draws the song's waveform down its ruler so a marker can be put
where the music visibly changes. Drawing it in the browser meant downloading
and decoding the whole song first -- 6 MB for a four-minute Ogg -- which is
not workable on mobile data (Justin, 2026-10-28, on a bus). So the server
does it once per file and keeps the answer: about 15 KB per song.

Only content this node already holds is decoded. A CID that is not local is
refused rather than fetched, so the endpoint cannot be used to make the node
pull arbitrary content off the network.
"""

import array
import asyncio
import base64
import json
import logging
import re
import shutil
import sys
from pathlib import Path
from typing import Optional

import httpx

from ..config import get_settings

logger = logging.getLogger(__name__)

# 50 a second is a 20ms grain: at the editor's deepest zoom a four-minute
# song is about 13,000px tall, so 50/s is a bucket per pixel or better.
PER_SECOND = 50
# Decoded to mono at this rate before reducing; plenty for a loudness shape.
DECODE_RATE = 8000

CID_PATTERN = re.compile(r"^(Qm[1-9A-HJ-NP-Za-km-z]{44}|b[a-z2-7]{50,100})$")

# Decoding is CPU-bound in ffmpeg; two at once is plenty for this box.
_slots = asyncio.Semaphore(2)
_in_flight: dict[str, asyncio.Future] = {}


def valid_cid(cid: str) -> bool:
    return bool(CID_PATTERN.match(cid or ""))


def compute_peaks(pcm: bytes, sample_rate: int = DECODE_RATE,
                  per_second: int = PER_SECOND) -> bytes:
    """
    Reduce signed 16-bit mono PCM to one byte per slice: the loudest sample
    in the slice, scaled so the loudest moment of the song is 255.
    """
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    if sys.byteorder != "little":
        samples.byteswap()
    per = max(1, sample_rate // per_second)
    raw = []
    for i in range(0, len(samples), per):
        chunk = samples[i:i + per]
        raw.append(max(max(chunk), -min(chunk)))
    top = max(raw) if raw else 0
    if not top:
        return bytes(len(raw))
    return bytes(min(255, round(v * 255 / top)) for v in raw)


def _cache_path(cid: str) -> Path:
    return Path(get_settings().staging_dir) / "peaks" / f"{cid}.json"


async def _is_local(cid: str) -> bool:
    """True if this node holds the root block, without asking the network."""
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(
                f"{settings.ipfs_api_url}/api/v0/block/stat",
                params={"arg": cid, "offline": "true"},
            )
            return r.status_code == 200
    except Exception:
        return False


async def _decode(cid: str) -> Optional[bytes]:
    """The file as 8kHz mono s16le, via ffmpeg reading the local gateway."""
    settings = get_settings()
    if not shutil.which("ffmpeg"):
        logger.error("peaks: ffmpeg not found")
        return None
    url = f"{settings.ipfs_local_gateway_url}/ipfs/{cid}"
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-v", "error", "-i", url,
        "-ac", "1", "-ar", str(DECODE_RATE), "-f", "s16le", "pipe:1",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=300)
    except asyncio.TimeoutError:
        proc.kill()
        logger.warning("peaks: decode of %s timed out", cid)
        return None
    if proc.returncode != 0:
        logger.warning("peaks: ffmpeg failed for %s: %s", cid, err.decode()[:300])
        return None
    return out


async def _build(cid: str) -> Optional[dict]:
    async with _slots:
        pcm = await _decode(cid)
    if not pcm:
        return None
    peaks = compute_peaks(pcm)
    result = {
        "cid": cid,
        "per_second": PER_SECOND,
        "duration": round(len(pcm) / 2 / DECODE_RATE, 3),
        "peaks": base64.b64encode(peaks).decode("ascii"),
    }
    path = _cache_path(cid)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(result))
    tmp.replace(path)
    return result


class NotLocal(Exception):
    pass


async def get_peaks(cid: str) -> Optional[dict]:
    """
    Peaks for a CID: from the disk cache, or decoded now and cached.
    Raises NotLocal for content this node doesn't hold; None if decoding failed.
    """
    path = _cache_path(cid)
    if path.exists():
        return json.loads(path.read_text())

    if cid in _in_flight:
        return await asyncio.shield(_in_flight[cid])

    if not await _is_local(cid):
        raise NotLocal(cid)

    task = asyncio.ensure_future(_build(cid))
    _in_flight[cid] = task
    try:
        return await asyncio.shield(task)
    finally:
        _in_flight.pop(cid, None)
