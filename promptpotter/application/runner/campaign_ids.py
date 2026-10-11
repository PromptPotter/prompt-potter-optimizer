"""Two ``new`` calls on one declaration share origin scores under different ids: ``measurements/`` pools them."""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from promptpotter.domain.opt_search_point import OptSearchPoint
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.search_point import JobSearchPoint, TaskDecomposition


def content_hash_of(jsp: JobSearchPoint, dataset: list[Sample]) -> str:
    return jsp.content_hash(dataset)[:12]


def cycle_config_identity(jsp: JobSearchPoint, dataset: list[Sample]) -> str:
    return f"cycle_{content_hash_of(jsp, dataset)}"


def mint_campaign_id(dataset_name: str) -> str:
    return f"{dataset_name or 'campaign'}__{secrets.token_hex(3)}"


def mint_checkin_cycle_id() -> str:
    """The PERMANENT root id: the ``cycle_chk_`` prefix carries no separator, so it reads as ``root``."""
    return f"cycle_chk_{secrets.token_hex(6)}"


def build_origin_cycle_id(
    opt_sp: OptSearchPoint,
    schema: PipelineSchema,
    dataset: list[Sample],
    *,
    framing: TaskDecomposition,
    demo: Sequence[Sample],
) -> str:
    """Config-AWARE, as the measurement key is: a connector-config edit yields a DISTINCT origin."""
    jsp = opt_sp.to_job_search_point(schema=schema, framing=framing, demo=demo)
    return cycle_config_identity(jsp, dataset)


__all__ = [
    "build_origin_cycle_id",
    "cycle_config_identity",
    "mint_campaign_id",
    "mint_checkin_cycle_id",
]
