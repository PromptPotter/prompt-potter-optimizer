"""Campaign + cycle identity helpers — pure, no I/O.

- ``content_hash_of`` — target content hash (rendered prompt + dataset +
  target ``pipeline_params``); the 12-hex value on ``campaign.json::root_content_hash``,
  the root cycle dir (``cycle_<hash>``), and the archive run key.
- ``mint_campaign_id`` — fresh random 6-hex suffix glued to the dataset name;
  each ``new`` run mints a distinct campaign. Declaration is recorded as
  *properties* (``root_content_hash`` + ``optimizer_prompt_hash``) for drift
  detection on resume, not to derive the id.

Dataset-scoped ``archive/measurements/`` pools evidence across campaigns on
the same declaration, so two fresh ``new`` calls on an unchanged declaration
share origin scores (every sample cache-hits) but have different ``campaign_id``s."""

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
    """Provisional root cycle id for a check-in campaign — ``cycle_chk_{rand12_hex}``.

    The origin isn't authored at first action, so there's no content hash to
    address the root cycle by (the normal ``cycle_<hash>`` scheme). This stays the
    permanent root id; drift detection reads ``campaign.json::root_content_hash``
    (set at Start), not the parsed cycle id. The ``cycle_chk_`` prefix carries no
    sibling separator, so ``sibling_kind`` reads it as ``root`` and ``root_cycle_id``
    returns it whole."""
    return f"cycle_chk_{secrets.token_hex(6)}"


def build_origin_cycle_id(
    osp: OptSearchPoint,
    schema: PipelineSchema | None,
    dataset: list[Sample],
    base_pipeline_params: dict[str, Any] | None = None,
) -> str:
    """Cycle ID for an origin ``OptSearchPoint`` — the OSP → JSP projection ceremony.

    Config-AWARE: callers pass the overlay-merged ``session.pipeline_params`` (which
    carries the connector ``model``/config) as ``base_pipeline_params``, so the cycle id
    reflects the connector config and AGREES with the measurement key (``content_hash``
    over the same merged params). A connector-config edit (e.g. model 120B→20B) therefore
    yields a DISTINCT origin. Falls back to the sparse ``to_pipeline_params()`` only when
    no merged params are in scope."""
    base_pp = (
        base_pipeline_params
        if base_pipeline_params is not None
        else (schema.to_pipeline_params() if schema else {})
    )
    jsp = osp.to_job_search_point(base_pipeline_params=base_pp, schema=schema)
    return cycle_config_identity(jsp, dataset)


__all__ = [
    "build_origin_cycle_id",
    "content_hash_of",
    "cycle_config_identity",
    "mint_campaign_id",
    "mint_checkin_cycle_id",
]
