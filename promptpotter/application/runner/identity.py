"""Campaign + cycle identity helpers — pure, no I/O.

The content hash keeps two jobs: archive cache-reuse keying and resume
drift detection. ``content_hash_of`` is the bare 12-hex hash stored on
``campaign.json::root_content_hash``; ``cycle_config_identity`` is the
``cycle_<hash>`` form used as the root cycle's directory name.

The same hash is the **campaign's identity** — ``campaign_id_for`` builds
``{dataset}__{hash}`` from it, so re-running ``new`` on an unchanged origin
declaration (dataset + pipeline + context) resolves to the same campaign
and adds a session to it rather than minting a fresh campaign.

Loop-control / strategy knobs on ``CampaignConfig`` are deliberately
excluded from the hash so tweaking optimizer strategy or resuming with
different budgets does not register as drift.
"""

from __future__ import annotations

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


def campaign_id_for(dataset_name: str, content_hash: str) -> str:
    """Stable campaign id — ``{dataset}__{content_hash}``.

    The campaign's identity is the origin declaration's content hash —
    the same 12-hex value ``content_hash_of`` produces and
    ``cycle_config_identity`` stamps as the root cycle id. Deriving the
    campaign id from it means a re-run of ``new`` on an unchanged
    declaration lands in the *same* campaign (find-or-create adds a
    session); a changed declaration produces a different hash and a
    distinct campaign. ``content_hash`` is the bare hash, not the
    ``cycle_<hash>`` form.
    """
    return f"{dataset_name or 'campaign'}__{content_hash}"


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
]
