#!/usr/bin/env python3
"""Each Mood's folder as a gallery: https://folders.hunter.cryptograss.live/<slug>/ (Justin, #uploads-and-embeds,
19 Nov: "a simple gallery, showing only certain file types, from the mood directory").

The screenshots and recordings an emergent leaves in its Mood's directory (/opt/magenta/moods/<slug>) were
reachable only over SSH. Run each minute as root, this builds what Caddy serves at folders.<domain>:

    <out>/index.html                every Mood with something to show
    <out>/<slug>/index.html         that Mood's gallery, newest first
    <out>/<slug>/<path>             each file shown, hard-linked to the one in the Mood's directory

Shown: regular files with one of the extensions in KINDS, outside any git checkout (the repositories are on
GitHub), with no part of the path starting with a dot (.git, .claude, .env) or in SKIP, no deeper than DEPTH. Symlinks
are never followed or shown. Caddy serves only <out>, never the Mood directories themselves: what isn't
linked here can't be fetched, whatever its name.

A file is hard-linked, so it costs no space; where that can't be done (another filesystem) one under
COPY_MAX is copied, and a bigger one left out. A file gone from the Mood is gone from here the next minute.
Public, as the Moods are.

    mood-folders.py [--base-dir /opt/magenta] [--out /var/lib/mood-folders] [--domain hunter.cryptograss.live]
"""

import argparse
import errno
import html
import os
import re
import shutil
import sys
import time
from pathlib import Path
from urllib.parse import quote

SLUG = re.compile(r'^[a-z0-9][a-z0-9_.-]*$')
IMAGE = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'avif', 'svg'}
VIDEO = {'mp4', 'webm', 'mov'}
AUDIO = {'mp3', 'ogg', 'opus', 'flac', 'wav', 'm4a'}
OTHER = {'pdf'}
KINDS = IMAGE | VIDEO | AUDIO | OTHER
DEPTH = 4
SKIP = {'node_modules', '__pycache__', 'venv'}  # dependencies, not anyone's work
COPY_MAX = 50 * 1024 * 1024


def kind(name):
    ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
    for label, exts in (('image', IMAGE), ('video', VIDEO), ('audio', AUDIO), ('other', OTHER)):
        if ext in exts:
            return label
    return None


def shown(mood):
    """[(relative path, stat)] of what this Mood's gallery shows."""
    found = []
    for root, dirs, files in os.walk(mood, followlinks=False):
        here = Path(root)
        depth = len(here.relative_to(mood).parts)
        # Not into dot-directories, git checkouts, symlinked directories, or too deep.
        dirs[:] = [] if depth >= DEPTH else sorted(
            d for d in dirs
            if not d.startswith('.') and d not in SKIP and not (here / d).is_symlink()
            and not (here / d / '.git').exists())
        for name in files:
            if name.startswith('.') or not kind(name):
                continue
            path = here / name
            try:
                st = path.lstat()
            except OSError:
                continue
            if not os.path.isfile(path) or path.is_symlink():
                continue
            found.append((path.relative_to(mood), st))
    found.sort(key=lambda f: (-f[1].st_mtime, str(f[0])))
    return found


def place(src, dest, st):
    """Hard-link src at dest (or copy a small one). True if dest is now src's content."""
    try:
        have = dest.lstat()
        if have.st_ino == st.st_ino and have.st_dev == st.st_dev:
            return True
        # A copy (another filesystem) keeps the original's mtime to the nanosecond: the same size and
        # mtime is the same file. A stale link to a replaced original has the old one's, and is relinked.
        if have.st_nlink == 1 and have.st_size == st.st_size and have.st_mtime_ns == st.st_mtime_ns:
            return True
        dest.unlink()
    except FileNotFoundError:
        pass
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dest, follow_symlinks=False)
        return True
    except OSError as e:
        if e.errno != errno.EXDEV or st.st_size > COPY_MAX:
            return False
    try:
        shutil.copy2(src, dest, follow_symlinks=False)
        return True
    except OSError:
        return False


def size(n):
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return f'{n:.0f} {unit}' if unit == 'B' else f'{n:.1f} {unit}'
        n /= 1024


STYLE = """
:root { color-scheme: light dark; --bg: #faf8f4; --fg: #222; --dim: #777; --card: #fff; --line: #e4e0d8; --accent: #b5106e; }
@media (prefers-color-scheme: dark) { :root { --bg: #16141a; --fg: #e8e4ec; --dim: #9a94a2; --card: #211e26; --line: #34303a; --accent: #f05aa8; } }
body { margin: 0; padding: 1rem; background: var(--bg); color: var(--fg); font: 15px/1.4 system-ui, sans-serif; }
a { color: var(--accent); }
h1 { font-size: 1.3rem; margin: 0 0 .25rem; }
.sub { color: var(--dim); margin: 0 0 1rem; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: .75rem; }
figure { margin: 0; background: var(--card); border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }
figure img, figure video { display: block; width: 100%; max-height: 360px; object-fit: contain; background: #0001; }
figure audio { width: 100%; }
figcaption { padding: .4rem .6rem; font-size: .85rem; word-break: break-all; }
figcaption small { color: var(--dim); display: block; }
.file { padding: 2rem .6rem; text-align: center; font-size: 2rem; }
ul.moods { list-style: none; padding: 0; columns: 16rem; }
ul.moods li { padding: .2rem 0; }
"""


def page(title, body):
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{html.escape(title)}</title><style>{STYLE}</style></head><body>{body}</body></html>\n')


def gallery(slug, files, domain, now):
    cards = []
    for rel, st in files:
        href = quote(str(rel))
        name = html.escape(str(rel))
        k = kind(rel.name)
        if k == 'image':
            media = f'<a href="{href}"><img src="{href}" alt="{name}" loading="lazy"></a>'
        elif k == 'video':
            media = f'<video src="{href}" controls preload="metadata" playsinline></video>'
        elif k == 'audio':
            media = f'<audio src="{href}" controls preload="none"></audio>'
        else:
            media = f'<a class="file" href="{href}">📄</a>'
        when = time.strftime('%d %b %H:%M', time.gmtime(st.st_mtime))
        cards.append(f'<figure>{media}<figcaption><a href="{href}">{name}</a>'
                     f'<small>{when}Z · {size(st.st_size)}</small></figcaption></figure>')
    sub = (f'{len(files)} file{"" if len(files) == 1 else "s"}, newest first. Pictures, video, sound and PDFs '
           f'from this Mood\'s folder on hunter; repositories are on GitHub. '
           f'<a href="https://magenta.cryptograss.live/moods/{quote(slug)}/">#{html.escape(slug)} in magenta</a> · '
           f'<a href="/">every Mood\'s folder</a>')
    body = (f'<h1>#{html.escape(slug)}\'s folder</h1><p class="sub">{sub}</p>'
            + (f'<div class="grid">{"".join(cards)}</div>' if cards else '<p>Nothing to show yet.</p>'))
    return page(f"#{slug}'s folder", body)


def index(counts):
    items = ''.join(f'<li><a href="{quote(slug)}/">#{html.escape(slug)}</a> <small>({n})</small></li>'
                    for slug, n in sorted(counts.items()) if n)
    return page("Moods' folders", "<h1>Moods' folders</h1><p class=\"sub\">The pictures, video, sound and PDFs "
                "in each Mood's folder on hunter.</p>" + (f'<ul class="moods">{items}</ul>' if items else
                                                          '<p>Nothing to show yet.</p>'))


def write_if_changed(path, text):
    try:
        if path.read_text() == text:
            return
    except OSError:
        pass
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(text)
    os.replace(tmp, path)


def sweep(out_mood, keep):
    """Remove what's no longer shown, and directories left empty."""
    for root, dirs, files in os.walk(out_mood, topdown=False):
        here = Path(root)
        for name in files:
            path = here / name
            rel = path.relative_to(out_mood)
            if rel not in keep and str(rel) != 'index.html':
                path.unlink(missing_ok=True)
        if here != out_mood:
            try:
                here.rmdir()
            except OSError:
                pass


def build(base_dir, out, domain, now=None):
    now = time.time() if now is None else now
    root = base_dir / 'moods'
    out.mkdir(parents=True, exist_ok=True)
    moods = sorted(p for p in root.iterdir() if p.is_dir() and not p.is_symlink() and SLUG.match(p.name)) \
        if root.is_dir() else []
    counts = {}
    for mood in moods:
        files = shown(mood)
        out_mood = out / mood.name
        out_mood.mkdir(exist_ok=True)
        placed = [(rel, st) for rel, st in files if place(mood / rel, out_mood / rel, st)]
        sweep(out_mood, {rel for rel, _ in placed})
        write_if_changed(out_mood / 'index.html', gallery(mood.name, placed, domain, now))
        counts[mood.name] = len(placed)
    for gone in out.iterdir():
        if gone.is_dir() and gone.name not in counts:
            shutil.rmtree(gone, ignore_errors=True)
    write_if_changed(out / 'index.html', index(counts))
    return counts


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--base-dir', default='/opt/magenta', type=Path)
    ap.add_argument('--out', default='/var/lib/mood-folders', type=Path)
    ap.add_argument('--domain', default='hunter.cryptograss.live')
    args = ap.parse_args(argv)
    build(args.base_dir, args.out, args.domain)
    return 0


if __name__ == '__main__':
    sys.exit(main())
