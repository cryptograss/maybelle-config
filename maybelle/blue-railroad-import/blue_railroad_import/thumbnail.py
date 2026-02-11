"""Thumbnail generation from IPFS videos."""

import subprocess
import tempfile
import shutil
from pathlib import Path
from typing import Optional
import urllib.request
import urllib.error

from cid import make_cid


IPFS_GATEWAY = "https://ipfs.maybelle.cryptograss.live"


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
    """Download video from IPFS gateway.

    Args:
        cid: IPFS content identifier
        output_path: Where to save the video
        timeout: Download timeout in seconds

    Returns:
        True if download succeeded, False otherwise
    """
    url = f"{IPFS_GATEWAY}/ipfs/{cid}"
    try:
        urllib.request.urlretrieve(url, output_path)
        return output_path.exists() and output_path.stat().st_size > 0
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        print(f"Failed to download video {cid}: {e}")
        return False
    except Exception as e:
        print(f"Unexpected error downloading video {cid}: {e}")
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
