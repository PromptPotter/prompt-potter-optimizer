from __future__ import annotations

import asyncio
import contextvars
import functools
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

# The reads' own workers, as many as the threads a sync route had: a read never queues behind a
# command's offloaded work on the loop's default executor.
_READ_WORKERS = ThreadPoolExecutor(max_workers=40, thread_name_prefix="read")


async def off_loop[*A, T](work: Callable[[*A], T], *args: *A) -> T:
    """A read's disk work on the reads' workers, in the caller's context: every read door's one
    offload, `ReadParams.answered`'s, a framed route's and a read's own alike."""
    run = functools.partial(contextvars.copy_context().run, work, *args)
    return await asyncio.get_running_loop().run_in_executor(_READ_WORKERS, run)
