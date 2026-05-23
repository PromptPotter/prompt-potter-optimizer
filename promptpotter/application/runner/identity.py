"""Campaign + cycle identity helpers — pure, no I/O.

**One hash, one mint.**

* ``content_hash_of`` — the *target* content hash (rendered target prompt +
  dataset + target ``pipeline_params``). It is the bare 12-hex value stored
  on ``campaign.json::root_content_hash``, the root cycle's directory name
  (``cycle_<hash>`` via ``cycle_config_identity``), and the archive run key.
* ``mint_campaign_id`` — the **campaign's identity**: a fresh per-call random
  6-hex suffix glued to the dataset name. Each ``new`` run mints a distinct
  campaign; the declaration is recorded as *properties* on ``campaign.json``
  (``root_content_hash`` + ``optimizer_prompt_hash``) and used by resume to
  warn on drift, not to derive the id.

The dataset-scoped ``archive/measurements/`` still pools evidence across
campaigns on the same declaration, so two fresh ``new`` calls on an unchanged
declaration produce campaigns with byte-identical origin scores (every
sample cache-hits) — just with different ``campaign_id``s.
"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from promptpotter.domain.opt_search_point import OptSearchPoint
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.search_point import JobSearchPoint


def content_hash_of(jsp: JobSearchPoint, dataset: list[Sample]) -> str:
    """Bare content hash (12 hex) of an origin ``JobSearchPoint``.

    Covers the rendered prompt, dataset, and full ``pipeline_params``
    (active steps + per-node target-layer config). Stored on the campaign
    manifest; resume recomputes and compares to detect config drift.
    """
    return jsp.content_hash(dataset)[:12]


def cycle_config_identity(jsp: JobSearchPoint, dataset: list[Sample]) -> str:
    """Root cycle id for an origin ``JobSearchPoint`` — ``cycle_<content_hash>``."""
    return f"cycle_{content_hash_of(jsp, dataset)}"


def mint_campaign_id(dataset_name: str) -> str:
    """Fresh campaign id — ``{dataset}__{rand6_hex}``.

    Each ``new`` invocation mints a distinct campaign, regardless of whether
    a campaign with the same declaration already exists on disk. The random
    6-hex suffix matches the shape of the previous content-addressed scheme
    (``justlogic__5d4c26``) but is no longer derivable from the declaration.

    The declaration (target hash + optimizer-prompt hash) is recorded as
    *properties* on ``campaign.json`` for drift detection on resume, not as
    the id. Two campaigns on the same declaration share their root cycle id
    (``cycle_<content_hash>``) but differ in ``campaign_id``.
    """
    return f"{dataset_name or 'campaign'}__{secrets.token_hex(3)}"


def build_origin_cycle_id(
    osp: OptSearchPoint,
    schema: PipelineSchema | None,
    dataset: list[Sample],
) -> str:
    """Cycle ID for an origin ``OptSearchPoint`` — the OSP → JSP projection ceremony."""
    base_pp = schema.to_pipeline_params() if schema else {}
    jsp = osp.to_job_search_point(base_pipeline_params=base_pp, schema=schema)
    return cycle_config_identity(jsp, dataset)


__all__ = [
    "build_origin_cycle_id",
    "content_hash_of",
    "cycle_config_identity",
    "mint_campaign_id",
]
