"""Pickipedia bot client — snapshots draft diagnostics to a wiki sub-page.

When delivery-kid's filesystem is wiped (rebuild, container churn), the
``draft.json`` for each in-flight upload goes with it. The wiki side then
loses the upload/finalize/preview log trail that was the whole point of
PR #81's diagnostics panel. To survive that, we mirror the logs onto a
``ReleaseDraft:{id}/diagnostics`` wiki sub-page at each terminal state
transition. The wiki page outlives delivery-kid's storage, and the
diagnostics-panel JS falls back to it whenever the live ``/draft-content``
fetch returns 404.

The bot identity reused here is the same Magent@magent BotPassword that
the Blue Railroad import tool already uses (configured via
``PICKIPEDIA_BOT_USER`` and ``PICKIPEDIA_BOT_PASSWORD`` env vars). When
those are unset (typical in dev) every snapshot call no-ops cleanly.
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

logger = logging.getLogger(__name__)

# Lazy-initialised mwclient.Site singleton. Cached because each
# ``site.login()`` round-trips the wiki and we don't want to do that on
# every snapshot. ``_site_lock`` guards the init race when several
# webhooks fire concurrently.
_site = None
_site_lock = Lock()

# When credentials are missing we complain, but not on every snapshot — that
# would drown the log. Warning exactly once per process was the previous
# behaviour and it was too quiet: this container runs for weeks, so a single
# line at startup scrolls away and the module then fails in silence forever.
# Re-warn periodically instead, so "this has never worked" stays visible.
_last_missing_creds_warning = 0.0
MISSING_CREDS_WARN_INTERVAL = 3600

# Consecutive failures, so a persistent outage reads differently from a blip.
# Every snapshot failing since startup is a configuration problem, not a
# network one, and should say so.
_consecutive_failures = 0


def _parse_host(url: str) -> tuple[str, str]:
    """Split a wiki URL into (host, scheme) for mwclient.Site."""
    if url.startswith("https://"):
        return url[8:].rstrip("/"), "https"
    if url.startswith("http://"):
        return url[7:].rstrip("/"), "http"
    return url.rstrip("/"), "https"


def _get_site():
    """Return a logged-in mwclient.Site, or None if creds aren't configured."""
    global _site, _last_missing_creds_warning
    if _site is not None:
        return _site

    user = os.environ.get("PICKIPEDIA_BOT_USER", "Magent@magent")
    password = os.environ.get("PICKIPEDIA_BOT_PASSWORD")
    if not password:
        now = time.monotonic()
        if now - _last_missing_creds_warning > MISSING_CREDS_WARN_INTERVAL:
            _last_missing_creds_warning = now
            logger.error(
                "pickipedia_client: PICKIPEDIA_BOT_PASSWORD is empty, so no "
                "diagnostics snapshot has been written. The wiki copy is the "
                "only record of a draft's logs that survives a delivery-kid "
                "rebuild — without it, a draft whose staging is cleaned up "
                "leaves nothing behind. Set the env var from vault."
            )
        return None

    url = os.environ.get("PICKIPEDIA_URL", "https://pickipedia.xyz")
    host, scheme = _parse_host(url)

    with _site_lock:
        if _site is not None:
            return _site
        try:
            import mwclient
            # PickiPedia runs with $wgScriptPath empty, so its API lives at
            # /api.php. mwclient defaults to path='/w/', which makes every
            # request hit /w/api.php and 404 at login — silently disabling
            # every wiki snapshot this module exists to write.
            site = mwclient.Site(host, scheme=scheme, path="/")
            site.login(user, password)
            _site = site
            logger.info("pickipedia_client: authenticated to %s as %s", host, user)
            return site
        except Exception as e:
            logger.error("pickipedia_client: authentication to %s failed: %s", host, e)
            return None


def _build_snapshot_payload(state) -> dict:
    """Project a ContentDraftState into the JSON payload we write to the wiki.

    Kept deliberately flat so the wiki-side renderer can consume it with
    the same shape it gets from ``/draft-content``.
    """
    return {
        "draft_id": state.draft_id,
        "snapshot_at": datetime.now(timezone.utc).isoformat(),
        "status": state.status,
        "preview_status": state.preview_status,
        "upload_log": state.upload_log,
        "finalize_log": state.finalize_log,
        "preview_log": state.preview_log,
    }


def snapshot_diagnostics(draft_id: str, payload: dict) -> bool:
    """Write the diagnostics payload to ``ReleaseDraft:{id}/diagnostics``.

    Returns True if the write succeeded (or the page already had identical
    content), False if creds are missing or the wiki call failed. Never
    raises — terminal-state hooks call this fire-and-forget; we don't want
    a wiki blip to mask the underlying upload outcome.
    """
    site = _get_site()
    if site is None:
        return False

    title = f"ReleaseDraft:{draft_id}/diagnostics"
    content = json.dumps(payload, indent=2, default=str)
    summary = f"diagnostics snapshot — status={payload.get('status', 'unknown')}"

    global _consecutive_failures
    try:
        page = site.pages[title]
        existing = page.text() if page.exists else None
        if existing == content:
            _consecutive_failures = 0
            return True
        page.save(content, summary=summary)
        _consecutive_failures = 0
        logger.info("pickipedia_client: snapshotted %s (%d bytes)", title, len(content))
        return True
    except Exception as e:
        _consecutive_failures += 1
        logger.error("pickipedia_client: snapshot failed for %s: %s", title, e)
        # A run of failures is a configuration problem wearing the costume of
        # a transient one. Say which it looks like, because the difference
        # decides whether anyone goes and looks.
        if _consecutive_failures in (3, 10) or _consecutive_failures % 50 == 0:
            logger.error(
                "pickipedia_client: %d consecutive snapshot failures — this is "
                "not a blip. Diagnostics sub-pages are not being written at "
                "all, so any draft whose staging is cleaned up will leave no "
                "log trail behind. Check the bot's credentials and edit rights.",
                _consecutive_failures,
            )
        return False


async def snapshot_diagnostics_for_state_async(state) -> bool:
    """Async fire-and-forget snapshot from a ContentDraftState.

    Runs the sync mwclient call in a thread pool so we don't block the
    FastAPI event loop. Used by terminal-state hooks in the routes.
    """
    payload = _build_snapshot_payload(state)
    return await asyncio.to_thread(snapshot_diagnostics, state.draft_id, payload)


def snapshot_diagnostics_for_dict_async(draft_id: str, draft_data: dict):
    """Async fire-and-forget snapshot from a raw draft.json dict.

    Used by the Coconut webhook path, which mutates ``draft.json`` on disk
    rather than holding a ContentDraftState object. Returns the asyncio
    Task so the caller can ``create_task(...)`` it without awaiting.
    """
    payload = {
        "draft_id": draft_id,
        "snapshot_at": datetime.now(timezone.utc).isoformat(),
        "status": draft_data.get("status"),
        "preview_status": draft_data.get("preview_status"),
        "upload_log": draft_data.get("upload_log") or [],
        "finalize_log": draft_data.get("finalize_log") or [],
        "preview_log": draft_data.get("preview_log") or [],
    }
    return asyncio.to_thread(snapshot_diagnostics, draft_id, payload)


def write_finalized_to_releasedraft(
    draft_id: str,
    final_cid: str,
    finalized_at: str,
) -> bool:
    """Edit the ``ReleaseDraft:{draft_id}`` wiki page YAML to mark it finalized.

    Pre-V2-Coconut-migration, the browser-side JS handled this edit when
    the finalize SSE delivered a ``complete`` event. For the slow Coconut
    path that event never fires (the SSE closes after submission and the
    user's browser doesn't see the eventual completion), so we write from
    the webhook side instead.

    The edit:
      - Loads the current YAML
      - Sets ``status: finalized``, ``final_cid: <cid>``, ``finalized_at: <iso>``
      - Saves with summary ``Finalized: pinned to IPFS as <cid>``

    The summary line is what the Blue Railroad Imports bot's
    ``find_cid_from_history`` greps for to trigger Release page creation
    on its next cron run.

    Returns True if the edit succeeded, False if creds are missing or the
    write failed. Never raises.
    """
    site = _get_site()
    if site is None:
        return False

    try:
        import yaml as _yaml
    except ImportError:
        logger.error("pickipedia_client: PyYAML not installed; cannot edit ReleaseDraft YAML")
        return False

    title = f"ReleaseDraft:{draft_id}"
    try:
        page = site.pages[title]
        if not page.exists:
            logger.warning("pickipedia_client: %s does not exist; cannot mark finalized", title)
            return False

        existing_text = page.text()
        try:
            data = _yaml.safe_load(existing_text) or {}
        except _yaml.YAMLError as e:
            logger.error("pickipedia_client: failed to parse %s YAML: %s", title, e)
            return False

        if not isinstance(data, dict):
            logger.error("pickipedia_client: %s YAML is not a mapping; refusing to edit", title)
            return False

        data["status"] = "finalized"
        data["final_cid"] = final_cid
        data["finalized_at"] = finalized_at

        new_text = _yaml.safe_dump(data, default_flow_style=False, sort_keys=False)
        summary = f"Finalized: pinned to IPFS as {final_cid}"

        if new_text.strip() == existing_text.strip():
            return True

        page.save(new_text, summary=summary)
        logger.info("pickipedia_client: marked %s finalized (final_cid=%s)", title, final_cid)
        return True
    except Exception as e:
        logger.error("pickipedia_client: failed to mark %s finalized: %s", title, e)
        return False


async def write_finalized_to_releasedraft_async(
    draft_id: str,
    final_cid: str,
    finalized_at: str,
):
    """Async wrapper for ``write_finalized_to_releasedraft``.

    Runs the sync mwclient calls in a thread so the FastAPI event loop
    isn't blocked while the wiki round-trip happens.
    """
    return await asyncio.to_thread(
        write_finalized_to_releasedraft, draft_id, final_cid, finalized_at,
    )
