"""Thumbnail generation from IPFS videos."""

import json
import subprocess
import tempfile
import shutil
from pathlib import Path
from typing import Optional
import urllib.request
import urllib.error

from cid import make_cid


IPFS_GATEWAYS = [
    "https://ipfs.maybelle.cryptograss.live",
    "https://ipfs.io",
]

MAYBELLE_GATEWAY = "https://ipfs.maybelle.cryptograss.live"

# Hysteresis: require 3 consecutive failures before marking as unpinned
FAILURE_THRESHOLD = 3

# Use persistent Hetzner volume if available, otherwise fall back to temp
# /mnt/persist survives even full Jenkins rebuilds
_PERSIST_CACHE_DIR = Path("/mnt/persist/blue-railroad-cache")
_DEFAULT_CACHE_DIR = Path(tempfile.gettempdir())
CACHE_DIR = _PERSIST_CACHE_DIR if _PERSIST_CACHE_DIR.exists() else _DEFAULT_CACHE_DIR
CACHE_FILE = CACHE_DIR / "maybelle_pin_cache.json"


def _load_pin_cache() -> dict:
    """Load the pin status cache from disk."""
    try:
        if CACHE_FILE.exists():
            with open(CACHE_FILE, 'r') as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_pin_cache(cache: dict) -> None:
    """Save the pin status cache to disk."""
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CACHE_FILE, 'w') as f:
            json.dump(cache, f)
    except Exception as e:
        print(f"Warning: Could not save pin cache: {e}")


def check_maybelle_pinned(cid: str) -> bool:
    """Check if a CID is available on the maybelle IPFS gateway.

    Uses hysteresis to avoid flip-flopping when the gateway is temporarily
    slow or overloaded. A CID is only marked as unpinned after 3 consecutive
    failed checks.
    """
    if not cid:
        return False

    cache = _load_pin_cache()
    cid_status = cache.get(cid, {"pinned": True, "failures": 0})

    url = f"{MAYBELLE_GATEWAY}/ipfs/{cid}"
    try:
        req = urllib.request.Request(url, method='HEAD')
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status == 200:
                # Success - reset failures, mark as pinned
                cache[cid] = {"pinned": True, "failures": 0}
                _save_pin_cache(cache)
                return True
    except Exception:
        pass

    # Check failed - increment failure count
    failures = cid_status.get("failures", 0) + 1
    was_pinned = cid_status.get("pinned", True)

    if failures >= FAILURE_THRESHOLD:
        # Enough consecutive failures - mark as unpinned
        cache[cid] = {"pinned": False, "failures": failures}
        _save_pin_cache(cache)
        return False
    else:
        # Not enough failures yet - keep previous status
        cache[cid] = {"pinned": was_pinned, "failures": failures}
        _save_pin_cache(cache)
        return was_pinned


def normalize_cid(cid: str) -> str:
    """Normalize a CID to CIDv1 base32 format.

    CIDv0 (Qm...) and CIDv1 (bafy...) can represent the same content.
    This normalizes both to CIDv1 base32 so identical content gets
    the same filename regardless of which CID version was used.
    """
    try:
        parsed = make_cid(cid)
        # Convert to CIDv1 if it's v0
        if parsed.version == 0:
            parsed = parsed.to_v1()
        # Return base32 encoded string (bafy... format)
        return parsed.encode("base32").decode("ascii")
    except Exception as e:
        print(f"Warning: Could not normalize CID {cid}: {e}")
        return cid  # Fall back to original if parsing fails


def download_video(cid: str, output_path: Path, timeout: int = 60) -> bool:
    """Download video from IPFS gateway with fallback.

    Tries maybelle gateway first, falls back to public ipfs.io if needed.

    Args:
        cid: IPFS content identifier
        output_path: Where to save the video
        timeout: Download timeout in seconds

    Returns:
        True if download succeeded, False otherwise
    """
    for gateway in IPFS_GATEWAYS:
        url = f"{gateway}/ipfs/{cid}"
        try:
            print(f"  Trying {gateway}...")
            urllib.request.urlretrieve(url, output_path)
            if output_path.exists() and output_path.stat().st_size > 0:
                print(f"  Downloaded from {gateway}")
                return True
        except (urllib.error.URLError, urllib.error.HTTPError) as e:
            print(f"  {gateway} failed: {e}")
            continue
        except Exception as e:
            print(f"  {gateway} unexpected error: {e}")
            continue

    print(f"Failed to download video {cid} from all gateways")
    return False


def extract_frame(video_path: Path, output_path: Path, time_seconds: float = 2.0) -> bool:
    """Extract a single frame from video using ffmpeg.

    Args:
        video_path: Path to input video
        output_path: Where to save the thumbnail image
        time_seconds: Time offset to extract frame from

    Returns:
        True if extraction succeeded, False otherwise
    """
    # Check ffmpeg is available
    if not shutil.which('ffmpeg'):
        print("ffmpeg not found in PATH")
        return False

    try:
        result = subprocess.run([
            'ffmpeg', '-y',
            '-ss', str(time_seconds),
            '-i', str(video_path),
            '-vframes', '1',
            '-q:v', '2',  # High quality JPEG
            str(output_path)
        ], check=True, capture_output=True, timeout=30)
        return output_path.exists() and output_path.stat().st_size > 0
    except subprocess.CalledProcessError as e:
        print(f"ffmpeg failed: {e.stderr.decode() if e.stderr else 'unknown error'}")
        return False
    except subprocess.TimeoutExpired:
        print("ffmpeg timed out")
        return False


def generate_thumbnail(cid: str, output_dir: Optional[Path] = None) -> Optional[Path]:
    """Generate thumbnail for a video by its IPFS CID.

    Downloads the video from IPFS, extracts a frame at ~2 seconds,
    and saves it as a JPEG thumbnail. Filename is based on the CID,
    so multiple tokens sharing the same video will share the thumbnail.

    Args:
        cid: IPFS content identifier for the video
        output_dir: Directory to save thumbnail (defaults to temp dir)

    Returns:
        Path to generated thumbnail, or None if generation failed
    """
    if not cid:
        return None

    # Use provided output dir or temp directory
    if output_dir is None:
        output_dir = Path(tempfile.gettempdir())
    output_dir.mkdir(parents=True, exist_ok=True)

    thumb_filename = get_thumbnail_filename(cid)
    final_path = output_dir / thumb_filename

    # Create a temporary directory for the video download
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        video_path = tmpdir / f"video_{cid}.mp4"
        temp_thumb_path = tmpdir / thumb_filename

        print(f"Downloading video {cid} from IPFS...")
        if not download_video(cid, video_path):
            return None

        print(f"Extracting thumbnail frame...")
        if not extract_frame(video_path, temp_thumb_path):
            # Try at 0 seconds if 2 seconds fails (video might be shorter)
            if not extract_frame(video_path, temp_thumb_path, time_seconds=0.5):
                return None

        # Move to final location
        shutil.move(str(temp_thumb_path), str(final_path))
        print(f"Generated thumbnail: {final_path}")
        return final_path


def get_thumbnail_filename(cid: str) -> str:
    """Get the wiki filename for a video thumbnail based on its IPFS CID.

    CIDs are normalized to CIDv1 base32 format so that the same content
    always gets the same filename, regardless of whether it was referenced
    as CIDv0 (Qm...) or CIDv1 (bafy...).
    """
    normalized = normalize_cid(cid)
    return f"Blue_Railroad_Video_{normalized}.jpg"
