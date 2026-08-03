"""Fetch media from a yt-dlp-compatible URL into a draft's staging directory.

Server-side counterpart to the browser's multipart upload: instead of the
client pushing bytes, delivery-kid pulls them. Everything downstream
(analysis, preview transcode, finalize, pin) is identical either way.
"""

import asyncio
import ipaddress
import json
import logging
import re
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Optional
from urllib.parse import urlparse

from . import analyze

logger = logging.getLogger(__name__)


# yt-dlp happily accepts file://, ytsearch:, and other non-network schemes.
# Only these two ever make sense from an untrusted caller.
ALLOWED_SCHEMES = {"http", "https"}

# Percentage lines look like:
#   [download]  12.3% of ~100.00MiB at 1.00MiB/s ETA 00:30
_PROGRESS_RE = re.compile(r"\[download\]\s+([\d.]+)%")


class UrlNotAllowed(ValueError):
    """The supplied URL failed the pre-flight safety check."""


@dataclass
class FetchResult:
    success: bool
    file_path: Optional[Path] = None
    info: Optional[dict] = None
    error: Optional[str] = None


def _is_public_address(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True only for addresses that route on the public internet."""
    # IPv4-mapped IPv6 (::ffff:10.0.0.2) must be judged on the embedded v4 address,
    # which the IPv6 is_private check alone does not catch.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def validate_fetchable_url(url: str) -> str:
    """Reject non-http(s) schemes and hosts that resolve into private space.

    delivery-kid sits on a private network alongside IPFS and the storage box,
    so an unchecked server-side fetch is an SSRF primitive. This blocks the
    direct cases.

    Known limits, accepted deliberately rather than papered over:

    - **DNS rebinding.** We resolve here; yt-dlp resolves again when it
      fetches. A hostile resolver can answer differently the second time.
    - **Redirects.** yt-dlp follows them itself, and a public URL can redirect
      to an internal one.

    Closing both properly means pinning the resolved address for the actual
    transfer, which yt-dlp gives us no hook for. The durable fix is egress
    filtering on the delivery-kid host. Callers are authenticated wiki users,
    and fetched bytes are only ever surfaced back to the draft owner, so the
    residual exposure is bounded.

    Returns the URL unchanged when it passes.

    Raises:
        UrlNotAllowed: scheme is not http/https, host is missing or
            unresolvable, or any resolved address is non-public.
    """
    parsed = urlparse(url.strip())

    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise UrlNotAllowed(
            f"Only http and https URLs are accepted (got '{parsed.scheme or 'no scheme'}')"
        )

    host = parsed.hostname
    if not host:
        raise UrlNotAllowed("URL has no host")

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise UrlNotAllowed(f"Could not resolve host '{host}': {e}") from e

    if not infos:
        raise UrlNotAllowed(f"Could not resolve host '{host}'")

    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            raise UrlNotAllowed(f"Host '{host}' resolved to an unparseable address")
        if not _is_public_address(ip):
            raise UrlNotAllowed(
                f"Host '{host}' resolves to non-public address {addr}"
            )

    return url.strip()


def _build_command(url: str, dest_dir: Path, max_filesize_mb: int) -> list[str]:
    """Assemble the yt-dlp argv.

    ``--no-playlist`` matters: the dropzone this mirrors takes one file, and a
    URL that happens to carry a list parameter would otherwise pull the whole
    playlist into the draft.
    """
    return [
        "yt-dlp",
        # Never read a config file — it could inject arbitrary options.
        # (.netrc is already opt-in via --netrc, which we simply don't pass.)
        "--ignore-config",
        "--no-config-locations",
        "--no-playlist",
        "--no-continue",
        # ASCII-only names; the analyzer and IPFS layers both prefer them.
        "--restrict-filenames",
        # One progress line per update rather than carriage-return repaints.
        "--newline",
        "--no-color",
        "--max-filesize", f"{max_filesize_mb}M",
        "--merge-output-format", "mp4/mkv/webm",
        "-f", "bv*+ba/b",
        # Sidecar metadata — feeds the title/description pre-fill on the form.
        "--write-info-json",
        "--paths", f"home:{dest_dir}",
        "-o", "%(title).120s-%(id)s.%(ext)s",
        "--",
        url,
    ]


def _pick_media_file(dest_dir: Path) -> Optional[Path]:
    """Largest file in dest_dir with an extension the analyzer accepts."""
    allowed = (
        analyze.AUDIO_EXTENSIONS | analyze.VIDEO_EXTENSIONS | analyze.IMAGE_EXTENSIONS
    )
    candidates = [
        p for p in dest_dir.iterdir()
        if p.is_file() and p.suffix.lower() in allowed
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_size)


def _read_info_json(dest_dir: Path) -> Optional[dict]:
    """Parse the first .info.json yt-dlp wrote, if any."""
    for p in sorted(dest_dir.glob("*.info.json")):
        try:
            with open(p) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            logger.warning("Could not parse info json at %s", p)
    return None


def source_metadata(info: dict) -> dict:
    """Project a yt-dlp info dict onto the handful of fields the form pre-fills.

    Namespaced under ``source_*`` so it never collides with — or silently
    overwrites — metadata the user typed in themselves.
    """
    upload_date = info.get("upload_date")  # YYYYMMDD
    if upload_date and len(upload_date) == 8 and upload_date.isdigit():
        upload_date = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}"

    fields = {
        "source_url": info.get("webpage_url") or info.get("original_url"),
        "source_title": info.get("title"),
        "source_description": info.get("description"),
        "source_uploader": info.get("uploader") or info.get("channel"),
        "source_upload_date": upload_date,
        "source_duration_seconds": info.get("duration"),
        "source_extractor": info.get("extractor_key") or info.get("extractor"),
    }
    return {k: v for k, v in fields.items() if v not in (None, "")}


async def fetch_url_to_dir(
    url: str,
    dest_dir: Path,
    *,
    max_filesize_mb: int,
    timeout_seconds: int,
    progress_callback: Optional[Callable[[str, Optional[float]], Awaitable[None]]] = None,
) -> FetchResult:
    """Run yt-dlp against ``url``, depositing media into ``dest_dir``.

    Args:
        url: Already passed through validate_fetchable_url.
        dest_dir: Created if absent. Receives the media file plus its
            ``.info.json`` sidecar.
        max_filesize_mb: Passed to ``--max-filesize``; yt-dlp skips anything
            larger rather than downloading and then failing.
        timeout_seconds: Wall-clock ceiling for the whole fetch.
        progress_callback: Awaited with (message, percent) as output arrives.
            Percent is None for non-progress lines.

    Returns:
        FetchResult. ``success`` False carries a human-readable ``error``;
        this never raises for an ordinary download failure.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    cmd = _build_command(url, dest_dir, max_filesize_mb)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except FileNotFoundError:
        return FetchResult(success=False, error="yt-dlp is not installed on this server")
    except Exception as e:
        return FetchResult(success=False, error=f"Could not start yt-dlp: {e}")

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    tail: list[str] = []
    last_reported: Optional[int] = None

    try:
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise asyncio.TimeoutError

            line_bytes = await asyncio.wait_for(proc.stdout.readline(), remaining)
            if not line_bytes:
                break

            line = line_bytes.decode("utf-8", errors="replace").rstrip()
            if not line:
                continue

            # Keep a bounded tail so a failure message survives to the caller
            # without dragging megabytes of progress spam into draft.json.
            tail.append(line)
            if len(tail) > 20:
                tail.pop(0)

            if progress_callback is None:
                continue

            match = _PROGRESS_RE.search(line)
            if match:
                pct = float(match.group(1))
                # One callback per whole percent; yt-dlp emits far more.
                bucket = int(pct)
                if last_reported is not None and bucket == last_reported:
                    continue
                last_reported = bucket
                await progress_callback(line, pct)
            else:
                await progress_callback(line, None)

        await asyncio.wait_for(proc.wait(), max(deadline - loop.time(), 1))

    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return FetchResult(
            success=False,
            error=f"Fetch exceeded the {timeout_seconds}s limit and was stopped",
        )
    except Exception as e:
        proc.kill()
        await proc.wait()
        logger.exception("yt-dlp fetch failed for %s", url)
        return FetchResult(success=False, error=f"Fetch error: {e}")

    if proc.returncode != 0:
        detail = " | ".join(tail[-5:]) or f"exit code {proc.returncode}"
        return FetchResult(success=False, error=f"yt-dlp failed: {detail}")

    media = _pick_media_file(dest_dir)
    if media is None:
        detail = " | ".join(tail[-5:]) or "no recognised media file was produced"
        return FetchResult(
            success=False,
            error=f"Fetch produced no usable media file: {detail}",
        )

    return FetchResult(success=True, file_path=media, info=_read_info_json(dest_dir))
