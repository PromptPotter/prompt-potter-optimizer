"""Lightweight step-level timing + observability for optimizer pipeline steps.

Replaces the NodeBase I/O ceremony with a simple async context manager that
calls the same ``ObsLogger.log_node_step_start/end`` methods.

Usage::

    async with observed_step("l1_generate_r3", "llm/meta", obs=obs, trace_id=tid) as step:
        result = await some_service_function(...)
        step.output = {"n_items": len(result)}
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
class StepTrace:
    """Mutable bag for step output and metrics, yielded by ``observed_step``."""

    output: dict[str, Any] = field(default_factory=dict)
    duration_ms: float = 0.0
    error: str | None = None


@asynccontextmanager
async def observed_step(
    step_id: str,
    step_type: str,
    obs: "ObsLogger | None" = None,
    trace_id: str | None = None,
    obs_type: str = "generation",
):
    """Async context manager for step-level timing + observability.

    Captures wall-clock duration and optionally writes Langfuse-compatible
    observations via ``obs.log_node_step_start/end``.  Non-fatal: observability
    failures are logged as warnings and never crash the caller.

    Yields:
        StepTrace with ``.output`` (set by caller), ``.duration_ms``, ``.error``.
    """
    step = StepTrace()
    obs_id: str | None = None

    if obs and trace_id:
        try:
            obs_id = obs.log_node_step_start(
                trace_id=trace_id,
                node_id=step_id,
                node_type=step_type,
                obs_type=obs_type,
                input_data={},
            )
        except Exception:
            logger.warning("observed_step start failed for %s", step_id, exc_info=True)

    t0 = time.perf_counter()
    try:
        yield step
    except Exception as exc:
        step.error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        step.duration_ms = (time.perf_counter() - t0) * 1000
        if obs and trace_id and obs_id:
            try:
                obs.log_node_step_end(
                    obs_id=obs_id,
                    trace_id=trace_id,
                    node_id=step_id,
                    output_data=step.output,
                    metrics={"duration_ms": step.duration_ms},
                    error=step.error,
                )
            except Exception:
                logger.warning("observed_step end failed for %s", step_id, exc_info=True)
