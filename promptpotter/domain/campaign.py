"""``Campaign`` — the first-class optimization-effort entity.

A campaign is one declared optimization effort: a dataset + pipeline
origin + context text. It is a **forest** — it holds N *sessions* (every
``new`` on the same declaration adds one) plus their fork descendants,
all flat under ``cycles/``. The campaign is the single owner of the
frozen ``CampaignConfig`` snapshot. ``campaign.json`` at
``campaigns/{campaign_id}/`` is this model's dump.

``campaign_id`` is ``{dataset}__{root_content_hash}`` — derived from the
origin declaration's content hash (see
:func:`promptpotter.application.runner.identity.campaign_id_for`). It is
therefore *stable*: re-running ``new`` on an unchanged declaration
resolves to the same campaign and adds a session to it.

``root_content_hash`` IS the campaign identity — the bare content hash of
the origin ``JobSearchPoint``. ``root_cycle_id`` is the first session's
root cycle, a convenience pointer; the forest may hold more sessions
(``CampaignStore.list_sessions``). Resume drift detection recomputes the
current config's hash and compares it to ``root_content_hash``.
"""

from __future__ import annotations

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
    backend_id: str = ""
    config: dict = Field(default_factory=dict)


__all__ = ["Campaign"]
