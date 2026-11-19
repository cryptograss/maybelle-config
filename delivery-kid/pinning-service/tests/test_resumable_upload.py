"""A draft's file sent in pieces, and picked up where it stopped.

Justin, 19 Nov 2026: a ~940 MB video from a phone kept failing with "The
connection dropped before it was all sent". It went up as one request, so a
dropped connection lost all of it. Here it arrives in pieces appended to a
.part file, and the sender asks how much is here and carries on.
"""

import asyncio
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import create_upload_token
from app.config import Settings, get_settings
from app.models.content import ContentFile
from app.routes import content, upload

KEY = "test-secret"
FILM = bytes(range(256)) * 4096  # 1 MiB, every byte position distinguishable enough
NAME = "station-inn.mov"


@pytest.fixture
def api(tmp_path, monkeypatch):
    """delivery-kid's upload routes over a temp staging dir, with ffprobe stood in for."""
    analysed = []

    async def analysed_instead(draft_id, draft_dir, upload_dir, state, settings):
        files = sorted(p for p in upload_dir.iterdir() if p.is_file())
        analysed.append([p.name for p in files])
        state.files = [ContentFile(original_filename=p.name, detected_title=p.stem, media_type="video",
                                   format="MOV", size_bytes=p.stat().st_size) for p in files]
        state.status = "uploaded"
        content.save_draft_state(draft_dir, state)
        return state.files

    monkeypatch.setattr(content, "_analyze_and_mark_uploaded", analysed_instead)
    monkeypatch.setattr(upload, "_receiving", {})
    app = FastAPI()
    app.include_router(content.router)
    app.include_router(upload.router)
    app.dependency_overrides[get_settings] = lambda: Settings(
        api_key=KEY, staging_dir=str(tmp_path), authorized_wallets="", max_file_size_mb=4)
    client = TestClient(app)
    client.analysed = analysed
    client.staging = tmp_path
    return client


def as_(user: str = "JMyles") -> dict:
    stamp = int(time.time() * 1000)
    return {"X-Upload-Token": create_upload_token(KEY, user, stamp), "X-Upload-User": user,
            "X-Upload-Timestamp": str(stamp)}


def new_draft(api) -> str:
    answer = api.post("/draft-content/init", headers=as_())
    assert answer.status_code == 200, answer.text
    return answer.json()["draft_id"]


def send(api, draft, data, offset, size=len(FILM), name=NAME, user="JMyles"):
    return api.post(f"/draft-content/{draft}/upload", params={"name": name, "size": size, "offset": offset},
                    content=data, headers=as_(user))


def part_of(api, draft) -> Path:
    return api.staging / "drafts" / draft / "upload" / (NAME + ".part")


def test_pieces_add_up_to_the_file_and_it_is_analysed_once(api):
    draft = new_draft(api)
    third = len(FILM) // 3
    assert send(api, draft, FILM[:third], 0).json() == {"received": third, "size": len(FILM)}
    assert send(api, draft, FILM[third:2 * third], third).json()["received"] == 2 * third
    assert send(api, draft, FILM[2 * third:], 2 * third).json()["received"] == len(FILM)

    done = api.post(f"/draft-content/{draft}/upload/complete", headers=as_())

    assert done.status_code == 200, done.text
    assert done.json()["status"] == "uploaded"
    assert done.json()["files"][0]["original_filename"] == NAME
    upload_dir = api.staging / "drafts" / draft / "upload"
    assert (upload_dir / NAME).read_bytes() == FILM
    assert [p.name for p in upload_dir.iterdir()] == [NAME]  # no .part left
    assert not (api.staging / "drafts" / draft / "upload.json").exists()
    assert api.analysed == [[NAME]]


def test_it_says_how_much_is_here_so_a_sender_can_carry_on(api):
    draft = new_draft(api)
    assert api.get(f"/draft-content/{draft}/upload", headers=as_()).json() == {
        "resumable": True, "name": None, "size": None, "received": 0, "status": "awaiting_upload"}

    send(api, draft, FILM[:300_000], 0)

    here = api.get(f"/draft-content/{draft}/upload", headers=as_()).json()
    assert here == {"resumable": True, "name": NAME, "size": len(FILM), "received": 300_000, "status": "uploading"}


def test_bytes_sent_from_the_wrong_place_are_refused_with_the_right_place(api):
    draft = new_draft(api)
    send(api, draft, FILM[:300_000], 0)

    ahead = send(api, draft, FILM[400_000:], 400_000)
    again = send(api, draft, FILM[:300_000], 0)  # its answer was lost; the sender sends it again

    assert ahead.status_code == 409 and ahead.json()["detail"]["received"] == 300_000
    assert again.status_code == 409 and again.json()["detail"]["received"] == 300_000
    assert part_of(api, draft).read_bytes() == FILM[:300_000]  # neither was written


def test_complete_before_all_of_it_is_here_says_how_much_is(api):
    draft = new_draft(api)
    send(api, draft, FILM[:300_000], 0)

    early = api.post(f"/draft-content/{draft}/upload/complete", headers=as_())

    assert early.status_code == 409 and early.json()["detail"]["received"] == 300_000
    assert api.analysed == []
    assert part_of(api, draft).exists()


def test_complete_asked_twice_answers_the_same(api):
    draft = new_draft(api)
    send(api, draft, FILM, 0)
    first = api.post(f"/draft-content/{draft}/upload/complete", headers=as_())

    second = api.post(f"/draft-content/{draft}/upload/complete", headers=as_())

    assert second.status_code == 200
    assert second.json()["files"] == first.json()["files"]
    assert api.analysed == [[NAME]]  # not analysed again


def test_more_bytes_than_the_file_is_refused_and_too_big_is_refused_before_any_byte(api):
    draft = new_draft(api)
    too_many = send(api, draft, FILM + b"extra", 0)
    assert too_many.status_code == 400
    assert not part_of(api, draft).exists() or part_of(api, draft).stat().st_size <= len(FILM)

    huge = send(api, new_draft(api), b"x", 0, size=5 * 1024 * 1024)  # the limit here is 4 MB
    assert huge.status_code == 413


def test_only_its_uploader_sends_asks_or_completes(api):
    draft = new_draft(api)
    send(api, draft, FILM[:1000], 0)
    other = as_("SomeoneElse")

    assert send(api, draft, FILM[1000:], 1000, user="SomeoneElse").status_code == 403
    assert api.get(f"/draft-content/{draft}/upload", headers=other).status_code == 403
    assert api.post(f"/draft-content/{draft}/upload/complete", headers=other).status_code == 403
    assert api.get(f"/draft-content/{draft}/upload").status_code == 401  # and nobody without a token
    assert part_of(api, draft).stat().st_size == 1000


def test_a_name_cannot_leave_the_upload_directory_or_be_a_kind_we_do_not_take(api):
    draft = new_draft(api)

    escaped = send(api, draft, FILM, 0, name="../../../etc/station-inn.mov")
    script = send(api, new_draft(api), FILM, 0, name="station-inn.sh")

    assert escaped.status_code == 200
    assert (api.staging / "drafts" / draft / "upload" / "station-inn.mov.part").exists()
    assert not (api.staging / "etc").exists()
    assert script.status_code == 400


def test_a_draft_that_is_not_a_draft_id_is_not_found(api):
    assert api.get("/draft-content/..%2f..%2fetc/upload", headers=as_()).status_code == 404
    assert api.get("/draft-content/not-a-uuid/upload", headers=as_()).status_code == 404


def test_starting_over_with_another_file_replaces_the_first(api):
    draft = new_draft(api)
    send(api, draft, FILM[:300_000], 0)

    midway = send(api, draft, b"other", 300_000, size=10, name="other.mp4")  # not from its top: refused
    restart = send(api, draft, b"0123456789", 0, size=10, name="other.mp4")
    api.post(f"/draft-content/{draft}/upload/complete", headers=as_())

    assert midway.status_code == 409
    assert restart.status_code == 200
    assert api.analysed == [["other.mp4"]]
    assert not part_of(api, draft).exists()


def test_a_finished_draft_takes_no_more_bytes(api):
    draft = new_draft(api)
    send(api, draft, FILM, 0)
    api.post(f"/draft-content/{draft}/upload/complete", headers=as_())

    more = send(api, draft, FILM, 0)

    assert more.status_code == 409
    assert (api.staging / "drafts" / draft / "upload" / NAME).read_bytes() == FILM


def test_a_request_gone_quiet_never_writes_once_another_has_the_file(tmp_path):
    """A stalled connection's request wakes after the sender has come back and carried on:
    its late bytes must not land on top of the newcomer's."""
    part = tmp_path / "f.part"
    slot = upload._Receiving()

    async def scenario():
        old, new = object(), object()
        slot.holder = old
        stalled = asyncio.Event()

        async def old_request():
            await stalled.wait()                   # ...minutes later, its bytes arrive
            async with slot.lock:
                if slot.holder is not old:
                    return "superseded"
                upload._append(part, b"LATE")
                return "wrote"

        waiting = asyncio.create_task(old_request())
        slot.holder = new                          # the sender comes back
        async with slot.lock:
            upload._append(part, b"fresh")
        stalled.set()
        return await waiting

    assert asyncio.run(scenario()) == "superseded"
    assert part.read_bytes() == b"fresh"
