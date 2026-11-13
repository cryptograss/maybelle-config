"""Serve .torrent files, and the seeder's status.

The seeder runs in its own container. This reads what it leaves in the
seeding folder (services/seeding_store) and never talks to libtorrent.
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from ..config import Settings, get_settings
from ..services.seeding_store import read_status, torrent_file_by_infohash

router = APIRouter(prefix="/torrent", tags=["torrent"])


@router.get("/{infohash}.torrent")
async def get_torrent_file(infohash: str, settings: Settings = Depends(get_settings)):
    """Serve a .torrent file by infohash."""
    torrent_bytes = torrent_file_by_infohash(settings.seeding_dir, infohash)
    if not torrent_bytes:
        raise HTTPException(404, "Torrent not found")

    return Response(
        content=torrent_bytes,
        media_type="application/x-bittorrent",
        headers={"Content-Disposition": f'attachment; filename="{infohash.lower()}.torrent"'},
    )


@router.get("/status")
async def seeder_status(settings: Settings = Depends(get_settings)):
    """The seeder's status, as it last wrote it; "running" is false if that's stale."""
    return read_status(settings.seeding_dir)
