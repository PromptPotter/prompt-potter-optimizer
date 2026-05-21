"""Campaign + cycle identity helpers — pure, no I/O.

**Two hashes, two jobs.**

* ``content_hash_of`` — the *target* content hash (rendered target prompt +
  dataset + target ``pipeline_params``). It is the bare 12-hex value stored
  on ``campaign.json::root_content_hash``, the root cycle's directory name
  (``cycle_<hash>`` via ``cycle_config_identity``), and the archive run key.
* ``declaration_hash`` — the **campaign's identity**: it folds the target
  hash together with the *optimizer*-prompt hash (the four optimizer
  meta-prompts; see
  :func:`promptpotter.application.optimization.dispatch.llm_call.combined_optimizer_prompt_hash`).
  ``campaign_id_for`` builds ``{dataset}__{declaration_hash}`` from it.

A campaign is therefore a *complete* optimization declaration: re-running
``new`` on an unchanged dataset + target + optimizer prompts resolves to the
same campaign (a new session); editing a target field OR an optimizer
meta-prompt shifts the declaration hash and mints a distinct campaign.

Loop-control / strategy knobs on ``CampaignConfig`` are deliberately
excluded from both hashes so tweaking optimizer strategy or resuming with
different budgets does not register as drift.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from promptpotter.domain.opt_search_point import OptSearchPoint
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.search_point import JobSearchPoint


def content_hash_of(jsp: JobSearchPoint, dataset: list) -> str:
    """Bare content hash (12 hex) of an origin ``JobSearchPoint``.

    Covers the rendered prompt, dataset, and full ``pipeline_params``
    (active steps + per-node target-layer config). Stored on the campaign
    manifest; resume recomputes and compares to detect config drift.
    """
    return jsp.content_hash(dataset)[:12]


def cycle_config_identity(jsp: JobSearchPoint, dataset: list) -> str:
    """Root cycle id for an origin ``JobSearchPoint`` — ``cycle_<content_hash>``."""
    return f"cycle_{content_hash_of(jsp, dataset)}"


def declaration_hash(target_hash: str, optimizer_hash: str) -> str:
    """Campaign-identity hash — folds the target hash + the optimizer hash.

    A campaign's identity is the *complete* declaration: the target
    (``content_hash_of``) AND the optimizer meta-prompt set
    (``combined_optimizer_prompt_hash``). Either side changing yields a
    distinct campaign. Pure — a deterministic 12-hex digest of the two
    inputs.
    """
    blob = f"{target_hash}:{optimizer_hash}"
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def campaign_id_for(dataset_name: str, decl_hash: str) -> str:
    """Stable campaign id — ``{dataset}__{decl_hash}``.

    The campaign's identity is the declaration hash (see
    :func:`declaration_hash`) — the target content hash folded with the
    optimizer-prompt hash. Re-running ``new`` on an unchanged declaration
    lands in the *same* campaign (find-or-create adds a session); changing
    a target field or an optimizer meta-prompt produces a different hash
    and a distinct campaign. ``decl_hash`` is the bare 12-hex value.
    """
    return f"{dataset_name or 'campaign'}__{decl_hash}"


def build_origin_cycle_id(
    osp: OptSearchPoint,
    schema: PipelineSchema | None,
    dataset: list,
) -> str:
    """Cycle ID for an origin ``OptSearchPoint`` — the OSP → JSP projection ceremony."""
    base_pp = schema.to_pipeline_params() if schema else {}
    jsp = osp.to_job_search_point(base_pipeline_params=base_pp, schema=schema)
    return cycle_config_identity(jsp, dataset)


__all__ = [
    "build_origin_cycle_id",
    "campaign_id_for",
    "content_hash_of",
    "cycle_config_identity",
    "declaration_hash",
]
