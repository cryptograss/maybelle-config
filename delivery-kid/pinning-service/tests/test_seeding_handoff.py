"""The seeder runs in its own process; the web service hands it torrents through
the seeding folder (app/services/seeding_store.py). These check both ends."""

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.routes.torrent import router as torrent_router
from app.services import seeding_store
from app.services.seeding_store import (
    BY_INFOHASH, INCOMING, read_status, stage_for_seeding, write_status,
)
from app.services.torrent import create_torrent

CID = "bafybeigdyrzt5sfp7udm7hu76uh7y26nf3efuylqabf3oclgtqy55fbzdi"


def make_content(root: Path, files: dict[str, bytes]) -> Path:
    content = root / "content"
    for name, data in files.items():
        p = content / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return content


def make_client(seeding_dir: Path) -> TestClient:
    app = FastAPI()
    app.include_router(torrent_router)
    settings = Settings(staging_dir=str(seeding_dir.parent), seeding_dir=str(seeding_dir))
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app)


# --- the web service's side: no libtorrent ---------------------------------

def test_web_service_never_loads_libtorrent():
    """Importing the whole web app must not pull libtorrent into the process."""
    out = subprocess.run(
        [sys.executable, "-c", "import sys, app.main; print('libtorrent' in sys.modules)"],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, check=True,
    )
    assert out.stdout.strip() == "False"


def test_stage_moves_content_and_torrent_into_incoming(tmp_path):
    seeding = tmp_path / "seeding"
    content = make_content(tmp_path, {"a.flac": b"x" * 100, "b.flac": b"y" * 50})
    entry = stage_for_seeding(seeding, CID, b"d4:infod4:name1:xee", content)

    assert entry is not None
    assert entry.parent == seeding / INCOMING
    assert entry.name.startswith(CID + ".")
    assert (entry / "torrent.dat").read_bytes() == b"d4:infod4:name1:xee"
    assert (entry / "data" / "a.flac").read_bytes() == b"x" * 100
    assert not content.exists(), "content is moved, not copied"
    # Nothing half-written is left beside it.
    assert [p.name for p in (seeding / INCOMING).iterdir()] == [entry.name]


def test_stage_refuses_odd_cid(tmp_path):
    content = make_content(tmp_path, {"a": b"x"})
    assert stage_for_seeding(tmp_path / "seeding", "../etc", b"d", content) is None
    assert content.exists()


def test_torrent_route_serves_by_infohash_index(tmp_path):
    seeding = tmp_path / "seeding"
    ih = "ab" * 20
    (seeding / CID).mkdir(parents=True)
    (seeding / CID / "torrent.dat").write_bytes(b"TORRENT")
    (seeding / BY_INFOHASH).mkdir()
    (seeding / BY_INFOHASH / ih).write_text(CID)

    client = make_client(seeding)
    r = client.get(f"/torrent/{ih}.torrent")
    assert r.status_code == 200
    assert r.content == b"TORRENT"
    assert r.headers["content-type"] == "application/x-bittorrent"

    assert client.get(f"/torrent/{'cd' * 20}.torrent").status_code == 404
    assert client.get("/torrent/not-a-hash.torrent").status_code == 404


def test_status_missing_fresh_and_stale(tmp_path):
    seeding = tmp_path / "seeding"
    seeding.mkdir()
    assert read_status(seeding)["running"] is False

    write_status(seeding, {"running": True, "torrents": 3})
    s = read_status(seeding)
    assert s["running"] is True and s["torrents"] == 3

    later = time.time() + seeding_store.STATUS_STALE_SECONDS + 5
    s = read_status(seeding, now=later)
    assert s["running"] is False
    assert "old" in s["reason"]

    # And through the route.
    r = make_client(seeding).get("/torrent/status")
    assert r.status_code == 200 and r.json()["torrents"] == 3


# --- the seeder's side: libtorrent, in its own process in production -------

try:
    from app.services.seeder import Seeder
except ImportError:  # libtorrent isn't installed here
    Seeder = None


@pytest.fixture
def seeder(tmp_path):
    if Seeder is None:
        pytest.skip("libtorrent not installed")
    s = Seeder(str(tmp_path / "seeding"), listen_ports=(0, 0))
    s.start()
    yield s
    s.stop()


def stage(tmp_path, seeding, name, files):
    content = make_content(tmp_path / name, files)
    result = create_torrent(directory=content, name=name)
    assert result.success
    assert stage_for_seeding(seeding, CID, result.torrent_bytes, content)
    return result


def test_seeder_takes_multi_file_handover(tmp_path, seeder):
    seeding = seeder.seeding_dir
    result = stage(tmp_path, seeding, "My Album", {"01.flac": b"a" * 3000, "02.flac": b"b" * 2000})

    assert seeder.process_incoming() == 1
    assert list((seeding / INCOMING).iterdir()) == []
    assert (seeding / CID / "data" / "My Album" / "01.flac").exists()
    assert (seeding / BY_INFOHASH / result.infohash).read_text() == CID
    assert result.infohash in seeder._handles

    # The web service can now serve its .torrent.
    r = make_client(seeding).get(f"/torrent/{result.infohash}.torrent")
    assert r.status_code == 200 and r.content == result.torrent_bytes


def test_seeder_takes_single_file_handover(tmp_path, seeder):
    seeding = seeder.seeding_dir
    result = stage(tmp_path, seeding, "video.mp4", {CID: b"v" * 4000})

    assert seeder.process_incoming() == 1
    assert (seeding / CID / "data" / "video.mp4").read_bytes() == b"v" * 4000


def test_new_torrent_for_same_cid_replaces_old(tmp_path, seeder):
    seeding = seeder.seeding_dir
    old = stage(tmp_path, seeding, "v1", {"a.flac": b"1" * 1000, "b.flac": b"2" * 10})
    seeder.process_incoming()
    new = stage(tmp_path, seeding, "v2", {"a.flac": b"3" * 1000, "b.flac": b"4" * 10})
    seeder.process_incoming()

    assert old.infohash != new.infohash
    assert not (seeding / BY_INFOHASH / old.infohash).exists()
    assert (seeding / BY_INFOHASH / new.infohash).read_text() == CID
    assert set(seeder._handles) == {new.infohash}


def test_unreadable_handover_is_set_aside_not_retried(tmp_path, seeder):
    seeding = seeder.seeding_dir
    content = make_content(tmp_path, {"a": b"x"})
    stage_for_seeding(seeding, CID, b"not a torrent", content)

    assert seeder.process_incoming() == 0
    names = [p.name for p in (seeding / INCOMING).iterdir()]
    assert len(names) == 1 and names[0].startswith(".failed-" + CID)
    assert seeder.process_incoming() == 0  # not picked up again


def test_seeder_reloads_and_reindexes_on_start(tmp_path, seeder):
    seeding = seeder.seeding_dir
    result = stage(tmp_path, seeding, "Album", {"a.flac": b"a" * 100, "b.flac": b"b" * 100})
    seeder.process_incoming()
    seeder.stop()
    (seeding / BY_INFOHASH / result.infohash).unlink()  # an older seeder kept no index

    again = Seeder(str(seeding), listen_ports=(0, 0))
    again.start()
    try:
        assert result.infohash in again._handles
        assert (seeding / BY_INFOHASH / result.infohash).read_text() == CID
        again.write_status()
        status = json.loads((seeding / ".status.json").read_text())
        assert status["running"] is True and status["torrents"] == 1
    finally:
        again.stop()


def test_seeding_folder_listing_stays_cids_only(tmp_path, seeder):
    """audit-storage.py takes `ls seeding/` as the list of seeded CIDs."""
    seeding = seeder.seeding_dir
    stage(tmp_path, seeding, "Album", {"a": b"a" * 10, "b": b"b"})
    seeder.process_incoming()
    seeder.write_status()
    visible = [p.name for p in seeding.iterdir() if not p.name.startswith(".")]
    assert visible == [CID]
