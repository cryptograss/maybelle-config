"""The seeding folder: how the web service and the seeder hand torrents to each other.

The BitTorrent seeder runs in its own container (``python -m app.seeder_main``),
not inside the web service. The two share nothing but this folder, on the
Storage Box (``/staging/seeding``):

    seeding/
      <cid>/torrent.dat        a seeded torrent: its metainfo
      <cid>/data/...           ...and its files, laid out as libtorrent expects
      .incoming/<cid>.<n>/     a torrent the web service has handed over,
                               waiting for the seeder (torrent.dat + data/)
      .incoming/.tmp-<n>/      one still being written; the seeder ignores it
      .incoming/.failed-...    one the seeder couldn't take; kept to look at
      .by-infohash/<infohash>  the CID seeding that infohash (written by the seeder)
      .status.json             what the seeder is doing (written by the seeder)

Only the seeder writes ``<cid>/``, ``.by-infohash`` and ``.status.json``. The
web service only writes into ``.incoming`` and reads the rest. Names starting
with a dot stay out of ``ls``, which the storage audit
(maybelle/scripts/audit-storage.py) reads as the list of seeded CIDs.

Nothing here imports libtorrent: the web service doesn't load it at all.
"""

import json
import logging
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

INCOMING = ".incoming"
BY_INFOHASH = ".by-infohash"
STATUS_FILE = ".status.json"
TMP_PREFIX = ".tmp-"
FAILED_PREFIX = ".failed-"

# The seeder rewrites .status.json every STATUS_INTERVAL seconds; older than
# STATUS_STALE_SECONDS and we say it isn't running.
STATUS_INTERVAL = 30
STATUS_STALE_SECONDS = 120

_INFOHASH_RE = re.compile(r"^[0-9a-f]{40}$")
_CID_RE = re.compile(r"^[A-Za-z0-9]+$")


def stage_for_seeding(seeding_dir: str | Path, cid: str, torrent_bytes: bytes,
                      content_dir: Path) -> Optional[Path]:
    """Hand a torrent and its files to the seeder.

    Moves ``content_dir`` (the files the torrent was made from) and writes the
    ``.torrent`` into ``.incoming/.tmp-<n>/``, then renames that to
    ``.incoming/<cid>.<n>/`` in one step, so the seeder never sees half of it.
    The seeder lays the files out, starts seeding, and removes the entry.

    Returns the entry's path, or None if it couldn't be written.
    """
    if not _CID_RE.match(cid):
        logger.error("Refusing to stage torrent for odd CID %r", cid)
        return None

    incoming = Path(seeding_dir) / INCOMING
    token = uuid.uuid4().hex[:12]
    tmp = incoming / f"{TMP_PREFIX}{token}"
    final = incoming / f"{cid}.{token}"
    try:
        tmp.mkdir(parents=True)
        shutil.move(str(content_dir), str(tmp / "data"))
        (tmp / "torrent.dat").write_bytes(torrent_bytes)
        os.rename(tmp, final)
        return final
    except Exception as e:
        logger.error("Failed to stage torrent for CID %s: %s", cid, e)
        shutil.rmtree(tmp, ignore_errors=True)
        return None


def torrent_file_by_infohash(seeding_dir: str | Path, infohash: str) -> Optional[bytes]:
    """The .torrent of a torrent being seeded, by infohash; None if unknown."""
    infohash = infohash.lower()
    if not _INFOHASH_RE.match(infohash):
        return None
    seeding_dir = Path(seeding_dir)
    try:
        cid = (seeding_dir / BY_INFOHASH / infohash).read_text().strip()
    except OSError:
        return None
    if not _CID_RE.match(cid):
        return None
    try:
        return (seeding_dir / cid / "torrent.dat").read_bytes()
    except OSError:
        return None


def write_status(seeding_dir: str | Path, status: dict) -> None:
    """Write the seeder's status, replacing the old one in one step."""
    path = Path(seeding_dir) / STATUS_FILE
    tmp = path.with_name(f"{STATUS_FILE}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps({**status, "written_at": time.time()}))
    os.replace(tmp, path)


def read_status(seeding_dir: str | Path, now: Optional[float] = None) -> dict:
    """What the seeder last said it was doing, and whether it's still saying it."""
    now = time.time() if now is None else now
    try:
        status = json.loads((Path(seeding_dir) / STATUS_FILE).read_text())
    except (OSError, ValueError):
        return {"running": False, "reason": "the seeder has written no status"}
    age = now - float(status.get("written_at", 0))
    status["status_age_seconds"] = round(age)
    if age > STATUS_STALE_SECONDS:
        status["running"] = False
        status["reason"] = f"the seeder's status is {round(age)}s old"
    return status
