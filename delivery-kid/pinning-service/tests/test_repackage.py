"""Republishing a release with its init segments fixed, inside the IPFS node.

A fake kubo stands in for the real one: a tree of /ipfs/<cid> directories,
an MFS that copies them by reference, and pins. The test checks what the
new directory holds, not which calls were made.
"""
import asyncio
import hashlib
import json

import httpx

from app.services.repackage import repackage_release

from tests.test_drop_empty_sdtp import EMPTY_SDTP, init_segment

OLD = "QmOldRelease"


def cid_of(tree) -> str:
    return "Qm" + hashlib.sha256(json.dumps(tree, sort_keys=True, default=bytes.hex)
                                 .encode()).hexdigest()[:20]


class FakeKubo:
    def __init__(self, tree: dict):
        self.blocks = {OLD: tree}
        self.mfs: dict = {}
        self.pins = {OLD}
        self.writes = 0

    def resolve(self, path: str):
        parts = path.strip("/").split("/")
        if parts[0] == "ipfs":
            node = self.blocks[parts[1]]
            rest = parts[2:]
        else:
            node = self.mfs
            rest = parts
        for p in rest:
            node = node[p]
        return node

    def handler(self, request: httpx.Request) -> httpx.Response:
        cmd = request.url.path.split("/api/v0/", 1)[1]
        args = request.url.params.get_list("arg")
        q = request.url.params
        if cmd == "ls":
            node = self.resolve(args[0])
            links = [{"Name": k, "Type": 1 if isinstance(v, dict) else 2} for k, v in node.items()]
            return httpx.Response(200, json={"Objects": [{"Links": links}]})
        if cmd == "cat":
            node = self.resolve(args[0])
            return httpx.Response(200, content=node)
        if cmd == "files/mkdir":
            self.mfs.setdefault(args[0].strip("/"), {})
            return httpx.Response(200)
        if cmd == "files/rm":
            *parent, name = args[0].strip("/").split("/")
            node = self.mfs
            for p in parent:
                node = node.get(p, {})
            if name not in node and q.get("force") != "true":
                return httpx.Response(500, text="file does not exist")
            node.pop(name, None)
            return httpx.Response(200)
        if cmd == "files/cp":
            src, dst = args
            *parent, name = dst.strip("/").split("/")
            node = self.mfs
            for p in parent:
                node = node[p]
            node[name] = _copy(self.resolve(src))
            return httpx.Response(200)
        if cmd == "files/write":
            assert q.get("truncate") == "true" and q.get("create") == "true"
            *parent, name = args[0].strip("/").split("/")
            node = self.mfs
            for p in parent:
                node = node[p]
            body = request.content
            boundary = request.headers["content-type"].split("boundary=")[1].encode()
            part = body.split(b"--" + boundary)[1]
            if b'filename="' in part and b"/" in part.split(b'filename="', 1)[1].split(b'"', 1)[0]:
                # As kubo does: a slash makes the part a directory, not a file.
                return httpx.Response(500, json={"Message": "file argument was nil"})
            node[name] = part.split(b"\r\n\r\n", 1)[1].rsplit(b"\r\n", 1)[0]
            self.writes += 1
            return httpx.Response(200)
        if cmd == "files/stat":
            tree = _copy(self.resolve(args[0]))
            cid = cid_of(tree)
            self.blocks[cid] = tree
            return httpx.Response(200, json={"Hash": cid})
        if cmd == "pin/add":
            self.pins.add(args[0])
            return httpx.Response(200)
        return httpx.Response(404, text=f"unknown {cmd}")


def _copy(node):
    return {k: _copy(v) for k, v in node.items()} if isinstance(node, dict) else node


def release(with_sdtp: bool) -> dict:
    sdtp = EMPTY_SDTP if with_sdtp else b""
    return {
        "master.m3u8": b"#EXTM3U\n",
        "metadata.json": json.dumps({"title": "Do you use AI subtitles?"}).encode(),
        "poster.jpg": b"\xff\xd8poster",
        "stream_1080p": {"init_0.mp4": init_segment(sdtp), "seg_00000.m4s": b"big segment"},
        "stream_360p": {"init_2.mp4": init_segment(sdtp), "seg_00000.m4s": b"small segment"},
        "stream_audio": {"init_3.mp4": init_segment(b""), "seg_00000.m4s": b"opus"},
    }


def run(kubo: FakeKubo, **kw):
    return asyncio.run(repackage_release(OLD, "http://ipfs:5001",
                                         transport=httpx.MockTransport(kubo.handler), **kw))


def test_fixes_every_video_init_segment_and_pins_the_new_release():
    kubo = FakeKubo(release(with_sdtp=True))

    result = run(kubo)

    assert result.success, result.error
    assert result.fixed == ["stream_1080p/init_0.mp4", "stream_360p/init_2.mp4"]
    assert result.new_cid and result.new_cid != OLD
    assert {OLD, result.new_cid} <= kubo.pins  # the old one stays pinned

    new, old = kubo.blocks[result.new_cid], kubo.blocks[OLD]
    assert new["stream_1080p"]["init_0.mp4"] == init_segment(b"")
    assert new["stream_360p"]["init_2.mp4"] == init_segment(b"")
    # Everything else is the same bytes.
    for name in ("master.m3u8", "poster.jpg"):
        assert new[name] == old[name]
    for rendition in ("stream_1080p", "stream_360p", "stream_audio"):
        assert new[rendition]["seg_00000.m4s"] == old[rendition]["seg_00000.m4s"]
    assert new["stream_audio"] == old["stream_audio"]

    metadata = json.loads(new["metadata.json"])
    assert metadata["title"] == "Do you use AI subtitles?"
    assert metadata["repackaged_from"] == OLD

    assert kubo.mfs["repackage"] == {}  # the MFS scaffolding is cleared


def test_running_it_twice_gives_the_same_cid():
    kubo = FakeKubo(release(with_sdtp=True))

    assert run(kubo).new_cid == run(kubo).new_cid


def test_a_release_without_the_box_is_left_alone():
    kubo = FakeKubo(release(with_sdtp=False))

    result = run(kubo)

    assert result.success and result.new_cid is None and result.fixed == []
    assert kubo.pins == {OLD} and kubo.writes == 0


def test_dry_run_reports_and_writes_nothing():
    kubo = FakeKubo(release(with_sdtp=True))

    result = run(kubo, dry_run=True)

    assert result.success and result.new_cid is None
    assert result.fixed == ["stream_1080p/init_0.mp4", "stream_360p/init_2.mp4"]
    assert kubo.pins == {OLD} and kubo.writes == 0 and kubo.mfs == {}


def test_a_failing_node_reports_the_error():
    def broken(request):
        return httpx.Response(500, text="ipfs is down")

    result = asyncio.run(repackage_release(OLD, "http://ipfs:5001",
                                           transport=httpx.MockTransport(broken)))

    assert not result.success and "ipfs is down" in result.error
