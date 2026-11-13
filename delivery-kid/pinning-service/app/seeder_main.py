"""The BitTorrent seeder's own process: ``python -m app.seeder_main``.

delivery-kid's compose file runs this in the ``seeder`` container, from the
same image as the web service but as a separate process with its own memory
limit. It seeds every torrent under the seeding folder, takes new ones the web
service hands over through ``.incoming``, and writes ``.status.json`` for
``GET /torrent/status``. See ``services/seeding_store`` for the folder.

Until block 26,371,678 (8 Nov 2026) the seeder ran inside the web service's uvicorn process
(``init_seeder`` in main.py's startup), so a libtorrent problem could take
uploads down, and every web restart stopped all seeding.
"""

import logging
import signal
import time

from .config import get_settings
from .services.seeder import Seeder
from .services.seeding_store import STATUS_INTERVAL

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("seeder")

# How often to look in .incoming. The Storage Box is a CIFS mount, where
# inotify doesn't see changes, so the seeder looks rather than waits.
POLL_SECONDS = 5


def main() -> None:
    settings = get_settings()
    seeder = Seeder(settings.seeding_dir)
    stopping = False

    def stop(signum, _frame):
        nonlocal stopping
        logger.info("Got signal %d, stopping", signum)
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    seeder.start()
    seeder.sweep_stale_tmp()
    last_status = 0.0
    while not stopping:
        taken = seeder.process_incoming()
        if taken:
            logger.info("Took %d new torrent(s) from .incoming", taken)
        seeder.log_alerts()
        if taken or time.monotonic() - last_status >= STATUS_INTERVAL:
            try:
                seeder.write_status()
            except OSError as e:
                logger.error("Couldn't write status: %s", e)
            last_status = time.monotonic()
        time.sleep(POLL_SECONDS)

    seeder.stop()


if __name__ == "__main__":
    main()
