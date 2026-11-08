"""/enrich/torrent does its slow work off the event loop, so the web service keeps
answering while a big Release's torrent is made (8 Nov 2026: unpacking, hashing and
copying 1.4 GB on the loop would stall every upload and finalize meanwhile)."""

import asyncio
import threading
from pathlib import Path

from app.config import Settings
from app.routes import enrich
from app.routes.enrich import TorrentRequest

CID = "bafybeigdyrzt5sfp7udm7hu76uh7y26nf3efuylqabf3oclgtqy55fbzdi"


def test_slow_steps_run_in_threads_and_the_loop_stays_free(tmp_path, monkeypatch):
    content = tmp_path / "wrap" / "content"
    content.mkdir(parents=True)
    (content / "a.bin").write_bytes(b"x" * 1000)
    main = threading.get_ident()
    seen = {}

    async def fake_fetch(cid, url):
        return content

    def slow_create(**kw):
        seen["create"] = threading.get_ident()
        import time; time.sleep(0.3)  # a big Release's hashing
        from app.services.torrent import create_torrent
        return create_torrent(**kw)

    def slow_stage(*args):
        seen["stage"] = threading.get_ident()
        return True

    monkeypatch.setattr(enrich, "fetch_ipfs_content", fake_fetch)
    monkeypatch.setattr(enrich, "create_torrent", slow_create)
    monkeypatch.setattr(enrich, "stage_for_seeding", slow_stage)
    settings = Settings(staging_dir=str(tmp_path), seeding_dir=str(tmp_path / "seeding"))

    async def run():
        ticks = 0

        async def heartbeat():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.02)
                ticks += 1

        beat = asyncio.create_task(heartbeat())
        result = await enrich.generate_torrent(TorrentRequest(cid=CID), "test", settings)
        beat.cancel()
        return result, ticks

    result, ticks = asyncio.run(run())
    assert result.success, result.error
    assert seen["create"] != main and seen["stage"] != main
    assert ticks >= 5  # the loop kept running while the hashing slept


def test_one_at_a_time_per_cid(tmp_path, monkeypatch):
    gate = asyncio.Event()

    async def run():
        async def held_fetch(cid, url):
            await gate.wait()
            return None

        monkeypatch.setattr(enrich, "fetch_ipfs_content", held_fetch)
        settings = Settings(staging_dir=str(tmp_path), seeding_dir=str(tmp_path / "seeding"))
        first = asyncio.create_task(enrich.generate_torrent(TorrentRequest(cid=CID), "test", settings))
        await asyncio.sleep(0)
        second = await enrich.generate_torrent(TorrentRequest(cid=CID), "test", settings)
        gate.set()
        return second, await first

    second, first = asyncio.run(run())
    assert (second.success, second.error) == (False, "Already being made")
    assert first.error == "Could not fetch CID from IPFS"
    assert CID not in enrich._in_progress
