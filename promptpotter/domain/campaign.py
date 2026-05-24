"""``Campaign`` — the first-class optimization-effort entity.

A campaign is one declared optimization effort: a dataset + pipeline
origin + context text + the optimizer meta-prompts it runs under. It
holds one session root (``cycle_<target_hash>``) plus any fork/diag/sweep
descendants, all flat under ``cycles/``. The campaign is the single
owner of the frozen ``CampaignConfig`` snapshot. ``campaign.json`` at
``campaigns/{campaign_id}/`` is this model's dump.

``campaign_id`` is ``{dataset}__{rand6_hex}`` — minted once per ``new``
invocation by :func:`promptpotter.application.runner.identity.mint_campaign_id`.
Each ``new`` produces a distinct campaign, regardless of whether the
declaration matches an existing campaign on disk. Two ``new`` calls on
an unchanged declaration share their root cycle id
(``cycle_<target_hash>`` is content-addressed) and their origin score
(the dataset-scoped archive cache-hits every sample), but differ in
``campaign_id`` and diverge from round 1 onward.

* ``root_content_hash`` — the target content hash; the campaign's root
  cycle dir is ``cycle_<root_content_hash>``. Resume recomputes it to
  detect target drift.
* ``optimizer_prompt_hash`` — the optimizer meta-prompt set's hash;
  resume recomputes it to detect optimizer-prompt drift.

Legacy on-disk shape (multi-session forest with ``_s{N}`` suffixes
under one ``campaign_id``) — see
:mod:`promptpotter.infrastructure.store.paths`.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Campaign(BaseModel):
    """Frozen manifest for one optimization campaign — ``campaign.json``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    campaign_id: str
    dataset_name: str
    label: str = ""
    created_at: str
    status: str = "active"
    finished_at: str = ""
    root_cycle_id: str
    root_content_hash: str = ""
    optimizer_prompt_hash: str = ""
    backend_id: str = ""
    config: dict[str, Any] = Field(default_factory=dict)


__all__ = ["Campaign"]
