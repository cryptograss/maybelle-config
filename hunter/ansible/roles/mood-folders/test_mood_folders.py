"""mood-folders.py against a temporary /opt/magenta and output directory.

    python3 -m pytest hunter/ansible/roles/mood-folders/test_mood_folders.py
"""

import errno
import importlib.util
import os
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('mood_folders', Path(__file__).parent / 'files' / 'mood-folders.py')
mf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mf)

DOMAIN = 'hunter.example'


@pytest.fixture
def dirs(tmp_path):
    base, out = tmp_path / 'opt', tmp_path / 'out'
    (base / 'moods').mkdir(parents=True)
    return base, out


def put(path, data=b'x'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_shows_media_and_nothing_else(dirs):
    base, out = dirs
    m = base / 'moods' / 'uploads'
    put(m / 'shot.png')
    put(m / 'clip.webm')
    put(m / 'sub' / 'memo.opus')
    put(m / 'doc.PDF')
    put(m / 'notes.md')                       # not a kind shown
    put(m / '.env', b'DB_PASSWORD=x')         # dot-file
    put(m / '.claude' / 'pic.png')            # under a dot-directory
    put(m / 'repo' / '.git' / 'HEAD')
    put(m / 'repo' / 'docs' / 'logo.png')     # inside a git checkout
    os.symlink('/etc/hostname', m / 'sneaky.png')

    counts = mf.build(base, out, DOMAIN)

    assert counts == {'uploads': 4}
    served = sorted(str(p.relative_to(out / 'uploads')) for p in (out / 'uploads').rglob('*') if p.is_file())
    assert served == ['clip.webm', 'doc.PDF', 'index.html', 'shot.png', 'sub/memo.opus']
    page = (out / 'uploads' / 'index.html').read_text()
    assert 'shot.png' in page and '<video' in page and '<audio' in page
    assert '.env' not in page and 'logo.png' not in page and 'sneaky' not in page
    assert '#uploads' in (out / 'index.html').read_text()


def test_hard_linked_not_copied(dirs):
    base, out = dirs
    src = put(base / 'moods' / 'm' / 'a.png', b'png')
    mf.build(base, out, DOMAIN)
    assert (out / 'm' / 'a.png').stat().st_ino == src.stat().st_ino


def test_removed_and_replaced_files_follow(dirs):
    base, out = dirs
    m = base / 'moods' / 'm'
    put(m / 'a.png', b'one')
    put(m / 'deep' / 'b.png')
    mf.build(base, out, DOMAIN)

    (m / 'a.png').unlink()
    put(m / 'a.png', b'two')                  # a new file, a new inode
    (m / 'deep' / 'b.png').unlink()
    mf.build(base, out, DOMAIN)

    assert (out / 'm' / 'a.png').read_bytes() == b'two'
    assert not (out / 'm' / 'deep').exists()


def test_a_mood_gone_is_gone(dirs):
    base, out = dirs
    put(base / 'moods' / 'old' / 'a.png')
    mf.build(base, out, DOMAIN)
    (base / 'moods' / 'old' / 'a.png').unlink()
    (base / 'moods' / 'old').rmdir()
    assert mf.build(base, out, DOMAIN) == {}
    assert not (out / 'old').exists()
    assert 'Nothing to show yet' in (out / 'index.html').read_text()


def test_other_filesystem_copies_small_skips_big(dirs, monkeypatch):
    base, out = dirs
    put(base / 'moods' / 'm' / 'small.png', b'small')
    put(base / 'moods' / 'm' / 'big.mp4', b'too big to copy')
    monkeypatch.setattr(mf, 'COPY_MAX', 8)

    def no_link(*a, **k):
        raise OSError(errno.EXDEV, 'cross-device')
    monkeypatch.setattr(mf.os, 'link', no_link)

    assert mf.build(base, out, DOMAIN) == {'m': 1}
    assert (out / 'm' / 'small.png').read_bytes() == b'small'
    assert not (out / 'm' / 'big.mp4').exists()


def test_names_are_escaped(dirs):
    base, out = dirs
    put(base / 'moods' / 'm' / '<b>x".png')
    mf.build(base, out, DOMAIN)
    page = (out / 'm' / 'index.html').read_text()
    assert '<b>x' not in page
    assert '%3Cb%3Ex%22.png' in page


def test_too_deep_is_left_out(dirs):
    base, out = dirs
    put(base / 'moods' / 'm' / 'a' / 'b' / 'c' / 'd' / 'e' / 'f.png')
    assert mf.build(base, out, DOMAIN) == {'m': 0}


def test_unchanged_run_rewrites_nothing(dirs):
    base, out = dirs
    put(base / 'moods' / 'm' / 'a.png')
    mf.build(base, out, DOMAIN)
    before = (out / 'm' / 'index.html').stat().st_mtime_ns
    mf.build(base, out, DOMAIN)
    assert (out / 'm' / 'index.html').stat().st_mtime_ns == before
