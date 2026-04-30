"""FastAPI application for delivery-kid pinning service."""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .routes import health, albums, drafts, content, enrich, torrent, coconut, staging
from .routes.content import log_pre_handler_failure
from .services.seeder import init_seeder, stop_seeder

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager for startup/shutdown tasks.

    On startup:
    - Run initial cleanup of orphaned drafts
    - Start periodic orphan cleanup background task

    On shutdown:
    - Cancel background cleanup task
    """
    settings = get_settings()
    staging_dir = Path(settings.staging_dir)

    # Ensure staging directory exists
    staging_dir.mkdir(parents=True, exist_ok=True)
    (staging_dir / "drafts").mkdir(exist_ok=True)

    # Start BitTorrent seeder
    init_seeder(settings.seeding_dir)

    logger.info("Delivery Kid pinning service started")
    yield

    # Shutdown: stop seeder first
    stop_seeder()
    logger.info("Delivery Kid pinning service stopped")


app = FastAPI(
    title="Delivery Kid Pinning Service",
    description="IPFS pinning and album upload service for CryptoGrass",
    version="1.0.0",
    lifespan=lifespan
)

# Configure CORS
settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_origin_regex=settings.cors_origin_regex,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS", "DELETE"],
    allow_headers=["*"],
    expose_headers=["*"]
)

# Persist pre-handler upload failures into draft.upload_log.
#
# Body-parse errors, request-too-large, and any HTTPException raised before
# the /draft-content POST handler runs are otherwise invisible: the handler
# never gets the chance to call its own fail() helper. This middleware
# observes the response status, and when an /init-created draft exists it
# leaves an entry on the draft's upload_log so the ReleaseDraft page can
# show what blew up. Dedup against the handler's own fail() is handled by
# log_pre_handler_failure itself.
@app.middleware("http")
async def log_draft_request_failures(request: Request, call_next):
    response = await call_next(request)
    if (
        request.url.path == "/draft-content"
        and request.method == "POST"
        and response.status_code >= 400
    ):
        draft_id = request.headers.get("x-draft-id") or request.headers.get("X-Draft-Id")
        if draft_id:
            try:
                log_pre_handler_failure(
                    draft_id,
                    response.status_code,
                    dict(request.headers),
                    get_settings(),
                )
            except Exception:
                logger.exception(
                    "[middleware] Failed to log pre-handler upload failure"
                )
    return response


# Include routers
app.include_router(health.router)
app.include_router(albums.router)
app.include_router(drafts.router)
app.include_router(content.router)
app.include_router(enrich.router)
app.include_router(torrent.router)
app.include_router(coconut.router)
app.include_router(staging.router)


@app.get("/")
async def root():
    """Root endpoint."""
    return {"service": "delivery-kid-pinning", "status": "running"}
