"""Pinning and unpinning by bare CID take finalize auth, not any upload token.

An upload token is what the wiki hands every signed-in account on every page,
and what magenta mints for anyone sending a video. Until this, it was enough
to unpin any release from the local node and Pinata.
"""

import time
from types import SimpleNamespace
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import create_upload_token
from app.config import Settings, get_settings
from app.routes.albums import router

KEY = "test-secret"
CID = "QmVYU5wNxPMUMjiDJAuYuKYeQPNdANnf8weFNrY2u4JQ1i"


def make_client() -> TestClient:
    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_settings] = lambda: Settings(api_key=KEY, authorized_wallets="")
    return TestClient(test_app)


def hmac_headers(action: str, user: str = "JMyles") -> dict:
    stamp = int(time.time() * 1000)
    return {"X-Upload-Token": create_upload_token(KEY, user, stamp, action=action),
            "X-Upload-User": user, "X-Upload-Timestamp": str(stamp)}


def unpinned():
    return SimpleNamespace(success=True, error=None, local_unpinned=True, pinata_unpinned=False)


def test_an_upload_token_cannot_unpin_or_pin():
    client = make_client()
    with mock.patch("app.routes.albums.ipfs.unpin", new=mock.AsyncMock(return_value=unpinned())) as unpin, \
            mock.patch("app.routes.albums.ipfs.pin_cid", new=mock.AsyncMock()) as pin:
        assert client.delete(f"/unpin/{CID}", headers=hmac_headers("upload")).status_code == 401
        assert client.post(f"/pin/{CID}", headers=hmac_headers("upload")).status_code == 401
        assert client.delete(f"/unpin/{CID}").status_code in (401, 403)  # nothing at all
    unpin.assert_not_called()
    pin.assert_not_called()


def test_a_finalize_token_or_the_api_key_still_can():
    client = make_client()
    with mock.patch("app.routes.albums.ipfs.unpin", new=mock.AsyncMock(return_value=unpinned())) as unpin:
        assert client.delete(f"/unpin/{CID}", headers=hmac_headers("finalize")).status_code == 200
        assert client.delete(f"/unpin/{CID}", headers={"X-API-Key": KEY}).status_code == 200
    assert unpin.await_count == 2
