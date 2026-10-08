#!/usr/bin/env python3
"""Audit delivery-kid storage against PickiPedia Release/ReleaseDraft pages.

Cross-references IPFS pins, BitTorrent seeding dirs, and staging draft dirs
against what's actually tracked in the wiki. Flags orphans in both directions
so nothing falls through the cracks.

Checks performed:
  1. IPFS pins vs Release pages
     - ORPHAN PIN: pinned but no Release page
     - MISSING PIN: Release page but not pinned (unless delete/unpin flag)
     - DELETED / RETIRED: Release pages flagged delete/unpin (and pin status)

  2. BitTorrent seeding dirs vs Release pages
     - ORPHAN SEED: seeding dir but no Release page
     - MISSING SEED: Release page but no seeding dir

  3. Staging drafts vs ReleaseDraft pages
     - ORPHAN DRAFT: staging dir but no wiki page
     - STALLED DRAFT: wiki page + empty/incomplete staging (no draft.json or empty upload/)
     - DEAD WIKI DRAFT: wiki page but no staging, never finalized
     - ABANDONED DRAFT: wiki page flagged `abandoned: true` (shown separately,
       with alive infra flagged as CLEANUP PENDING)

  4. Blue Railroad chain data vs Release pages
     - Delegates to audit-chain-data.py on maybelle

Usage: maybelle/scripts/audit-storage.py
"""

import json
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

import yaml


DK_HOST = "root@delivery-kid.cryptograss.live"
MAYBELLE_HOST = "root@maybelle.cryptograss.live"
WIKI_API = "https://pickipedia.xyz/api.php"

# Every kubo node has this empty directory pinned, so it always appears in pin listings.
IPFS_EMPTY_DIR = "qmunllspaccz1vlxqvkxqqlx5r1x345qqfhbsf67hva3nn"


def ssh(host: str, cmd: str) -> str:
    """Run a command on a remote host via SSH, return stdout. Empty string on failure."""
    result = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", host, cmd],
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        print(f"  [ssh {host} failed: {result.stderr.strip()}]", file=sys.stderr)
        return ""
    return result.stdout


def wiki_get(params: dict) -> dict:
    params = {**params, "format": "json"}
    url = f"{WIKI_API}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read().decode())


def allpages(namespace: int) -> list[str]:
    """Return all page titles (after the colon) in a namespace."""
    titles = []
    cont = None
    while True:
        params = {"action": "query", "list": "allpages",
                  "apnamespace": str(namespace), "aplimit": "500"}
        if cont:
            params["apcontinue"] = cont
        data = wiki_get(params)
        for p in data.get("query", {}).get("allpages", []):
            t = p["title"]
            titles.append(t.split(":", 1)[1] if ":" in t else t)
        cont = data.get("continue", {}).get("apcontinue")
        if not cont:
            break
    return titles


def page_content(title: str) -> str:
    data = wiki_get({"action": "query", "titles": title,
                     "prop": "revisions", "rvprop": "content", "rvslots": "main"})
    for p in data.get("query", {}).get("pages", {}).values():
        for r in p.get("revisions", []):
            return r.get("slots", {}).get("main", {}).get("*", "") or ""
    return ""


def page_comments(title: str, limit: int = 50) -> list[str]:
    data = wiki_get({"action": "query", "titles": title, "prop": "revisions",
                     "rvprop": "comment", "rvlimit": str(limit)})
    comments = []
    for p in data.get("query", {}).get("pages", {}).values():
        for r in p.get("revisions", []):
            comments.append(r.get("comment") or "")
    return comments


def fetch_releaselist() -> list[dict]:
    """Use the wiki's releaselist API module to get all releases + their YAML fields."""
    with urllib.request.urlopen(f"{WIKI_API}?action=releaselist&format=json", timeout=30) as resp:
        return json.loads(resp.read().decode()).get("releases", [])


def fetch_pins() -> dict[str, str]:
    """Recursive pins on the delivery-kid IPFS node, as {lowercase: as-published}.

    Comparisons here are case-insensitive because Release page titles are not
    reliably cased, but CIDs *are* case-sensitive — a lowercased CID is not a
    valid address and any wiki link built from one is a redlink. Keeping both
    forms lets us compare loosely and print correctly.
    """
    out = ssh(DK_HOST, "docker exec ipfs ipfs pin ls --type=recursive -q 2>/dev/null")
    pins = {}
    for line in out.splitlines():
        cid = line.strip()
        if cid:
            pins[cid.lower()] = cid
    return pins


def fetch_seeding_dirs() -> list[str]:
    out = ssh(DK_HOST, "ls /mnt/storage-box/staging/seeding/ 2>/dev/null")
    return [line.strip() for line in out.splitlines() if line.strip()]


def parse_originals(out: str) -> list[dict]:
    """[{id, size_kb, files}, ...] from the originals listing."""
    found = []
    for line in (out or "").splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        try:
            found.append({"id": parts[0], "size_kb": int(parts[1]), "files": int(parts[2])})
        except ValueError:
            continue
    return found


def fetch_originals() -> list[dict]:
    """Uploads kept by "Keep original file", one entry per draft.

    These are the largest things delivery-kid stores on purpose and, until
    this, the only ones the audit never looked at. One ssh for all of them.
    """
    script = r"""
cd /mnt/storage-box/staging/originals 2>/dev/null || exit 0
for d in */; do
  d=${d%/}
  [ -z "$d" ] && continue
  size_kb=$(du -sk "$d" 2>/dev/null | cut -f1)
  files=$(find "$d" -type f 2>/dev/null | wc -l | tr -d ' ')
  echo "$d ${size_kb:-0} ${files:-0}"
done
"""
    return parse_originals(ssh(DK_HOST, script))


def split_originals(originals: list[dict], wiki_draft_ids: list[str]) -> tuple[list[dict], list[dict]]:
    """(kept for a draft we know about, kept for a draft with no page).

    A kept original is deliberate, so it is history rather than a problem.
    One whose ReleaseDraft page is gone is bytes nobody can trace back to
    a release, which is worth a person's attention.
    """
    known = {w.lower() for w in wiki_draft_ids}
    kept, orphaned = [], []
    for o in originals:
        (kept if o["id"].lower() in known else orphaned).append(o)
    return kept, orphaned


def print_originals(kept: list[dict], orphaned: list[dict]):
    every = kept + orphaned
    if not every:
        return
    total = sum(o["size_kb"] for o in every)
    print(f"  KEPT ORIGINALS ({len(every)}, {human_size(total)} total) — uploads saved by "
          f"\"Keep original file\", largest first. Deliberate; listed so their cost is visible:")
    for o in sorted(every, key=lambda o: o["size_kb"], reverse=True):
        mark = "" if o in kept else "   ← no ReleaseDraft page"
        print(f"    {o['id']} ({human_size(o['size_kb'])}, {o['files']} file"
              f"{'' if o['files'] == 1 else 's'}){mark}")


def fetch_staging_drafts() -> list[dict]:
    """Return [{id, has_draft_json, upload_files, size_kb, mtime}, ...]."""
    script = r"""
cd /mnt/storage-box/staging/drafts 2>/dev/null || exit 0
for d in */; do
  d=${d%/}
  [ -z "$d" ] && continue
  has_json=no; [ -f "$d/draft.json" ] && has_json=yes
  upload_files=0
  [ -d "$d/upload" ] && upload_files=$(find "$d/upload" -type f 2>/dev/null | wc -l | tr -d ' ')
  size_kb=$(du -sk "$d" 2>/dev/null | cut -f1)
  # Newest file mtime in the tree — falls back to dir mtime when empty.
  mtime=$(find "$d" -type f -printf '%T@\n' 2>/dev/null | sort -nr | head -1 | cut -d. -f1)
  [ -z "$mtime" ] && mtime=$(stat -c %Y "$d" 2>/dev/null)
  [ -z "$mtime" ] && mtime=0
  echo "$d $has_json $upload_files $size_kb $mtime"
done
"""
    out = ssh(DK_HOST, script)
    drafts = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 4:
            drafts.append({
                "id": parts[0],
                "has_draft_json": parts[1] == "yes",
                "upload_files": int(parts[2] or 0),
                "size_kb": int(parts[3] or 0),
                "mtime": int(parts[4]) if len(parts) >= 5 else 0,
            })
    return drafts


def human_size(kb: int) -> str:
    if kb < 1024:
        return f"{kb}K"
    if kb < 1024 * 1024:
        return f"{kb // 1024}M"
    return f"{kb / 1024 / 1024:.1f}G"


def human_age(epoch: int) -> str:
    """Render a unix-epoch timestamp as 'just now' / '14m' / '3d' style age."""
    if not epoch:
        return ""
    import time as _time
    delta = max(0, int(_time.time()) - epoch)
    if delta < 60:
        return f"{delta}s"
    if delta < 3600:
        return f"{delta // 60}m"
    if delta < 86400:
        return f"{delta // 3600}h"
    return f"{delta // 86400}d"


def audit_pins(releases: list[dict], pins: dict[str, str], seeding: list[str]) -> dict:
    """Cross-reference Release pages against IPFS pins + seeding dirs.

    Captures per-release state (pinned, seeded, pinned_on) so the summary can
    flag cases where YAML says delete/unpin but the infrastructure is still
    alive — the "cleanup pending" state.
    """
    release_cids = {r.get("ipfs_cid", "").lower() for r in releases if r.get("ipfs_cid")}
    seed_cids = {s.lower() for s in seeding}

    orphan_pins = []
    for pin in pins:
        if pin == IPFS_EMPTY_DIR:
            continue
        if pin not in release_cids:
            # Print the CID as published, not as compared — see fetch_pins.
            orphan_pins.append(pins[pin] if isinstance(pins, dict) else pin)

    missing_pins, deleted, retired = [], [], []
    for r in releases:
        cid = r.get("ipfs_cid") or r.get("page_title") or ""
        title = r.get("title") or cid[:16]
        pinned = cid.lower() in pins
        seeded = cid.lower() in seed_cids

        # Parse YAML for delete/unpin flags + pinned_on
        ydata = {}
        try:
            raw = page_content(f"Release:{cid}")
            parsed = yaml.safe_load(raw) if raw else None
            if isinstance(parsed, dict):
                ydata = parsed
        except Exception:
            pass

        pinned_on = ydata.get("pinned_on") or []
        entry = {"cid": cid, "title": title, "pinned": pinned,
                 "seeded": seeded, "pinned_on": pinned_on}
        if ydata.get("delete"):
            deleted.append(entry)
        elif ydata.get("unpin"):
            retired.append(entry)
        elif not pinned:
            missing_pins.append(entry)

    deliberately_unpinned = {e["cid"].lower() for e in deleted + retired}
    return {"orphan_pins": orphan_pins, "missing_pins": missing_pins,
            "deleted": deleted, "retired": retired,
            "deliberately_unpinned": deliberately_unpinned}


def audit_seeding(releases: list[dict], seeding: list[str],
                  deliberately_unpinned: set[str]) -> dict:
    """Releases flagged delete/unpin are expected NOT to be seeded.
    Skip them in both orphan and missing checks — pin audit already
    flags any lingering infra as CLEANUP PENDING."""
    release_cids = {
        r.get("ipfs_cid", "").lower() for r in releases
        if r.get("ipfs_cid") and r.get("ipfs_cid", "").lower() not in deliberately_unpinned
    }
    seed_cids = {s.lower() for s in seeding}

    orphan_seeds = [
        s for s in seeding
        if s.lower() not in release_cids and s.lower() not in deliberately_unpinned
    ]
    missing_seeds = sorted(release_cids - seed_cids - {""})
    return {"orphan_seeds": orphan_seeds, "missing_seeds": missing_seeds}


def fetch_abandoned_drafts(wiki_draft_ids: list[str]) -> dict[str, dict]:
    """Return {draft_id_lower: {reason, keep_files}} for drafts flagged
    `abandoned: true`. ``keep_files`` reflects ``abandoned_keep_files``,
    which the user can set to signal that staging files are intentionally
    being kept around (so the audit treats them as "kept", not pending)."""
    abandoned = {}
    for w in wiki_draft_ids:
        try:
            raw = page_content(f"ReleaseDraft:{w}")
            data = yaml.safe_load(raw) if raw else None
        except Exception:
            continue
        if isinstance(data, dict) and data.get("abandoned"):
            abandoned[w.lower()] = {
                "reason": data.get("abandoned_reason") or "",
                "keep_files": bool(data.get("abandoned_keep_files")),
            }
    return abandoned


def audit_drafts(
    wiki_draft_ids: list[str],
    staging_drafts: list[dict],
    abandoned: dict[str, dict],
) -> dict:
    staging_by_id = {d["id"].lower(): d for d in staging_drafts}
    wiki_lower = {w.lower(): w for w in wiki_draft_ids}

    orphan_drafts = []      # staging dir, no wiki page
    stalled_drafts = []     # wiki page + no draft.json + empty upload/
    dead_wiki_drafts = []   # wiki page, no staging, never finalized
    unknown_drafts = []     # wiki page, no staging, could not tell — never
                            # recommend deleting one of these
    finalized_gone = []     # wiki page, no staging, WAS finalized (expected)
    abandoned_drafts = []   # wiki page flagged `abandoned: true`

    for d in staging_drafts:
        lower = d["id"].lower()
        if lower not in wiki_lower:
            orphan_drafts.append(d)
            continue
        if lower in abandoned:
            info = abandoned[lower]
            abandoned_drafts.append({
                **d, "wiki_title": wiki_lower[lower],
                "reason": info["reason"], "has_staging": True,
                "keep_files": info["keep_files"],
            })
        elif not d["has_draft_json"] and d["upload_files"] == 0:
            stalled_drafts.append({**d, "wiki_title": wiki_lower[lower]})

    seen_abandoned_with_staging = {a["wiki_title"].lower() for a in abandoned_drafts}

    for w in wiki_draft_ids:
        lower = w.lower()
        if lower in staging_by_id:
            continue
        if lower in abandoned and lower not in seen_abandoned_with_staging:
            info = abandoned[lower]
            abandoned_drafts.append({
                "wiki_title": w, "reason": info["reason"], "has_staging": False,
                "keep_files": info["keep_files"],
            })
            continue
        # Was this ever finalized? The answer decides whether the report
        # says "expected" or "safe to delete from wiki", so a lookup that
        # did not happen must not be read as a no. Swallowing the error and
        # defaulting to False meant one wiki hiccup could recommend deleting
        # the record of a published release.
        is_finalized = False
        known = True
        try:
            for c in page_comments(f"ReleaseDraft:{w}"):
                if "pinned to ipfs" in c.lower():
                    is_finalized = True
                    break
        except Exception as exc:
            known = False
            print(f"    could not read comments for {w}: {exc}", file=sys.stderr)

        if is_finalized:
            finalized_gone.append(w)
        elif known:
            dead_wiki_drafts.append(w)
        else:
            unknown_drafts.append(w)

    return {"orphan_drafts": orphan_drafts, "stalled_drafts": stalled_drafts,
            "dead_wiki_drafts": dead_wiki_drafts, "finalized_gone": finalized_gone,
            "abandoned_drafts": abandoned_drafts,
            "unknown_drafts": unknown_drafts}


def parse_dag_size(raw: str) -> Optional[int]:
    """Bytes from one `ipfs dag stat` reply, whatever shape it arrives in.

    Kubo has printed this several ways across versions — JSON with
    TotalSize, JSON with Size, and a plain "Size: N, NumBlocks: M" line —
    so all three are accepted rather than pinning the audit to one release
    of the node.
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            for key in ("TotalSize", "Size", "CumulativeSize"):
                value = parsed.get(key)
                if isinstance(value, (int, float)):
                    return int(value)
    except json.JSONDecodeError:
        pass
    match = re.search(r"(?:Total\s*)?Size:?\s*([0-9][0-9,]*)", raw, re.IGNORECASE)
    if match:
        return int(match.group(1).replace(",", ""))
    return None


def fetch_pin_sizes(cids: list[str]) -> dict[str, Optional[int]]:
    """Total DAG size of each pin, in bytes, as {cid: bytes or None}.

    Without this the orphan list cannot be acted on: eleven addresses might
    be twenty megabytes or two gigabytes and the report reads identically
    either way, so the only honest recommendation was "look into it".

    One ssh for the whole batch. `dag stat` walks the graph, so this is not
    free, but it runs over orphans only — a handful, not all 66 pins.
    """
    if not cids:
        return {}
    parts = [
        f'printf "%s\\t" {cid}; '
        f'docker exec ipfs ipfs dag stat --progress=false --enc=json {cid} '
        f'2>/dev/null | tr -d "\\n"; printf "\\n"'
        for cid in cids
    ]
    out = ssh(DK_HOST, "; ".join(parts))
    sizes: dict[str, Optional[int]] = {cid: None for cid in cids}
    for line in out.splitlines():
        if "\t" not in line:
            continue
        cid, _, raw = line.partition("\t")
        cid = cid.strip()
        if cid in sizes:
            sizes[cid] = parse_dag_size(raw)
    return sizes


def fetch_pin_metadata(cid: str) -> Optional[dict]:
    """Read metadata.json out of a pinned directory, if it has one.

    Finalization writes this file alongside the media, so it is the only thing
    tying a pin back to what it was meant to be.
    """
    out = ssh(DK_HOST, f"docker exec ipfs ipfs cat /ipfs/{cid}/metadata.json 2>/dev/null")
    if not out.strip():
        return None
    try:
        parsed = json.loads(out)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def is_coconut_remnant(meta: Optional[dict]) -> bool:
    """A pin that is only the paperwork of a Coconut transcode that produced
    nothing.

    Coconut was the hosted transcoder, removed in #129. When it failed it
    still got its metadata pinned: method "coconut", no qualities, no
    variants, zero output bytes — a directory holding one 485-byte
    metadata.json and no media at all.

    Eleven of these total five kilobytes. There is nothing to reclaim, and
    they are the only surviving record that those uploads happened, so they
    are reported apart from the problems rather than deleted or counted as
    one.
    """
    if not isinstance(meta, dict):
        return False
    transcode = meta.get("transcode")
    if not isinstance(transcode, dict):
        return False
    if str(transcode.get("method", "")).lower() != "coconut":
        return False
    if transcode.get("total_output_size_bytes") not in (0, None, "0"):
        return False
    # A Coconut run that actually produced something is a different animal:
    # it has media, and an orphan pin holding media is a real finding.
    return not transcode.get("qualities") and not transcode.get("variants")


def split_coconut_remnants(orphan_pins: list[str],
                           metadata: dict) -> tuple[list[str], list[dict]]:
    """Partition orphan pins into (still worth attention, Coconut remnants)."""
    remaining, remnants = [], []
    for cid in orphan_pins:
        meta = metadata.get(cid)
        if is_coconut_remnant(meta):
            remnants.append({
                "cid": cid,
                "title": (meta or {}).get("title"),
                "uploaded_by": (meta or {}).get("uploaded_by"),
                "created_at": (meta or {}).get("created_at"),
            })
        else:
            remaining.append(cid)
    return remaining, remnants


def _normalise_title(title: str) -> str:
    return " ".join(str(title or "").lower().split())


def correlate_unrecorded_publishes(orphan_pins: list[str],
                                   dead_wiki_drafts: list[str],
                                   metadata: Optional[dict] = None) -> list[dict]:
    """Pair orphan pins with drafts that published without recording it.

    The reasoning that makes this worth doing:

    Staging for a draft is only ever removed after a *successful* pin. So a
    wiki draft with no staging and no "pinned to IPFS" record did not fail —
    it succeeded, and then lost its paperwork before anything wrote the CID
    down. Its content is almost certainly sitting in the orphan pin list.

    Until now the audit reported both halves of that and drew no line between
    them: an orphan pin appeared, a draft went dead, and the recommendation
    was "safe to delete from wiki" — pointing at the only surviving reference
    to a published, seeding, sixty-six-minute video. Reconstructing the link
    by hand is what recovered it; doing it here costs one `ipfs cat` per
    orphan pin.

    Returns [{cid, draft, title, confidence}, ...], strongest first.
    """
    if not orphan_pins or not dead_wiki_drafts:
        return []

    draft_titles = {}
    for draft in dead_wiki_drafts:
        try:
            parsed = yaml.safe_load(page_content(f"ReleaseDraft:{draft}")) or {}
            content = parsed.get("content") or {}
            title = content.get("title") if isinstance(content, dict) else None
            if title:
                draft_titles.setdefault(_normalise_title(title), []).append(draft)
        except Exception:
            continue

    matches = []
    unmatched_pins = []
    for cid in orphan_pins:
        # Already read once for the Coconut split; one ssh per pin is enough.
        meta = metadata.get(cid) if metadata is not None else fetch_pin_metadata(cid)
        if not meta:
            unmatched_pins.append(cid)
            continue
        key = _normalise_title(meta.get("title", ""))
        if key and key in draft_titles:
            for draft in draft_titles[key]:
                matches.append({
                    "cid": cid,
                    "draft": draft,
                    "title": meta.get("title"),
                    "uploaded_by": meta.get("uploaded_by"),
                    "confidence": "title match",
                })
        else:
            unmatched_pins.append(cid)

    # Even without a title match the coincidence is worth stating: a dead
    # draft means a pin happened, so an orphan pin alongside one is a
    # candidate rather than a curiosity.
    matched_drafts = {m["draft"] for m in matches}
    for draft in dead_wiki_drafts:
        if draft not in matched_drafts and unmatched_pins:
            matches.append({
                "cid": None,
                "draft": draft,
                "title": None,
                "uploaded_by": None,
                "confidence": "unmatched — staging was cleaned, so this draft "
                              "probably published; check the orphan pins",
            })

    matches.sort(key=lambda m: 0 if m["confidence"] == "title match" else 1)
    return matches


def print_unrecorded_publishes(matches: list[dict]):
    if not matches:
        return
    print(f"  UNRECORDED PUBLISHES ({len(matches)}) — content that appears to be "
          f"pinned but was never written down:")
    for m in matches:
        if m["cid"]:
            print(f"    [[Release:{m['cid']}|{m['cid']}]]")
            print(f"      looks like [[ReleaseDraft:{m['draft']}]] — "
                  f"\"{m['title']}\" ({m['confidence']})")
            if m.get("uploaded_by"):
                print(f"      uploaded by {m['uploaded_by']}")
        else:
            print(f"    [[ReleaseDraft:{m['draft']}]] — {m['confidence']}")
    print("    Staging is only removed after a successful pin, so these drafts")
    print("    did not fail — they published and lost the record. Do NOT delete")
    print("    these pages; write the CID back onto the draft instead.")


def print_section(title: str):
    print(f"\n--- {title} ---")


def _alive_flags(entry: dict) -> list[str]:
    flags = []
    if entry.get("pinned"):
        flags.append("pinned")
    if entry.get("seeded"):
        flags.append("seeded")
    if entry.get("pinned_on"):
        flags.append(f"pinned_on={','.join(entry['pinned_on'])}")
    return flags


def print_pin_audit(result: dict, release_count: int):
    # Only enumerate deleted/retired releases that have alive infra
    # (CLEANUP PENDING) — fully-cleaned ones are history and would otherwise
    # pile up over time. Counts of all are rolled up below.
    if result["deleted"]:
        pending = [r for r in result["deleted"] if _alive_flags(r)]
        print(f"  Deleted releases ({len(result['deleted'])} total, "
              f"{len(pending)} cleanup pending):")
        for r in pending:
            alive = _alive_flags(r)
            print(f"    {r['cid']} {r['title']} [CLEANUP PENDING: {', '.join(alive)}]")
    if result["retired"]:
        pending = [r for r in result["retired"] if _alive_flags(r)]
        print(f"  Retired releases ({len(result['retired'])} total, "
              f"{len(pending)} cleanup pending):")
        for r in pending:
            alive = _alive_flags(r)
            print(f"    {r['cid']} {r['title']} [CLEANUP PENDING: {', '.join(alive)}]")
    if result["missing_pins"]:
        print(f"  MISSING PINS ({len(result['missing_pins'])}):")
        for r in result["missing_pins"]:
            print(f"    {r['cid']} {r['title']}")
    if result["orphan_pins"]:
        sizes = result.get("orphan_pin_sizes") or {}
        known = [s for s in sizes.values() if isinstance(s, int)]
        total = f", {human_size(sum(known) // 1024)} total" if known else ""
        print(f"  ORPHAN PINS ({len(result['orphan_pins'])}{total}) — "
              f"pinned but no Release page:")
        # Largest first: if one of these is worth reclaiming it is the one
        # at the top, and if the whole list is small that is visible at a
        # glance rather than after eleven lookups.
        for cid in sorted(result["orphan_pins"],
                          key=lambda c: sizes.get(c) if isinstance(sizes.get(c), int) else -1,
                          reverse=True):
            size = sizes.get(cid)
            shown = human_size(size // 1024) if isinstance(size, int) else "size unknown"
            print(f"    {cid} ({shown})")

    total = release_count
    d, r, m = len(result["deleted"]), len(result["retired"]), len(result["missing_pins"])
    active = total - d - r
    print(f"  {active} active, {d} deleted, {r} retired, {m} missing pins")


def print_seeding_audit(result: dict, seed_count: int):
    if result["orphan_seeds"]:
        print(f"  ORPHAN SEEDS ({len(result['orphan_seeds'])}) — seeding dir but no Release page:")
        for s in result["orphan_seeds"]:
            size = ssh(DK_HOST, f"du -sh /mnt/storage-box/staging/seeding/{s} 2>/dev/null | cut -f1").strip()
            print(f"    {s} ({size or '?'})")
    if result["missing_seeds"]:
        print(f"  MISSING SEEDS ({len(result['missing_seeds'])}) — Release page but no seeding dir:")
        for c in result["missing_seeds"]:
            print(f"    {c}")
    print(f"  {seed_count} total seeding dirs, "
          f"{len(result['orphan_seeds'])} orphaned, "
          f"{len(result['missing_seeds'])} missing")


def print_coconut_remnants(remnants: list[dict], sizes: dict):
    if not remnants:
        return
    known = [sizes.get(r["cid"]) for r in remnants]
    known = [s for s in known if isinstance(s, int)]
    total = f", {human_size(sum(known) // 1024)}" if known else ""
    print(f"  COCONUT REMNANTS ({len(remnants)}{total}) — metadata-only pins "
          f"from the removed hosted transcoder. No media, nothing to "
          f"reclaim; kept as the only record those uploads happened:")
    for r in sorted(remnants, key=lambda r: r.get("created_at") or ""):
        when = (r.get("created_at") or "")[:10]
        who = (r.get("uploaded_by") or "unknown").replace("wiki:", "")
        title = r.get("title") or "(no title recorded)"
        print(f"    {when}  {who:16} {title}")


def print_draft_audit(result: dict, wiki_count: int, staging_count: int):
    if result["orphan_drafts"]:
        print(f"  ORPHAN DRAFTS ({len(result['orphan_drafts'])}) — staging dir, no wiki page:")
        for d in result["orphan_drafts"]:
            age = human_age(d.get("mtime", 0))
            age_part = f", {age} old" if age else ""
            print(f"    {d['id']} ({human_size(d['size_kb'])}{age_part})")
    if result["stalled_drafts"]:
        print(f"  STALLED DRAFTS ({len(result['stalled_drafts'])}) — wiki page + empty staging:")
        for d in result["stalled_drafts"]:
            print(f"    {d['id']} (no draft.json, upload/ empty)")
    if result["dead_wiki_drafts"]:
        print(f"  DEAD WIKI DRAFTS ({len(result['dead_wiki_drafts'])}) — "
              f"never finalized, no staging (safe to delete from wiki):")
        for w in result["dead_wiki_drafts"]:
            print(f"    {w}")
    if result.get("unknown_drafts"):
        print(f"  UNDETERMINED ({len(result['unknown_drafts'])}) — could not read "
              f"the page's comments, so whether these finalized is unknown. "
              f"Do not delete on the strength of this run:")
        for w in result["unknown_drafts"]:
            print(f"    {w}")
    if result["abandoned_drafts"]:
        print(f"  ABANDONED DRAFTS ({len(result['abandoned_drafts'])}) — flagged `abandoned: true`:")
        for a in result["abandoned_drafts"]:
            if not a["has_staging"]:
                state = "clean"
            elif a.get("keep_files"):
                # User explicitly asked to keep the files; not pending.
                state = "files kept"
            else:
                state = "CLEANUP PENDING: staging dir"
            reason = f" — {a['reason']}" if a["reason"] else ""
            print(f"    {a['wiki_title'][:36]} [{state}]{reason}")
    if result["finalized_gone"]:
        print(f"  {len(result['finalized_gone'])} finalized wiki drafts without staging "
              f"(expected — staging cleaned on finalize)")
    print(f"  {wiki_count} wiki drafts, {staging_count} staging dirs")


def main():
    print("=== Storage Audit ===\n")

    print("Fetching Release pages from wiki...")
    releases = fetch_releaselist()
    release_count = len(releases)
    print(f"  {release_count} Release pages")

    print("Fetching ReleaseDraft pages from wiki...")
    all_draft_pages = allpages(3006)
    # A draft's own subpages — ReleaseDraft:<id>/diagnostics — are pages in
    # the namespace but they are not drafts. Counted as drafts they have no
    # staging directory and were never finalized on their own, so every
    # successful publish produced a "DEAD WIKI DRAFT ... safe to delete from
    # wiki" line pointing at the only surviving record of that upload: what
    # ffmpeg said, which tracks were skipped, how far the bytes got.
    #
    # pickipedia#114 fixed this same confusion in the extension. This path
    # never got it.
    wiki_draft_ids = [d for d in all_draft_pages if "/" not in d]
    subpages = [d for d in all_draft_pages if "/" in d]
    draft_count = len(wiki_draft_ids)
    print(f"  {draft_count} ReleaseDraft pages"
          + (f" ({len(subpages)} subpages, not counted as drafts)"
             if subpages else ""))

    print("Fetching IPFS pins from delivery-kid...")
    pins = fetch_pins()
    pin_count = len(pins)
    print(f"  {pin_count} recursive pins")

    print("Fetching seeding directories...")
    seeding = fetch_seeding_dirs()
    seed_count = len(seeding)
    print(f"  {seed_count} seeding directories")

    print("Fetching staging drafts...")
    staging_drafts = fetch_staging_drafts()
    staging_count = len(staging_drafts)
    print(f"  {staging_count} staging draft directories")

    print_section("IPFS Pins vs Release Pages")
    pin_result = audit_pins(releases, pins, seeding)
    # Measure the orphans before reporting them. audit_pins stays free of
    # I/O so it can be tested; the sizes are attached here.
    pin_result["orphan_pin_sizes"] = fetch_pin_sizes(pin_result["orphan_pins"])
    # Read each orphan's metadata once, here, and reuse it for both the
    # Coconut split and the unrecorded-publish correlation below.
    orphan_metadata = {cid: fetch_pin_metadata(cid)
                       for cid in pin_result["orphan_pins"]}
    pin_result["orphan_pins"], pin_result["coconut_remnants"] = \
        split_coconut_remnants(pin_result["orphan_pins"], orphan_metadata)
    print_pin_audit(pin_result, release_count)
    print_coconut_remnants(pin_result["coconut_remnants"],
                           pin_result["orphan_pin_sizes"])

    print_section("Seeding Dirs vs Release Pages")
    seed_result = audit_seeding(releases, seeding, pin_result["deliberately_unpinned"])
    print_seeding_audit(seed_result, seed_count)

    print("Scanning wiki drafts for `abandoned: true`...")
    abandoned = fetch_abandoned_drafts(wiki_draft_ids)
    print(f"  {len(abandoned)} abandoned")

    print_section("Staging Drafts vs Wiki ReleaseDraft Pages")
    draft_result = audit_drafts(wiki_draft_ids, staging_drafts, abandoned)
    print_draft_audit(draft_result, draft_count, staging_count)

    print_section("Kept Originals")
    kept_originals, orphan_originals = split_originals(fetch_originals(), wiki_draft_ids)
    print_originals(kept_originals, orphan_originals)

    # Both halves of the "published but unrecorded" story are now known: an
    # orphan pin exists, and a draft went dead. Neither alone means much;
    # together they are the signature of a finalize that succeeded and lost
    # its record. Correlate them rather than leaving a reader to notice.
    unrecorded = correlate_unrecorded_publishes(
        pin_result["orphan_pins"], draft_result["dead_wiki_drafts"],
        metadata=orphan_metadata
    )
    if unrecorded:
        print_section("Possibly Published But Unrecorded")
        print_unrecorded_publishes(unrecorded)

    print_section("Blue Railroad Chain Data vs Releases")
    # Flush our own stdout buffer before launching a subprocess that writes
    # to the same pipe — otherwise, when stdout is captured (not a TTY),
    # the child's output appears *before* our buffered prints in the output.
    sys.stdout.flush()
    script_dir = Path(__file__).parent
    chain_audit = script_dir / "audit-chain-data.py"
    if chain_audit.exists():
        try:
            with open(chain_audit) as f:
                script_body = f.read()
            # When running ON maybelle (host cron path), skip the SSH and
            # docker exec straight in. Otherwise (laptop invocation) SSH to
            # maybelle and pipe through. BatchMode keeps the cron path from
            # ever blocking on an interactive password prompt if the SSH
            # self-loopback is misconfigured.
            import socket
            on_maybelle = (
                socket.gethostname().lower().startswith("maybelle")
                or socket.getfqdn().lower().startswith("maybelle")
                # /etc/delivery-kid-audit.env is created by ansible only on
                # maybelle — its presence is a definitive marker.
                or Path("/etc/delivery-kid-audit.env").exists()
            )
            if on_maybelle:
                cmd = ["docker", "exec", "-i", "jenkins", "python3", "-"]
            else:
                cmd = [
                    "ssh", "-o", "BatchMode=yes",
                    "-o", "ConnectTimeout=10",
                    MAYBELLE_HOST,
                    "docker exec -i jenkins python3 -",
                ]
            subprocess.run(cmd, input=script_body, text=True, timeout=120)
        except Exception as e:
            print(f"  (could not check chain data: {e})")
    else:
        print(f"  (missing {chain_audit})")

    print_section("Summary")
    print(f"  Release pages:       {release_count}")
    print(f"  ReleaseDraft pages:  {draft_count}")
    print(f"  IPFS pins:           {pin_count}")
    print(f"  Seeding dirs:        {seed_count}")
    print(f"  Staging drafts:      {staging_count}")
    print()
    print(f"  Orphan pins:         {len(pin_result['orphan_pins'])}")
    # Deliberately not a problem label: five kilobytes of known-dead
    # paperwork in the same alarm box as a real fault is how the nine false
    # "unrecorded publishes" went unexamined for weeks.
    print(f"  Coconut remnants:    {len(pin_result.get('coconut_remnants') or [])}"
          f"  (informational)")
    print(f"  Kept originals:      {len(kept_originals) + len(orphan_originals)}"
          f"  ({human_size(sum(o['size_kb'] for o in kept_originals + orphan_originals))},"
          f" informational)")
    print(f"  Orphan originals:    {len(orphan_originals)}")
    print(f"  Missing pins:        {len(pin_result['missing_pins'])}")
    print(f"  Orphan seeds:        {len(seed_result['orphan_seeds'])}")
    print(f"  Missing seeds:       {len(seed_result['missing_seeds'])}")
    print(f"  Orphan drafts:       {len(draft_result['orphan_drafts'])}")
    print(f"  Stalled drafts:      {len(draft_result['stalled_drafts'])}")
    print(f"  Dead wiki drafts:    {len(draft_result['dead_wiki_drafts'])}")
    print(f"  Abandoned drafts:    {len(draft_result['abandoned_drafts'])}")
    print(f"  Unrecorded publishes: {len(unrecorded)}")

    cleanup_pending = sum(
        1 for r in pin_result["deleted"] + pin_result["retired"]
        if _alive_flags(r)
    )
    # abandoned_keep_files=true is "intentionally kept" — not pending cleanup.
    cleanup_pending += sum(
        1 for a in draft_result["abandoned_drafts"]
        if a["has_staging"] and not a.get("keep_files")
    )
    print(f"  Cleanup pending:     {cleanup_pending} "
          f"(deleted/retired releases + abandoned drafts with alive infra)")

    print("\n=== Audit Complete ===")


if __name__ == "__main__":
    main()
