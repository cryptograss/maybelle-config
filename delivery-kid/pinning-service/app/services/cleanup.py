"""Draft cleanup service - removes orphaned draft directories and monitors staging space."""

import asyncio
import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# Orphaned drafts (no valid draft.json) older than this are removed
ORPHAN_THRESHOLD_HOURS = 7 * 24  # 7 days


def cleanup_orphaned_drafts(staging_dir: Path) -> tuple[int, int]:
    """
    Remove orphaned draft directories (those without a valid draft.json).

    Drafts with a valid draft.json are never removed here — they persist
    until explicitly finalized or deleted by the user.

    Returns tuple of (drafts_checked, drafts_removed).
    """
    drafts_dir = staging_dir / "drafts"
    if not drafts_dir.exists():
        return (0, 0)

    now = datetime.now(timezone.utc)
    checked = 0
    removed = 0

    for draft_dir in drafts_dir.iterdir():
        if not draft_dir.is_dir():
            continue

        checked += 1

        # Only remove directories without a valid draft.json (orphans)
        draft_json = draft_dir / "draft.json"
        has_valid_state = False
        if draft_json.exists():
            try:
                with open(draft_json) as f:
                    json.load(f)
                has_valid_state = True
            except (json.JSONDecodeError, ValueError):
                pass

        if has_valid_state:
            # Valid draft — leave it alone
            continue

        # Orphaned directory — remove if old enough
        try:
            mtime = datetime.fromtimestamp(draft_dir.stat().st_mtime, tz=timezone.utc)
            age_hours = (now - mtime).total_seconds() / 3600
            if age_hours > ORPHAN_THRESHOLD_HOURS:
                logger.info(f"Removing orphaned draft: {draft_dir.name} (age: {age_hours:.1f}h)")
                shutil.rmtree(draft_dir)
                removed += 1
        except OSError as e:
            logger.error(f"Failed to remove orphaned draft {draft_dir.name}: {e}")

    return (checked, removed)


def get_staging_size_gb(staging_dir: Path) -> float:
    """
    Calculate total size of the staging directory in gigabytes.
    """
    total_bytes = 0
    try:
        for path in staging_dir.rglob("*"):
            if path.is_file():
                total_bytes += path.stat().st_size
    except OSError:
        pass
    return total_bytes / (1024 ** 3)


async def periodic_cleanup(staging_dir: Path, interval_seconds: int = 3600):
    """
    Async task that periodically cleans up orphaned drafts.

    Args:
        staging_dir: Path to the staging directory
        interval_seconds: How often to run cleanup (default: 1 hour)
    """
    logger.info(f"Starting periodic orphan cleanup task (interval: {interval_seconds}s)")

    while True:
        try:
            checked, removed = cleanup_orphaned_drafts(staging_dir)
            size_gb = get_staging_size_gb(staging_dir)

            if removed > 0 or checked > 0:
                logger.info(
                    f"Orphan cleanup complete: checked={checked}, removed={removed}, "
                    f"staging_size={size_gb:.2f}GB"
                )
            else:
                logger.debug(f"Orphan cleanup: no drafts to check, staging_size={size_gb:.2f}GB")

        except Exception as e:
            logger.error(f"Error during cleanup: {e}")

        await asyncio.sleep(interval_seconds)


def startup_cleanup(staging_dir: Path) -> None:
    """
    Run cleanup synchronously at startup.

    Clears any orphaned drafts that accumulated while service was down.
    """
    logger.info("Running startup cleanup...")
    try:
        checked, removed = cleanup_orphaned_drafts(staging_dir)
        size_gb = get_staging_size_gb(staging_dir)
        logger.info(
            f"Startup cleanup complete: checked={checked}, removed={removed}, "
            f"staging_size={size_gb:.2f}GB"
        )
    except Exception as e:
        logger.error(f"Error during startup cleanup: {e}")
