"""Tests for maybelle/scripts/repackage-sdtp-releases.py."""

import importlib.util
import io
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "repackage_sdtp_releases", SCRIPTS / "repackage-sdtp-releases.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["repackage_sdtp_releases"] = module
    spec.loader.exec_module(module)
    return module


mod = _load()


def test_normalize_cid_lowercases_bafy_and_preserves_qm():
    assert mod.normalize_cid("Bafybeigdyrzt") == "bafybeigdyrzt"
    assert mod.normalize_cid("QmYacRRA9WjdwwvbcmF8ujYZDvBN9n5m39atTSmoX4fWV1") == (
        "QmYacRRA9WjdwwvbcmF8ujYZDvBN9n5m39atTSmoX4fWV1"
    )


def test_init_segment_has_sdtp_detects_box_and_clean_segments(monkeypatch):
    files = {
        "https://ipfs.delivery-kid.cryptograss.live/ipfs/QmOld/master.m3u8": (
            b"#EXTM3U\nstream_720p/playlist.m3u8\nstream_audio/playlist.m3u8\n"
        ),
        "https://ipfs.delivery-kid.cryptograss.live/ipfs/QmOld/stream_720p/playlist.m3u8": (
            b'#EXTM3U\n#EXT-X-MAP:URI="init_0.mp4"\nseg_0.m4s\n'
        ),
        "https://ipfs.delivery-kid.cryptograss.live/ipfs/QmOld/stream_720p/init_0.mp4": (
            b"\x00\x00\x00\x18ftypisom\x00\x00\x00\x0csdtp\x00\x00\x00\x00"
        ),
        "https://ipfs.delivery-kid.cryptograss.live/ipfs/QmClean/master.m3u8": (
            b"#EXTM3U\nstream_720p/playlist.m3u8\n"
        ),
        "https://ipfs.delivery-kid.cryptograss.live/ipfs/QmClean/stream_720p/playlist.m3u8": (
            b'#EXTM3U\n#EXT-X-MAP:URI="init_0.mp4"\nseg_0.m4s\n'
        ),
        "https://ipfs.delivery-kid.cryptograss.live/ipfs/QmClean/stream_720p/init_0.mp4": (
            b"\x00\x00\x00\x18ftypisom"
        ),
    }
    monkeypatch.setattr(mod, "_http_bytes", lambda url, timeout=15: files[url])
    assert mod.init_segment_has_sdtp("QmOld") is True
    assert mod.init_segment_has_sdtp("QmClean") is False


def test_main_repackages_explicit_cid_and_writes_mapping(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("DELIVERY_KID_API_KEY", "test-secret-not-real")
    calls = []

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(
                {
                    "success": True,
                    "old_cid": "QmYacRRA9WjdwwvbcmF8ujYZDvBN9n5m39atTSmoX4fWV1",
                    "new_cid": "QmNewPeggyO11111111111111111111111111111111111",
                    "fixed": ["stream_720p/init_0.mp4", "stream_360p/init_0.mp4"],
                }
            ).encode("utf-8")

    def fake_urlopen(req, timeout=180):
        calls.append((req.full_url, json.loads(req.data.decode("utf-8")), req.headers))
        return FakeResp()

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
    out_file = tmp_path / "map.json"
    rc = mod.main(
        [
            "QmYacRRA9WjdwwvbcmF8ujYZDvBN9n5m39atTSmoX4fWV1",
            "--json-out",
            str(out_file),
        ]
    )
    assert rc == 0
    assert len(calls) == 1
    assert calls[0][0] == "https://delivery-kid.cryptograss.live/enrich/repackage"
    assert calls[0][1] == {
        "cid": "QmYacRRA9WjdwwvbcmF8ujYZDvBN9n5m39atTSmoX4fWV1",
        "dry_run": False,
    }
    stdout = capsys.readouterr().out
    assert (
        "QmYacRRA9WjdwwvbcmF8ujYZDvBN9n5m39atTSmoX4fWV1 -> QmNewPeggyO11111111111111111111111111111111111"
        in stdout
    )
    saved = json.loads(out_file.read_text(encoding="utf-8"))
    assert saved[0]["new_cid"] == "QmNewPeggyO11111111111111111111111111111111111"
