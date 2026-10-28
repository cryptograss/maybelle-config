"""Waveform peaks for Rabbithole's timeline editor. See services/peaks.py."""

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from ..services import peaks

router = APIRouter(prefix="/peaks", tags=["peaks"])


@router.get("/{cid}.json")
async def get_peaks(cid: str):
    if not peaks.valid_cid(cid):
        raise HTTPException(400, "Not a CID")
    try:
        result = await peaks.get_peaks(cid)
    except peaks.NotLocal:
        raise HTTPException(404, "Not held on this node")
    if result is None:
        raise HTTPException(502, "Could not decode that file as audio")
    # A CID's content never changes, so neither do its peaks. Public and
    # readable from anywhere, like the gateway that serves the audio itself.
    return JSONResponse(result, headers={
        "Cache-Control": "public, max-age=31536000, immutable",
        "Access-Control-Allow-Origin": "*",
    })
