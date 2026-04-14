"""Album and pin management routes."""

from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_auth, require_wallet_auth
from ..config import get_settings, Settings
from ..services import ipfs

router = APIRouter()


@router.get("/local-pins")
async def list_local_pins():
    """List all locally pinned CIDs. Public endpoint for build-time fetching."""
    cids = await ipfs.get_local_pins()
    # Return as objects with 'cid' property for arthel compatibility
    pins = [{"cid": cid} for cid in cids]
    return {"pins": pins, "count": len(pins), "node": "delivery-kid"}


@router.post("/pin/{cid}")
async def pin_cid(
    cid: str,
    identity: str = Depends(require_auth),
    settings: Settings = Depends(get_settings),
):
    """
    Pin a CID to the local IPFS node.

    Accepts API key, HMAC token, or wallet auth.
    """
    result = await ipfs.pin_cid(cid)

    if not result.success:
        raise HTTPException(
            status_code=500,
            detail=f"Pin failed: {result.error}"
        )

    return {
        "success": True,
        "cid": cid,
        "message": f"Pinned {cid}",
    }


@router.delete("/unpin/{cid}")
async def unpin_cid(
    cid: str,
    identity: str = Depends(require_auth),
):
    """
    Unpin a CID from both local IPFS and Pinata.

    Accepts API key, HMAC token, or wallet auth.
    """
    result = await ipfs.unpin(cid)

    if not result.success:
        raise HTTPException(
            status_code=500,
            detail=f"Unpin failed: {result.error}"
        )

    return {
        "success": True,
        "cid": cid,
        "local_unpinned": result.local_unpinned,
        "pinata_unpinned": result.pinata_unpinned,
        "message": f"Unpinned {cid}"
    }
