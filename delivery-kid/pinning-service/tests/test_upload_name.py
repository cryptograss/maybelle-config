"""An uploaded file's name can't leave its draft's upload directory.

POST /draft-content joined the sender's filename onto the upload directory
as it came, so "../../../x.mp4" was written three directories up: anywhere
the service can write, by anyone holding an upload token. Found on a local
run, 19 Nov 2026, while adding uploads in pieces.
"""

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import create_upload_token
from app.config import Settings, get_settings
from app.routes import content

KEY = "test-secret"


def as_(user: str = "JMyles") -> dict:
    stamp = int(time.time() * 1000)
    return {"X-Upload-Token": create_upload_token(KEY, user, stamp), "X-Upload-User": user,
            "X-Upload-Timestamp": str(stamp)}


def test_a_name_with_a_directory_on_it_lands_in_the_upload_directory(tmp_path):
    app = FastAPI()
    app.include_router(content.router)
    app.dependency_overrides[get_settings] = lambda: Settings(
        api_key=KEY, staging_dir=str(tmp_path), authorized_wallets="")

    # Not a video, so analysis refuses it (400): what matters is where it was written.
    TestClient(app).post("/draft-content", headers=as_(),
                         files=[("files", ("../../../escaped.mp4", b"not really a video", "video/mp4"))])

    assert not (tmp_path / "escaped.mp4").exists()
    assert [p.name for p in tmp_path.glob("drafts/*/upload/*")] == ["escaped.mp4"]


@pytest.mark.parametrize("name,bare", [
    ("station-inn.mov", "station-inn.mov"),
    ("../../../etc/station-inn.mov", "station-inn.mov"),
    ("C:\\Users\\sky\\station-inn.MOV", "station-inn.MOV"),
    ("/abs/olute.mp4", "olute.mp4"),
])
def test_the_bare_name_is_kept(name, bare):
    assert content.safe_upload_name(name) == bare


@pytest.mark.parametrize("name", ["", None, "..", ".mp4", "station-inn.sh", "station-inn", "a/b/"])
def test_names_that_are_refused(name):
    with pytest.raises(ValueError):
        content.safe_upload_name(name)
