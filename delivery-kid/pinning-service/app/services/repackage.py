"""Republish an already-pinned video release with its init segments fixed.

Releases encoded by delivery-kid's ffmpeg 7.1 carry an empty sdtp box in
every video rendition's init segment, and iPhone Safari refuses those
streams (see transcode._drop_empty_sdtp). New encodes are fixed as they are
made; this fixes the ones already published.

Everything happens inside the local IPFS node, through its MFS: the release
is copied by reference, only its init segments (a kilobyte or two each) are
rewritten, and the result is pinned. No segment is downloaded, re-added or
re-encoded, so a 1.4 GB release costs about as much as a small one.

The new directory has a new CID. The old one stays pinned: moving the
Release page to the new CID, and unpinning the old, are separate steps.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Optional

import httpx

from .transcode import drop_empty_sdtp_bytes

logger = logging.getLogger(__name__)

MFS_ROOT = "/repackage"


@dataclass
class RepackageResult:
    success: bool
    old_cid: str
    new_cid: Optional[str] = None
    fixed: list[str] = field(default_factory=list)
    error: Optional[str] = None


class _Kubo:
    def __init__(self, client: httpx.AsyncClient, api_url: str):
        self.client = client
        self.api = f"{api_url}/api/v0"

    async def call(self, command: str, files=None, **params) -> httpx.Response:
        response = await self.client.post(f"{self.api}/{command}", params=params, files=files)
        if response.status_code != 200:
            raise RuntimeError(f"{command}: {response.status_code} {response.text[:200]}")
        return response

    async def ls(self, path: str) -> list[dict]:
        data = (await self.call("ls", arg=path)).json()
        objects = data.get("Objects") or [{}]
        return objects[0].get("Links") or []


async def repackage_release(cid: str, api_url: str, *, dry_run: bool = False,
                            transport: Optional[httpx.AsyncBaseTransport] = None
                            ) -> RepackageResult:
    """Copy the release at `cid` with every video init segment fixed, and pin it.

    If no init segment needs fixing, nothing is written and new_cid is None.
    With dry_run, reports what would be fixed and writes nothing.
    """
    async with httpx.AsyncClient(timeout=120.0, transport=transport) as client:
        kubo = _Kubo(client, api_url)
        try:
            # Which init segments need the fix. Each rendition lives in a
            # stream_<name>/ directory, and names its init segment in its
            # playlist; reading the directory is enough.
            changes: dict[str, bytes] = {}
            for entry in await kubo.ls(f"/ipfs/{cid}"):
                if entry.get("Type") != 1 or not entry["Name"].startswith("stream_"):
                    continue
                for child in await kubo.ls(f"/ipfs/{cid}/{entry['Name']}"):
                    name = child["Name"]
                    if not (name.startswith("init") and name.endswith(".mp4")):
                        continue
                    path = f"{entry['Name']}/{name}"
                    original = (await kubo.call("cat", arg=f"/ipfs/{cid}/{path}")).content
                    fixed = drop_empty_sdtp_bytes(original)
                    if fixed is not None:
                        changes[path] = fixed

            if not changes or dry_run:
                return RepackageResult(success=True, old_cid=cid, fixed=sorted(changes))

            # metadata.json records where this came from, so the new release
            # carries its own provenance. No timestamp: the same release
            # always repackages to the same CID, so running it twice costs
            # nothing and pins nothing new.
            try:
                metadata = json.loads((await kubo.call("cat", arg=f"/ipfs/{cid}/metadata.json")).content)
            except (RuntimeError, ValueError):
                metadata = None
            if isinstance(metadata, dict):
                metadata["repackaged_from"] = cid
                metadata["repackaged_because"] = (
                    "empty sdtp box removed from video init segments; "
                    "iPhone Safari would not play the stream with it")
                changes["metadata.json"] = json.dumps(metadata, indent=2).encode()

            work = f"{MFS_ROOT}/{cid}"
            await kubo.call("files/mkdir", arg=MFS_ROOT, parents="true")
            try:
                await kubo.call("files/rm", arg=work, recursive="true", force="true")
            except RuntimeError:
                pass  # nothing left over from an earlier attempt
            await kubo.call("files/cp", arg=[f"/ipfs/{cid}", work])
            for path, data in sorted(changes.items()):
                # The part's filename must be bare: kubo reads a slash in it
                # as a directory, and then finds no file to write.
                await kubo.call("files/write", files={"file": ("data", data)},
                                arg=f"{work}/{path}", create="true", truncate="true")
            new_cid = (await kubo.call("files/stat", arg=work, hash="true")).json()["Hash"]
            await kubo.call("pin/add", arg=new_cid)
            # The pin holds the blocks now; the MFS copy is only scaffolding.
            await kubo.call("files/rm", arg=work, recursive="true", force="true")

            fixed = sorted(p for p in changes if p != "metadata.json")
            logger.info("[repackage] %s -> %s (%d init segments fixed)", cid, new_cid, len(fixed))
            return RepackageResult(success=True, old_cid=cid, new_cid=new_cid, fixed=fixed)
        except Exception as e:
            logger.exception("[repackage] %s failed", cid)
            return RepackageResult(success=False, old_cid=cid, error=str(e))
