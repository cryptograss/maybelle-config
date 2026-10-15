"""Deleting a content draft: its uploader or a finalizer, and never mid-finalize.

The ReleaseDraft page's "Abandon — delete files" button calls this
(pickipedia#143), so the rules are the button's rules.
"""

import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import create_upload_token
from app.config import Settings, get_settings
from app.models.content import ContentDraftState
from app.routes import content
from app.services import finalize_jobs

KEY = "test-secret"
DRAFT = "6072d82c-d76c-4a6f-aca6-f23c4012d505"


def client(staging: Path) -> TestClient:
    app = FastAPI()
    app.include_router(content.router)
    app.dependency_overrides[get_settings] = lambda: Settings(api_key=KEY, staging_dir=str(staging),
                                                              authorized_wallets="")
    return TestClient(app)


def as_(user: str, action: str = "upload") -> dict:
    stamp = int(time.time() * 1000)
    return {"X-Upload-Token": create_upload_token(KEY, user, stamp, action=action),
            "X-Upload-User": user, "X-Upload-Timestamp": str(stamp)}


def staged(staging: Path, uploader: str = "wiki:SkymanJenkins") -> Path:
    draft_dir = staging / "drafts" / DRAFT
    (draft_dir / "upload").mkdir(parents=True)
    (draft_dir / "upload" / "station-inn.mov").write_bytes(b"\0" * 1024)
    content.save_draft_state(draft_dir, ContentDraftState(
        draft_id=DRAFT, uploaded_by=uploader, created_at=datetime.now(timezone.utc)))
    return draft_dir


def test_its_uploader_deletes_it(tmp_path, monkeypatch):
    monkeypatch.setattr(finalize_jobs, "_jobs", {})
    draft_dir = staged(tmp_path)
    assert client(tmp_path).delete(f"/draft-content/{DRAFT}", headers=as_("SkymanJenkins")).status_code == 200
    assert not draft_dir.exists()


def test_someone_else_cannot_but_a_finalizer_can(tmp_path, monkeypatch):
    monkeypatch.setattr(finalize_jobs, "_jobs", {})
    draft_dir = staged(tmp_path)
    c = client(tmp_path)
    assert c.delete(f"/draft-content/{DRAFT}", headers=as_("JMyles")).status_code == 403
    assert draft_dir.exists()
    assert c.delete(f"/draft-content/{DRAFT}", headers=as_("JMyles", "finalize")).status_code == 200
    assert not draft_dir.exists()


def test_not_while_it_is_being_finalized(tmp_path, monkeypatch):
    draft_dir = staged(tmp_path)
    monkeypatch.setattr(finalize_jobs, "_jobs", {DRAFT: finalize_jobs.Job(draft_id=DRAFT)})
    answer = client(tmp_path).delete(f"/draft-content/{DRAFT}", headers=as_("SkymanJenkins"))
    assert answer.status_code == 409
    assert draft_dir.exists()
    # Once that finalize is over, it may go.
    finalize_jobs._jobs[DRAFT].done = True
    finalize_jobs._jobs[DRAFT].finished_at = time.time()
    assert client(tmp_path).delete(f"/draft-content/{DRAFT}", headers=as_("SkymanJenkins")).status_code == 200
