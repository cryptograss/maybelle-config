"""Finalize runs as delivery-kid's own job: it outlives whoever was watching,
jobs take turns, and a restart leaves no draft "finalizing" for ever."""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.models.content import ContentDraftState
from app.routes import content
from app.services import finalize_jobs


@pytest.fixture(autouse=True)
def fresh_jobs(monkeypatch):
    monkeypatch.setattr(finalize_jobs, "_jobs", {})
    monkeypatch.setattr(finalize_jobs, "_waiting", [])
    monkeypatch.setattr(finalize_jobs, "_running", None)
    monkeypatch.setattr(finalize_jobs, "_turn", asyncio.Lock())


def frame(event, **data):
    return {"event": event, "data": json.dumps(data)}


async def queued(ahead):
    return frame("progress", stage="queued", message=f"{ahead} ahead")


@pytest.mark.asyncio
async def test_a_job_finishes_after_its_watcher_goes_away():
    gate = asyncio.Event()
    finished = []

    async def steps():
        yield frame("progress", stage="transcode", progress=10)
        await gate.wait()
        finished.append(True)
        yield frame("complete", cid="bafy")

    job = finalize_jobs.start("d1", steps, queued)
    watcher = finalize_jobs.follow(job)
    first = await watcher.__anext__()
    assert json.loads(first["data"])["stage"] == "transcode"
    await watcher.aclose()  # the tab closed

    gate.set()
    await asyncio.wait_for(job.task, 1)
    assert finished == [True]
    assert job.done and job.events[-1]["event"] == "complete"

    # A late watcher (the page reloaded) gets the whole story.
    seen = [f async for f in finalize_jobs.follow(job)]
    assert [f["event"] for f in seen] == ["progress", "complete"]


@pytest.mark.asyncio
async def test_finalizes_take_turns_and_say_so():
    order = []
    gates = {"a": asyncio.Event(), "b": asyncio.Event()}

    def steps_for(name):
        async def steps():
            order.append(f"{name} start")
            await gates[name].wait()
            order.append(f"{name} end")
            yield frame("complete", cid=name)
        return steps

    a = finalize_jobs.start("a", steps_for("a"), queued)
    await asyncio.sleep(0)
    b = finalize_jobs.start("b", steps_for("b"), queued)
    await asyncio.sleep(0.01)
    assert order == ["a start"]  # b waits its turn
    assert finalize_jobs.ahead_of("b") == 1
    assert json.loads(b.events[0]["data"]) == {"stage": "queued", "message": "1 ahead"}

    gates["b"].set()
    gates["a"].set()
    await asyncio.wait_for(asyncio.gather(a.task, b.task), 1)
    assert order == ["a start", "a end", "b start", "b end"]
    assert finalize_jobs.ahead_of("b") == 0


@pytest.mark.asyncio
async def test_asking_again_joins_the_job_already_running():
    gate = asyncio.Event()
    runs = []

    async def steps():
        runs.append(1)
        await gate.wait()
        yield frame("complete", cid="x")

    first = finalize_jobs.start("d", steps, queued)
    again = finalize_jobs.start("d", steps, queued)
    assert again is first
    gate.set()
    await asyncio.wait_for(first.task, 1)
    assert runs == [1]


@pytest.mark.asyncio
async def test_what_escapes_the_steps_ends_the_job_as_an_error():
    async def steps():
        raise RuntimeError("disk full")
        yield  # pragma: no cover

    job = finalize_jobs.start("d", steps, queued)
    await asyncio.wait_for(job.task, 1)
    assert job.done
    assert json.loads(job.events[-1]["data"]) == {"stage": "exception", "message": "disk full"}


def write_draft(staging: Path, draft_id: str, status: str) -> Path:
    draft_dir = staging / "drafts" / draft_id
    draft_dir.mkdir(parents=True)
    state = ContentDraftState(draft_id=draft_id, uploaded_by="wiki:Someone", status=status,
                              created_at=datetime.now(timezone.utc))
    content.save_draft_state(draft_dir, state)
    return draft_dir


def test_a_restart_fails_stranded_finalizes_and_says_why(tmp_path):
    write_draft(tmp_path, "stranded", "finalizing")
    write_draft(tmp_path, "waiting", "uploaded")
    assert content.fail_stranded_finalizes(tmp_path) == ["stranded"]
    stranded = content.load_draft_state(tmp_path / "drafts" / "stranded")
    assert stranded.status == "finalize_failed"
    assert "finalize again" in stranded.finalize_log[-1]["error"]
    assert content.load_draft_state(tmp_path / "drafts" / "waiting").status == "uploaded"
