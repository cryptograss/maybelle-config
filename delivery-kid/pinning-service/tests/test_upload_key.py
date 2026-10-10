"""The upload-only key: it signs upload tokens and nothing else.

magenta holds it (memory-lane services/delivery_kid.py), so a video sent from
a Mood carries the authority to upload and no more. Finalizing stays with the
wiki's finalize-release right, whose tokens only the main key can sign.
"""

import time

from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient

from app.auth import create_upload_token, require_auth, require_finalize_auth, verify_upload_token
from app.config import Settings, get_settings

MAIN, UPLOAD = "main-secret", "upload-only-secret"


def settings(**over) -> Settings:
    return Settings(**{"api_key": MAIN, "upload_key": UPLOAD, "authorized_wallets": "", **over})


def headers(key: str, action: str, user: str = "JMyles", age_s: float = 0) -> dict:
    stamp = int((time.time() - age_s) * 1000)
    return {"X-Upload-Token": create_upload_token(key, user, stamp, action=action),
            "X-Upload-User": user, "X-Upload-Timestamp": str(stamp)}


def client(s: Settings) -> TestClient:
    app = FastAPI()

    @app.post("/upload")
    async def upload(identity: str = Depends(require_auth)):
        return {"as": identity}

    @app.post("/finalize")
    async def finalize(identity: str = Depends(require_finalize_auth)):
        return {"as": identity}

    app.dependency_overrides[get_settings] = lambda: s
    return TestClient(app)


def test_an_upload_key_token_uploads():
    answer = client(settings()).post("/upload", headers=headers(UPLOAD, "upload"))
    assert answer.status_code == 200
    assert answer.json() == {"as": "wiki:JMyles"}


def test_the_upload_key_cannot_finalize_however_it_signs():
    c = client(settings())
    assert c.post("/finalize", headers=headers(UPLOAD, "finalize")).status_code == 401
    assert c.post("/finalize", headers=headers(UPLOAD, "upload")).status_code == 401
    assert c.post("/upload", headers=headers(UPLOAD, "finalize")).status_code == 401  # nor pass as one
    assert c.post("/finalize", headers={"X-API-Key": UPLOAD}).status_code == 401
    assert c.post("/upload", headers={"X-API-Key": UPLOAD}).status_code == 401


def test_the_main_key_works_as_before():
    c = client(settings())
    assert c.post("/upload", headers=headers(MAIN, "upload")).status_code == 200
    assert c.post("/finalize", headers=headers(MAIN, "finalize")).status_code == 200
    assert c.post("/upload", headers=headers(MAIN, "upload", age_s=20 * 24 * 3600)).status_code == 200  # 30 days


def test_upload_key_tokens_live_a_day():
    s = settings()
    stale = headers(UPLOAD, "upload", age_s=2 * 24 * 3600)
    assert not verify_upload_token(stale["X-Upload-Token"], "JMyles", int(stale["X-Upload-Timestamp"]), s)
    fresh = headers(UPLOAD, "upload", age_s=3600)
    assert verify_upload_token(fresh["X-Upload-Token"], "JMyles", int(fresh["X-Upload-Timestamp"]), s)


def test_no_upload_key_set_means_none_accepted():
    s = settings(upload_key="")
    h = headers(UPLOAD, "upload")
    assert client(s).post("/upload", headers=h).status_code == 401
    # and an empty key never signs anything into validity
    h = headers("", "upload")
    assert client(s).post("/upload", headers=h).status_code == 401
