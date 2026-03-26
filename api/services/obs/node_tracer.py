"""Lightweight node-level timing + observability for optimizer pipeline nodes.

Async context manager wrapping ``ObsLogger.log_node_start/end``.

Usage::

    async with observed_node("l1_generate_r3", "llm/meta", obs=obs, trace_id=tid) as trace:
        result = await some_service_function(...)
        trace.output = {"n_items": len(result)}
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from api.services.obs.observability_logger import ObsLogger

logger = logging.getLogger(__name__)


@dataclass
class NodeTrace:
    """Mutable bag for node output and metrics, yielded by ``observed_node``."""

    output: dict[str, Any] = field(default_factory=dict)
    duration_ms: float = 0.0
    error: str | None = None


@asynccontextmanager
async def observed_node(
    node_id: str,
    node_type: str,
    obs: "ObsLogger | None" = None,
    trace_id: str | None = None,
    obs_type: str = "generation",
):
    """Async context manager for node-level timing + observability.

    Captures wall-clock duration and optionally writes Langfuse-compatible
    observations via ``obs.log_node_start/end``.  Non-fatal: observability
    failures are logged as warnings and never crash the caller.

    Yields:
        NodeTrace with ``.output`` (set by caller), ``.duration_ms``, ``.error``.
    """
    trace = NodeTrace()
    obs_id: str | None = None

    if obs and trace_id:
        try:
            obs_id = obs.log_node_start(
                trace_id=trace_id,
                node_id=node_id,
                node_type=node_type,
                obs_type=obs_type,
                input_data={},
            )
        except Exception:
            logger.warning("observed_node start failed for %s", node_id, exc_info=True)

    t0 = time.perf_counter()
    try:
        yield trace
    except Exception as exc:
        trace.error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        trace.duration_ms = (time.perf_counter() - t0) * 1000
        if obs and trace_id and obs_id:
            try:
                obs.log_node_end(
                    obs_id=obs_id,
                    trace_id=trace_id,
                    node_id=node_id,
                    output_data=trace.output,
                    metrics={"duration_ms": trace.duration_ms},
                    error=trace.error,
                )
            except Exception:
                logger.warning("observed_node end failed for %s", node_id, exc_info=True)
