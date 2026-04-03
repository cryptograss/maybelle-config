"""Tests for the staging file serving endpoint."""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.routes.staging import router


def make_settings(staging_dir: str) -> Settings:
    """Create test settings pointing at a tmp staging dir."""
    return Settings(
        staging_dir=staging_dir,
        api_key="test-secret",
        authorized_wallets="",
    )


def make_client(settings: Settings) -> TestClient:
    """Create a test client with settings overridden via FastAPI DI."""
    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(test_app)


@pytest.fixture
def staging_dir(tmp_path):
    """Create a staging directory with a test draft and file."""
    draft_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    upload_dir = tmp_path / "drafts" / draft_id / "upload"
    upload_dir.mkdir(parents=True)

    test_file = upload_dir / "test-video.mp4"
    test_file.write_bytes(b"\x00" * 1024)

    return tmp_path, draft_id


def _auth_headers(api_key: str = "test-secret") -> dict:
    return {"X-API-Key": api_key}


class TestStagingEndpoint:

    def test_serves_existing_file(self, staging_dir):
        tmp_path, draft_id = staging_dir
        client = make_client(make_settings(str(tmp_path)))
        resp = client.get(
            f"/staging/drafts/{draft_id}/test-video.mp4",
            headers=_auth_headers(),
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "video/mp4"
        assert len(resp.content) == 1024

    def test_404_for_missing_file(self, staging_dir):
        tmp_path, draft_id = staging_dir
        client = make_client(make_settings(str(tmp_path)))
        resp = client.get(
            f"/staging/drafts/{draft_id}/nonexistent.mp4",
            headers=_auth_headers(),
        )
        assert resp.status_code == 404

    def test_404_for_missing_draft(self, staging_dir):
        tmp_path, _ = staging_dir
        client = make_client(make_settings(str(tmp_path)))
        resp = client.get(
            "/staging/drafts/00000000-0000-0000-0000-000000000000/file.mp4",
            headers=_auth_headers(),
        )
        assert resp.status_code == 404

    def test_requires_auth(self, staging_dir):
        tmp_path, draft_id = staging_dir
        client = make_client(make_settings(str(tmp_path)))
        resp = client.get(
            f"/staging/drafts/{draft_id}/test-video.mp4",
        )
        assert resp.status_code == 401

    def test_rejects_bad_api_key(self, staging_dir):
        tmp_path, draft_id = staging_dir
        client = make_client(make_settings(str(tmp_path)))
        resp = client.get(
            f"/staging/drafts/{draft_id}/test-video.mp4",
            headers=_auth_headers("wrong-key"),
        )
        assert resp.status_code == 401

    def test_query_param_auth(self, staging_dir):
        """Test that <video src="...?token=&user=&timestamp="> auth works."""
        import hmac, hashlib, time

        tmp_path, draft_id = staging_dir
        settings = make_settings(str(tmp_path))
        client = make_client(settings)

        username = "TestUser"
        ts = int(time.time() * 1000)
        token = hmac.new(
            settings.api_key.encode(),
            f"upload:{username}:{ts}".encode(),
            hashlib.sha256,
        ).hexdigest()

        resp = client.get(
            f"/staging/drafts/{draft_id}/test-video.mp4"
            f"?token={token}&user={username}&timestamp={ts}",
        )
        assert resp.status_code == 200
        assert len(resp.content) == 1024

    def test_query_param_auth_rejects_bad_token(self, staging_dir):
        tmp_path, draft_id = staging_dir
        client = make_client(make_settings(str(tmp_path)))

        resp = client.get(
            f"/staging/drafts/{draft_id}/test-video.mp4"
            f"?token=badtoken&user=TestUser&timestamp=1234567890000",
        )
        assert resp.status_code == 401
