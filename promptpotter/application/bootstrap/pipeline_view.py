"""Bootstrap-time backend pipeline discovery.

Fetches ``GET /pipeline`` from the live backend and parses the response
into a ``PipelineSchema`` view dict the rest of the system consumes.
The pure dict→domain parser lives in ``domain/pipeline_parsing.py``;
this module owns the I/O and the "live vs. default" classification.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from promptpotter.domain.pipeline_parsing import parse_pipeline_response
from promptpotter.domain.pipeline_schema import PipelineSchema

if TYPE_CHECKING:
    from promptpotter.infrastructure.backend import BackendClient

logger = logging.getLogger(__name__)

__all__ = ["compute_pipeline_view"]


async def compute_pipeline_view(
    backend_client: BackendClient,
) -> dict[str, Any]:
    """Build a view of the backend pipeline.

    Returns a dict with keys:
      backend_pipeline  — PipelineSchema.model_dump()
      computed_nodes    — pipeline nodes as dicts (direct copy for now)
      fetched_at        — ISO timestamp
      source            — "live" | "default"
    """
    try:
        raw: dict[str, Any] | None = await backend_client.fetch_pipeline()
        source = "live"
    except Exception:
        logger.warning("Backend unreachable at %s; returning empty schema", backend_client.base_url)
        raw = None
        source = "default"

    schema = parse_pipeline_response(raw) if raw is not None else PipelineSchema()
    computed_nodes = [s.model_dump() for s in schema.nodes]

    return {
        "backend_pipeline": schema.model_dump(),
        "computed_nodes": computed_nodes,
        "fetched_at": datetime.now(UTC).isoformat(),
        "source": source,
    }
