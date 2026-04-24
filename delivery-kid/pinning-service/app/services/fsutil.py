"""Filesystem helpers. Mostly defense against CIFS/Samba flakiness on the
Hetzner Storage Box — operations that should be trivial sometimes partial-fail
or need a retry to actually commit."""

import logging
import shutil
import time
from pathlib import Path


logger = logging.getLogger(__name__)


def safe_rmtree(path: Path, retries: int = 3, delay: float = 0.5) -> None:
    """rmtree that retries on partial failure.

    Observed failure mode on the storage box: ``shutil.rmtree`` returns
    without exception but the directory still exists (emptied or partial).
    After this call, ``path`` is guaranteed gone or we raise.
    """
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            if path.exists():
                shutil.rmtree(path)
        except Exception as e:
            last_error = e
            logger.warning("rmtree %s failed (attempt %d): %s", path, attempt + 1, e)
        if not path.exists():
            return
        time.sleep(delay)
    raise RuntimeError(f"rmtree did not complete for {path}: {last_error}")
