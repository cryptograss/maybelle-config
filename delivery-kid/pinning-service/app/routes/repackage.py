"""
Republish a video release so iPhones play it.

POST /enrich/repackage removes the empty sdtp box from a published release's
video init segments, inside the IPFS node, and pins the result under its
new CID (services/repackage.py). For the releases encoded before
transcode._drop_empty_sdtp existed.

Requires API key auth (X-API-Key header).
"""

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..auth import require_auth
from ..config import get_settings, Settings
from ..services.repackage import repackage_release

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/enrich", tags=["enrich"])


class RepackageRequest(BaseModel):
    # A CID and nothing else: it becomes a path in IPFS (/ipfs/<cid>/…) and a working directory removed
    # recursively (/repackage/<cid>), so no slash, dot or space may reach either (re's review, 10 Nov).
    cid: str = Field(pattern=r"^(Qm[1-9A-HJ-NP-Za-km-z]{44}|b[a-z2-7]{20,120})$")
    dry_run: bool = False


class RepackageResponse(BaseModel):
    success: bool
    old_cid: str
    new_cid: str | None = None
    fixed: list[str] = []
    error: str | None = None


@router.post("/repackage", response_model=RepackageResponse)
async def repackage(
    req: RepackageRequest,
    identity: str = Depends(require_auth),
    settings: Settings = Depends(get_settings),
):
    """
    Pin a copy of the release with its video init segments fixed, and
    return the copy's CID. The old CID stays pinned.

    new_cid is null when the release needed no fix. With dry_run, lists the
    init segments that would be fixed and changes nothing.
    """
    result = await repackage_release(req.cid, settings.ipfs_api_url, dry_run=req.dry_run)
    logger.info("[repackage] %s by %s: %s", req.cid, identity,
                result.new_cid or result.error or "no change")
    return RepackageResponse(**result.__dict__)
