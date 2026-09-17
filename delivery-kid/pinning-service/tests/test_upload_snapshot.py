"""A completed upload must be mirrored to the wiki, not just a finished one.

An "uploaded" draft can sit for days before anyone finalizes it, and the
staging directory is not a durable record. The snapshot at this point used to
happen only as a side effect of the Coconut preview webhook; when Coconut was
removed (#126) it went with it, and draft 38be9254 was uploaded with no
upload-stage diagnostics page at all.
"""

import asyncio
from datetime import datetime, timezone

import pytest

from app.config import Settings
from app.models.content import ContentDraftState
from app.routes import content as content_routes


class _Analysis:
    """Stand-in for one analyze.analyze_media_directory result."""

    success = True
    error = None
    original_filename = "IMG_0001.mov"
    detected_title = "IMG 0001"
    media_type = "video"
    format = "MOV"
    duration_seconds = 28.0
    sample_rate = None
    bit_depth = None
    channels = None
    width = 1920
    height = 1080
    video_codec = "hevc"
    audio_codec = "aac"
    size_bytes = 28898350
    creation_time = None


@pytest.fixture
def draft(tmp_path):
    draft_dir = tmp_path / "drafts" / "d1"
    (draft_dir / "upload").mkdir(parents=True)
    state = ContentDraftState(draft_id="38be9254-0098-4f36-9b0d-a53b22aa0948",
                              draft_type="content", uploaded_by="0xabc",
                              created_at=datetime.now(timezone.utc))
    return draft_dir, state


def _run(draft_dir, state, monkeypatch, analyses):
    """Run the shared upload-completion path, capturing snapshot calls."""
    snapshots = []

    async def fake_analyze(_directory):
        return analyses

    async def fake_snapshot(snapshot_state):
        snapshots.append(snapshot_state.status)
        return True

    monkeypatch.setattr(content_routes.analyze, "analyze_media_directory", fake_analyze)
    monkeypatch.setattr(content_routes, "snapshot_diagnostics_for_state_async", fake_snapshot)

    async def main():
        files = await content_routes._analyze_and_mark_uploaded(
            state.draft_id, draft_dir, draft_dir / "upload", state,
            Settings(staging_dir=str(draft_dir.parent.parent)),
        )
        # _fire_diagnostics_snapshot is deliberately not awaited; give the
        # task it schedules a turn to run.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return files

    return asyncio.run(main()), snapshots


def test_completed_upload_is_snapshotted_to_the_wiki(draft, monkeypatch):
    draft_dir, state = draft
    files, snapshots = _run(draft_dir, state, monkeypatch, [_Analysis()])

    assert len(files) == 1
    assert state.status == "uploaded"
    assert snapshots == ["uploaded"], "the completed upload was never mirrored to the wiki"


def test_video_upload_says_the_preview_is_the_file_itself(draft, monkeypatch):
    draft_dir, state = draft
    _run(draft_dir, state, monkeypatch, [_Analysis()])

    assert state.preview_status == "none"
    assert len(state.preview_log) == 1
    assert "uploaded file" in state.preview_log[0]["message"]
    # Nothing is sent anywhere for a preview any more.
    assert not any("oconut" in entry["message"] for entry in state.preview_log)


def test_nothing_usable_raises_and_snapshots_nothing(draft, monkeypatch):
    """An upload with no readable media is the caller's error to surface.

    Checking state.status here would prove nothing — "uploaded" is the model's
    default. What matters is that this path raises instead of recording a
    successful upload, and that it claims nothing on the wiki.
    """
    draft_dir, state = draft
    failed = _Analysis()
    failed.success = False
    failed.error = "ffprobe failed"
    snapshots = []

    async def fake_analyze(_directory):
        return [failed]

    async def fake_snapshot(snapshot_state):
        snapshots.append(snapshot_state.status)
        return True

    monkeypatch.setattr(content_routes.analyze, "analyze_media_directory", fake_analyze)
    monkeypatch.setattr(content_routes, "snapshot_diagnostics_for_state_async", fake_snapshot)

    async def main():
        await content_routes._analyze_and_mark_uploaded(
            state.draft_id, draft_dir, draft_dir / "upload", state,
            Settings(staging_dir=str(draft_dir.parent.parent)),
        )

    with pytest.raises(content_routes.NoUsableMediaError):
        asyncio.run(main())
    assert snapshots == []
    assert state.files == []
    assert any(e["phase"] == "analyze-error" for e in state.upload_log)
