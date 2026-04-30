#!/usr/bin/env python3
"""
Backfill per-track Release pages for an existing record.

Calls delivery-kid's GET /album-tracks/{cid} to discover the per-track
CIDs (which IPFS knows from the original pin), then:

  1. For each track, creates Release:QmFlacCid as a "track"-type
     Release page if it doesn't already exist. FLAC is the canonical
     identity; OGG and other encodings are listed in the YAML.
  2. Updates the album's Release:QmAlbumCid YAML to include a tracks
     array linking each track's canonical CID.

Idempotent — track pages that already exist are left alone.

Auth: pickipedia bot credentials via env vars. Bot password format is
"Username@BotName" for lgname; create one at Special:BotPasswords.

Usage:
    PICKIPEDIA_BOT_USER='Magent@backfill' \\
    PICKIPEDIA_BOT_PASS='...' \\
    ./backfill-album-tracks.py QmUWtV7fG1K9pM5TQSf5c38vmh9MtU6p3VNQuzaxvYr6ep

    # Dry run — fetch + plan, no writes:
    ./backfill-album-tracks.py --dry-run QmUWtV...
"""

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from urllib.error import HTTPError, URLError

try:
    import yaml
except ImportError:
    print("error: PyYAML required. Install with: pip3 install pyyaml", file=sys.stderr)
    sys.exit(2)


DELIVERY_KID_URL = os.environ.get("DELIVERY_KID_URL", "https://delivery-kid.cryptograss.live")
PICKIPEDIA_URL = os.environ.get("PICKIPEDIA_URL", "https://pickipedia.xyz")
USER_AGENT = "BackfillAlbumTracks/1.0 (+magent)"


def http_get_json(url: str, opener=None) -> dict:
    """GET a URL, return parsed JSON. Uses urlopen by default."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    fetch = opener.open if opener else urllib.request.urlopen
    with fetch(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_post_form(url: str, params: dict, opener) -> dict:
    """POST form-encoded params, return parsed JSON. opener carries cookies."""
    body = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={
        "User-Agent": USER_AGENT,
        "Content-Type": "application/x-www-form-urlencoded",
    })
    with opener.open(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


# -- delivery-kid --

def fetch_album_tracks(album_cid: str) -> dict:
    url = f"{DELIVERY_KID_URL}/album-tracks/{album_cid}"
    return http_get_json(url)


# -- pickipedia mw.Api --

def make_mw_session():
    """Create a urllib opener that carries cookies, like a logged-in browser."""
    cj = CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))


def mw_login(opener, username: str, password: str) -> None:
    """Log in via clientlogin (works for bot passwords too)."""
    api = f"{PICKIPEDIA_URL}/api.php"

    # Get login token
    tok = http_post_form(api, {
        "action": "query", "meta": "tokens", "type": "login", "format": "json",
    }, opener)
    login_token = tok["query"]["tokens"]["logintoken"]

    # Bot password login uses action=login (legacy) with lgname/lgpassword
    result = http_post_form(api, {
        "action": "login",
        "lgname": username,
        "lgpassword": password,
        "lgtoken": login_token,
        "format": "json",
    }, opener)
    if result.get("login", {}).get("result") != "Success":
        raise RuntimeError(f"Login failed: {result}")


def mw_get_csrf(opener) -> str:
    api = f"{PICKIPEDIA_URL}/api.php"
    res = http_post_form(api, {
        "action": "query", "meta": "tokens", "format": "json",
    }, opener)
    token = res["query"]["tokens"]["csrftoken"]
    if token == "+\\":
        raise RuntimeError("CSRF token came back anonymous; login likely failed")
    return token


def mw_page_get(opener, title: str) -> dict:
    """Return {exists, content, revid} for a page."""
    api = f"{PICKIPEDIA_URL}/api.php"
    qs = urllib.parse.urlencode({
        "action": "query", "titles": title, "prop": "revisions",
        "rvprop": "content|ids", "rvslots": "main", "format": "json",
    })
    res = http_get_json(f"{api}?{qs}", opener)
    pages = res.get("query", {}).get("pages", {})
    page = next(iter(pages.values()), None) if pages else None
    if not page:
        return {"exists": False, "content": None, "revid": None}
    if "missing" in page:
        return {"exists": False, "content": None, "revid": None}
    rev = (page.get("revisions") or [None])[0]
    if not rev:
        return {"exists": True, "content": "", "revid": None}
    content = rev.get("slots", {}).get("main", {}).get("*") or rev.get("*", "")
    return {"exists": True, "content": content, "revid": rev.get("revid")}


def mw_page_edit(opener, title: str, text: str, summary: str,
                 csrf: str, createonly: bool = False) -> dict:
    api = f"{PICKIPEDIA_URL}/api.php"
    params = {
        "action": "edit", "title": title, "text": text,
        "summary": summary, "token": csrf, "format": "json",
        "contentformat": "text/x-yaml",
    }
    if createonly:
        params["createonly"] = "1"
    res = http_post_form(api, params, opener)
    if res.get("error"):
        raise RuntimeError(f"Edit {title} failed: {res['error']}")
    return res


# -- backfill logic --

def canonical_track_cid(track: dict) -> tuple[str, str]:
    """Pick the canonical CID + source-format for a track.

    FLAC is preferred (lossless, archival source). Falls back to OGG,
    then alphabetically-first encoding. Returns (cid, format_key).
    """
    encodings = track.get("encodings", {})
    for fmt in ("flac", "ogg", "wav", "m4a", "mp3"):
        if fmt in encodings and encodings[fmt].get("cid"):
            return encodings[fmt]["cid"], fmt
    # last-resort: any encoding with a cid
    for fmt, enc in sorted(encodings.items()):
        if enc.get("cid"):
            return enc["cid"], fmt
    raise ValueError(f"track has no encoding with a cid: {track}")


def build_track_yaml(track: dict, canonical_cid: str, canonical_fmt: str,
                     album_cid: str) -> str:
    encodings_block = {fmt: enc["cid"] for fmt, enc in track["encodings"].items()
                       if enc.get("cid")}
    body = {
        "title": track.get("title", ""),
        "release_type": "track",
        "parent_release": album_cid,
        "track_number": track.get("track_number"),
        "canonical_format": canonical_fmt,
        "encodings": encodings_block,
    }
    # Per-encoding sizes are useful for the editor / catalog UI.
    sizes = {fmt: enc["size"] for fmt, enc in track["encodings"].items()
             if enc.get("size")}
    if sizes:
        body["encoding_sizes"] = sizes
    return yaml.safe_dump(body, sort_keys=False, default_flow_style=False)


def merge_album_tracks_yaml(existing_yaml: str, tracks_array: list) -> str:
    """Add/replace `tracks:` in the album's YAML, preserving everything else."""
    data = yaml.safe_load(existing_yaml) or {}
    if not isinstance(data, dict):
        raise RuntimeError("album YAML root is not a mapping")
    data["tracks"] = tracks_array
    return yaml.safe_dump(data, sort_keys=False, default_flow_style=False)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("album_cid")
    p.add_argument("--dry-run", action="store_true",
                   help="Plan only — fetch listing, log what would be written, no writes.")
    args = p.parse_args()

    # Plan phase: works without auth.
    print(f"== Fetching album-tracks for {args.album_cid} ==", flush=True)
    album = fetch_album_tracks(args.album_cid)
    tracks = album.get("tracks", [])
    if not tracks:
        print("error: no tracks returned from delivery-kid", file=sys.stderr)
        return 1

    plan = []
    for t in tracks:
        cid, fmt = canonical_track_cid(t)
        plan.append({"track_cid": cid, "fmt": fmt, "track": t})

    print(f"  {len(plan)} tracks discovered:")
    for p_ in plan:
        t = p_["track"]
        print(f"    #{t.get('track_number')}: {t.get('title')!r}")
        print(f"      canonical {p_['fmt']}: {p_['track_cid']}")
        for fmt, enc in t["encodings"].items():
            if fmt != p_["fmt"]:
                print(f"      also     {fmt}: {enc.get('cid')}")

    if args.dry_run:
        print("\n(dry-run: no pages written)")
        return 0

    # Auth + write phase.
    user = os.environ.get("PICKIPEDIA_BOT_USER")
    password = os.environ.get("PICKIPEDIA_BOT_PASS")
    if not user or not password:
        print("error: set PICKIPEDIA_BOT_USER and PICKIPEDIA_BOT_PASS, or pass --dry-run",
              file=sys.stderr)
        return 2

    print(f"\n== Logging in to {PICKIPEDIA_URL} as {user} ==", flush=True)
    opener = make_mw_session()
    mw_login(opener, user, password)
    csrf = mw_get_csrf(opener)
    print("  ok")

    # Per-track pages (skip if exists).
    print(f"\n== Materializing {len(plan)} per-track Release pages ==", flush=True)
    for p_ in plan:
        title = f"Release:{p_['track_cid']}"
        existing = mw_page_get(opener, title)
        if existing["exists"]:
            print(f"  - {title}: already exists (skip)")
            continue
        body = build_track_yaml(p_["track"], p_["track_cid"], p_["fmt"], args.album_cid)
        mw_page_edit(opener, title, body,
                     f"Backfill: track #{p_['track']['track_number']} of {args.album_cid}",
                     csrf, createonly=True)
        print(f"  + {title}: created")

    # Album page: add tracks: array.
    print(f"\n== Updating album Release:{args.album_cid} ==", flush=True)
    album_title = f"Release:{args.album_cid}"
    existing = mw_page_get(opener, album_title)
    if not existing["exists"]:
        print(f"error: {album_title} does not exist", file=sys.stderr)
        return 1
    tracks_array = [{
        "cid": p_["track_cid"],
        "title": p_["track"].get("title", ""),
        "track_number": p_["track"].get("track_number"),
    } for p_ in plan]
    new_yaml = merge_album_tracks_yaml(existing["content"], tracks_array)
    if new_yaml.strip() == (existing["content"] or "").strip():
        print("  no changes")
    else:
        mw_page_edit(opener, album_title, new_yaml,
                     f"Backfill: add tracks array ({len(tracks_array)} tracks)", csrf)
        print(f"  updated")

    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
