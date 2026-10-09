"""Finalize jobs: a draft's encode and pin run in delivery-kid's own process,
not inside the request that asked for them.

Finalize used to be an SSE generator driven by the uploader's connection.
When the tab closed, the phone slept or the wifi blinked, the generator
stopped, and the draft was left at "finalizing" with nothing to pin it.
Two long encodes at once also fought over the box's 4GB and both stalled.

Now POST /finalize starts a job (or joins the one already running for that
draft), and the response streams the job's events to whoever is watching.
Closing the page stops the watching, not the job. Jobs take turns: one
finalize at a time, the rest queued in the order they were asked for. A
late watcher -- the page reloaded, or magenta looking in -- gets every event
from the start, and GET /draft-content/{id} has the same story in its
finalize_log.

Jobs live in memory, so a restart loses the ones in flight; startup marks
their drafts failed, with the upload still there to finalize again
(content.fail_stranded_finalizes).
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import AsyncIterator, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

KEEP_FINISHED = 3600  # seconds a finished job's events stay for late watchers


@dataclass
class Job:
    draft_id: str
    events: list = field(default_factory=list)  # SSE frames: {"event", "data"}
    done: bool = False
    finished_at: Optional[float] = None
    task: Optional[asyncio.Task] = None
    cond: asyncio.Condition = field(default_factory=asyncio.Condition)

    async def add(self, frame: dict) -> None:
        async with self.cond:
            self.events.append(frame)
            self.cond.notify_all()

    async def finish(self) -> None:
        async with self.cond:
            self.done = True
            self.finished_at = time.time()
            self.cond.notify_all()


_jobs: dict[str, Job] = {}
_waiting: list[str] = []      # draft ids queued behind the one running, oldest first
_running: Optional[str] = None
_turn = asyncio.Lock()        # one finalize at a time; asyncio.Lock wakes its waiters in order


def _forget_old(now: float) -> None:
    for draft_id, job in list(_jobs.items()):
        if job.done and job.finished_at and now - job.finished_at > KEEP_FINISHED:
            del _jobs[draft_id]


def current(draft_id: str) -> Optional[Job]:
    """The draft's job, running, queued, or finished within the hour."""
    _forget_old(time.time())
    return _jobs.get(draft_id)


def ahead_of(draft_id: str) -> int:
    """How many finalizes will run before this one (0 when it's running, or not queued)."""
    if draft_id not in _waiting:
        return 0
    return _waiting.index(draft_id) + (1 if _running else 0)


def start(draft_id: str,
          steps: Callable[[], AsyncIterator[dict]],
          on_queued: Callable[[int], Awaitable[dict]]) -> Job:
    """Run `steps()` (an async generator of SSE frames) as the draft's job, in its
    turn, and return the job. A draft already queued or running is joined, not
    started twice. `on_queued(ahead)` says it's waiting, as a frame."""
    job = current(draft_id)
    if job is not None and not job.done:
        return job
    job = Job(draft_id)
    _jobs[draft_id] = job
    _waiting.append(draft_id)
    job.task = asyncio.create_task(_run(job, steps, on_queued))
    return job


async def _run(job: Job, steps, on_queued) -> None:
    global _running
    try:
        ahead = ahead_of(job.draft_id)
        if ahead:
            await job.add(await on_queued(ahead))
        async with _turn:
            _waiting.remove(job.draft_id)
            _running = job.draft_id
            try:
                async for frame in steps():
                    await job.add(frame)
            finally:
                _running = None
    except asyncio.CancelledError:
        raise
    except Exception as e:  # the steps report their own failures; this is what escaped them
        logger.exception("[finalize:%s] job failed", job.draft_id[:8])
        await job.add({"event": "error", "data": json.dumps({"stage": "exception", "message": str(e)})})
    finally:
        if job.draft_id in _waiting:
            _waiting.remove(job.draft_id)
        await job.finish()


async def follow(job: Job) -> AsyncIterator[dict]:
    """Every frame of the job, from its first, as they come; ends when it does."""
    seen = 0
    while True:
        async with job.cond:
            await job.cond.wait_for(lambda: seen < len(job.events) or job.done)
            new, done = job.events[seen:], job.done
            seen += len(new)
        for frame in new:
            yield frame
        if done:
            return
