"""Tests for waveform peaks (Rabbithole's timeline editor)."""

import array
import base64
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.routes.peaks import router
from app.services import peaks

CID = "QmbKXEfZckUWiHeMgXQB8faPQfNCjCYhrg7miXNL2dmNfS"


def pcm(values):
    return array.array("h", values).tobytes()


def test_compute_peaks_takes_loudest_either_sign_and_scales_to_255():
    # 4 samples per slice at rate 8, 2 per second.
    out = peaks.compute_peaks(pcm([0, 100, -400, 50, 10, 0, 0, 200]), 8, 2)
    assert list(out) == [255, round(200 * 255 / 400)]


def test_compute_peaks_silence_and_ragged_tail():
    assert list(peaks.compute_peaks(pcm([0, 0, 0, 0, 0]), 4, 2)) == [0, 0, 0]


def test_valid_cid():
    assert peaks.valid_cid(CID)
    assert peaks.valid_cid("bafybeigdyrzt5sfp7udm7hu76uh7y26nf3efuylqabf3oclgtqy55fbzdi")
    assert not peaks.valid_cid("../../etc/passwd")
    assert not peaks.valid_cid("Qmshort")


@pytest.fixture
def client(tmp_path, monkeypatch):
    settings = Settings(staging_dir=str(tmp_path), api_key="x", authorized_wallets="")
    monkeypatch.setattr(peaks, "get_settings", lambda: settings)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app), tmp_path


def test_rejects_non_cid(client):
    c, _ = client
    assert c.get("/peaks/not-a-cid.json").status_code == 400


def test_refuses_content_not_held_here(client, monkeypatch):
    c, _ = client

    async def not_local(cid):
        return False

    async def must_not_decode(cid):
        raise AssertionError("decoded content that is not local")

    monkeypatch.setattr(peaks, "_is_local", not_local)
    monkeypatch.setattr(peaks, "_decode", must_not_decode)
    assert c.get(f"/peaks/{CID}.json").status_code == 404


def test_decodes_once_then_serves_from_cache(client, monkeypatch):
    c, tmp = client
    calls = []

    async def local(cid):
        return True

    async def decode(cid):
        calls.append(cid)
        return pcm([0, 8000] * 4000)  # one second at 8 kHz

    monkeypatch.setattr(peaks, "_is_local", local)
    monkeypatch.setattr(peaks, "_decode", decode)

    r = c.get(f"/peaks/{CID}.json")
    assert r.status_code == 200
    body = r.json()
    assert body["per_second"] == 50
    assert body["duration"] == 1.0
    assert len(base64.b64decode(body["peaks"])) == 50
    assert r.headers["access-control-allow-origin"] == "*"
    assert "immutable" in r.headers["cache-control"]

    assert c.get(f"/peaks/{CID}.json").json() == body
    assert calls == [CID]
    assert json.loads((tmp / "peaks" / f"{CID}.json").read_text()) == body


def test_undecodable_is_502(client, monkeypatch):
    c, _ = client

    async def local(cid):
        return True

    async def decode(cid):
        return None

    monkeypatch.setattr(peaks, "_is_local", local)
    monkeypatch.setattr(peaks, "_decode", decode)
    assert c.get(f"/peaks/{CID}.json").status_code == 502
