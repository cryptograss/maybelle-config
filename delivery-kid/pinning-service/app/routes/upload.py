"""
A draft's file, sent in pieces that can be picked up where they stopped.

POST /draft-content takes a whole upload as one request. On a phone that is
one 15-minute request for a 940 MB video: a change of network, a locked
screen or a restart of this service loses all of it, and the sender starts
again from nothing (Justin, 19 Nov 2026: "The connection dropped before it
was all sent", most of the time).

Here the file arrives as raw bytes appended to <name>.part in the draft's
upload directory, and what is on disk is the truth:

    POST /draft-content/init                      a draft id (routes/content.py)
    GET  /draft-content/{id}/upload               how much of it is here
    POST /draft-content/{id}/upload?name=&size=&offset=
                                                  bytes from `offset`, appended
    POST /draft-content/{id}/upload/complete      all here: analyse it

A request that dies partway keeps what it delivered: the sender asks how
much is here and carries on from that byte. Nothing is spooled to a
temporary file first, so a file is written once, not twice. Since the state
is the file itself, an upload also carries on across a restart of this
service.

One file per draft, which is what a video sent from a Mood is.
"""

import asyncio
import json
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from starlette.requests import ClientDisconnect

from ..auth import require_auth
from ..config import get_settings, get_commit, Settings
from ..models.content import ContentDraftResponse, ContentDraftState
from ..services.fsutil import safe_rmtree
from . import content

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/draft-content", tags=["content"])

# Bytes are gathered to this much before each write, so the event loop is
# left for a write (to the Storage Box, in a thread) once per megabyte rather
# than once per packet.
WRITE_BYTES = 1024 * 1024

SIDECAR = "upload.json"
CAN_RECEIVE = ("awaiting_upload", "uploading", "upload_failed")


class _Receiving:
    """Who is writing a draft's file just now.

    A sender whose connection went quiet without closing can sit in a request
    for minutes. When the same sender comes back (asking how much is here, or
    sending from there) the newcomer takes over: `holder` changes, and the old
    request, which checks `holder` under `lock` before every write, never
    writes again. So two requests can never both append to one file.
    """

    def __init__(self):
        self.lock = asyncio.Lock()
        self.holder: object | None = None


_receiving: dict[str, _Receiving] = {}


def _slot(draft_id: str) -> _Receiving:
    return _receiving.setdefault(draft_id, _Receiving())


def safe_name(name: str) -> str:
    try:
        return content.safe_upload_name(name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


def _own_draft(draft_id: str, identity: str, settings: Settings) -> tuple[Path, ContentDraftState]:
    try:
        uuid.UUID(draft_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Content draft not found")
    draft_dir = content.get_draft_dir(Path(settings.staging_dir), draft_id)
    state = content.load_draft_state(draft_dir)
    if state is None:
        raise HTTPException(status_code=404, detail="Content draft not found")
    if state.uploaded_by.lower() != identity.lower():
        raise HTTPException(status_code=403, detail="Not your draft")
    return draft_dir, state


def _sidecar(draft_dir: Path) -> dict | None:
    try:
        return json.loads((draft_dir / SIDECAR).read_text())
    except (OSError, ValueError):
        return None


def _part(draft_dir: Path, name: str) -> Path:
    return draft_dir / "upload" / (name + ".part")


def _received(draft_dir: Path, sidecar: dict | None) -> int:
    if not sidecar:
        return 0
    try:
        return _part(draft_dir, sidecar["name"]).stat().st_size
    except OSError:
        return 0


def _answer(state: ContentDraftState) -> ContentDraftResponse:
    return ContentDraftResponse(
        draft_id=state.draft_id, files=state.files, commit=get_commit(), status=state.status,
        upload_log=state.upload_log, preview_status=state.preview_status)


@router.get("/{draft_id}/upload")
async def upload_status(
    draft_id: str,
    identity: str = Depends(require_auth),
    settings: Settings = Depends(get_settings),
):
    """How much of this draft's file is here: {name, size, received, status}.

    Asking takes the file over from any request still writing it, so the
    answer stays true until the asker sends more.
    """
    draft_dir, state = _own_draft(draft_id, identity, settings)
    slot = _slot(draft_id)
    slot.holder = object()
    async with slot.lock:
        sidecar = _sidecar(draft_dir)
        return {
            "resumable": True,
            "name": sidecar["name"] if sidecar else None,
            "size": sidecar["size"] if sidecar else None,
            "received": _received(draft_dir, sidecar),
            "status": state.status,
        }


@router.post("/{draft_id}/upload")
async def upload_bytes(
    draft_id: str,
    request: Request,
    name: str = Query(..., description="The file's name"),
    size: int = Query(..., ge=1, description="The whole file's size in bytes"),
    offset: int = Query(..., ge=0, description="Where in the file this request's bytes begin"),
    identity: str = Depends(require_auth),
    settings: Settings = Depends(get_settings),
):
    """Append this request's body to the draft's file, from `offset`.

    409 with {received} when `offset` isn't where the file ends: the sender
    carries on from `received` instead. Bytes that arrive before a
    connection dies are kept.
    """
    draft_dir, state = _own_draft(draft_id, identity, settings)
    name = safe_name(name)
    if state.status not in CAN_RECEIVE:
        raise HTTPException(status_code=409, detail={
            "error": f"This draft is {state.status}; it takes no more bytes", "status": state.status})
    if size > settings.max_file_size_mb * 1024 * 1024:
        raise HTTPException(status_code=413,
                            detail=f"Larger than the {settings.max_file_size_mb}MB limit")

    slot = _slot(draft_id)
    me = object()
    slot.holder = me
    upload_dir = draft_dir / "upload"

    async with slot.lock:
        if slot.holder is not me:
            raise HTTPException(status_code=409, detail={"error": "superseded"})
        sidecar = _sidecar(draft_dir)
        if sidecar and (sidecar["name"] != name or sidecar["size"] != size):
            if offset != 0:
                raise HTTPException(status_code=409, detail={
                    "error": "A different file is part-way here", "received": 0,
                    "name": sidecar["name"], "size": sidecar["size"]})
            # From the top, with another file: the earlier attempt is replaced.
            _part(draft_dir, sidecar["name"]).unlink(missing_ok=True)
            sidecar = None
        received = _received(draft_dir, sidecar)
        if offset != received:
            raise HTTPException(status_code=409, detail={"error": "Not where the file ends", "received": received})
        if sidecar is None:
            # A fresh start: whatever an earlier attempt left is cleared, as
            # POST /draft-content clears it, so only this file is analysed.
            if upload_dir.exists():
                safe_rmtree(upload_dir)
            upload_dir.mkdir(parents=True, exist_ok=True)
            (draft_dir / SIDECAR).write_text(json.dumps({"name": name, "size": size}))
            content._append_upload_log(state, "upload-start", f"Receiving {name} ({size} bytes), in pieces.")
        elif state.status == "upload_failed":
            content._append_upload_log(state, "reupload-start", f"Carrying on with {name} from byte {received}.")
        if sidecar is None or state.status != "uploading":
            state.status = "uploading"
            content.save_draft_state(draft_dir, state)

    part = _part(draft_dir, name)
    buffer = bytearray()

    async def write() -> int | None:
        """Append what's gathered, unless another request has the file now. The file's size after, or None."""
        nonlocal received
        async with slot.lock:
            if slot.holder is not me:
                return None
            if received + len(buffer) > size:
                raise HTTPException(status_code=400, detail={
                    "error": "More bytes than the file's size", "received": received})
            await asyncio.to_thread(_append, part, bytes(buffer))
            received += len(buffer)
            buffer.clear()
            return received

    try:
        async for piece in request.stream():
            buffer.extend(piece)
            if len(buffer) >= WRITE_BYTES and await write() is None:
                raise HTTPException(status_code=409, detail={"error": "superseded"})
    except ClientDisconnect:
        # Keep what came before the connection went: it is asked for next.
        if buffer:
            await write()
        logger.info("[upload:%s] connection went at byte %d of %d", draft_id[:8], received, size)
        return {"received": received, "size": size}
    if buffer and await write() is None:
        raise HTTPException(status_code=409, detail={"error": "superseded"})
    return {"received": received, "size": size}


def _append(part: Path, data: bytes) -> None:
    with open(part, "ab") as f:
        f.write(data)


@router.post("/{draft_id}/upload/complete", response_model=ContentDraftResponse)
async def upload_complete(
    draft_id: str,
    identity: str = Depends(require_auth),
    settings: Settings = Depends(get_settings),
):
    """Every byte is here: give the file its name and analyse it, as POST /draft-content does.

    Asked again after it worked (the answer got lost on the way back), it
    gives the same answer.
    """
    draft_dir, state = _own_draft(draft_id, identity, settings)
    if state.status == "uploaded":
        return _answer(state)

    slot = _slot(draft_id)
    slot.holder = object()
    async with slot.lock:
        sidecar = _sidecar(draft_dir)
        if not sidecar:
            raise HTTPException(status_code=409, detail={"error": "Nothing has been sent yet", "received": 0})
        received = _received(draft_dir, sidecar)
        if received != sidecar["size"]:
            raise HTTPException(status_code=409, detail={"error": "Not all of it is here", "received": received})

        upload_dir = draft_dir / "upload"
        name = sidecar["name"]
        _part(draft_dir, name).rename(upload_dir / name)
        (draft_dir / SIDECAR).unlink(missing_ok=True)
        content._append_upload_log(state, "received", f"Saved {name} ({received} bytes)")
        content.save_draft_state(draft_dir, state)

        def fail(http_status: int, message: str) -> HTTPException:
            state.status = "upload_failed"
            content._append_upload_log(state, "error", message, error=message)
            try:
                content.save_draft_state(draft_dir, state)
            except Exception:
                logger.exception("[upload:%s] Failed to persist upload_log on error", draft_id[:8])
            content._fire_diagnostics_snapshot(state)
            return HTTPException(status_code=http_status, detail=message)

        try:
            await content._analyze_and_mark_uploaded(draft_id, draft_dir, upload_dir, state, settings)
        except content.NoUsableMediaError as e:
            raise fail(400, str(e))
        except Exception as e:
            logger.exception("[upload:%s] Analysis failed", draft_id[:8])
            raise fail(500, f"Upload error: {type(e).__name__}: {str(e) or '(no message)'}")
    _receiving.pop(draft_id, None)
    return _answer(state)
