"""Campaign + cycle identity — pure. Dataset-scoped ``measurements/`` pools evidence across campaigns on one
declaration, so two ``new`` calls on an unchanged declaration share origin scores under different ids."""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from promptpotter.domain.opt_search_point import OptSearchPoint
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.search_point import JobSearchPoint


def content_hash_of(jsp: JobSearchPoint, dataset: list[Sample]) -> str:
    """12-hex content hash of an origin ``JobSearchPoint`` (rendered prompt + dataset + ``pipeline_params``);
    stored on the campaign manifest, recomputed on resume to detect drift."""
    return jsp.content_hash(dataset)[:12]


def cycle_config_identity(jsp: JobSearchPoint, dataset: list[Sample]) -> str:
    """Root cycle id for an origin ``JobSearchPoint`` — ``cycle_<content_hash>``."""
    return f"cycle_{content_hash_of(jsp, dataset)}"


def mint_campaign_id(dataset_name: str) -> str:
    """Fresh ``{dataset}__{rand6_hex}``; each ``new`` invocation mints a distinct campaign.
    Same-declaration campaigns share their root cycle id but differ in ``campaign_id``."""
    return f"{dataset_name or 'campaign'}__{secrets.token_hex(3)}"


def mint_checkin_cycle_id() -> str:
    """Provisional root cycle id for a check-in — the origin isn't authored yet, so there is no content hash to address
    it by. This stays the PERMANENT root id; the ``cycle_chk_`` prefix carries no separator, so it reads as ``root``."""
    return f"cycle_chk_{secrets.token_hex(6)}"


def build_origin_cycle_id(
    opt_sp: OptSearchPoint,
    schema: PipelineSchema,
    dataset: list[Sample],
    base_pipeline_params: dict[str, Any] | None = None,
) -> str:
    """Origin cycle id — config-AWARE, so it agrees with the measurement key and a connector-config edit yields a
    DISTINCT origin. The schema is REQUIRED: two callers holding it differently stamped two ids for one origin."""
    base_pp = (
        base_pipeline_params if base_pipeline_params is not None else schema.to_pipeline_params()
    )
    jsp = opt_sp.to_job_search_point(base_pipeline_params=base_pp, schema=schema)
    return cycle_config_identity(jsp, dataset)


__all__ = [
    "build_origin_cycle_id",
    "cycle_config_identity",
    "mint_campaign_id",
    "mint_checkin_cycle_id",
]
