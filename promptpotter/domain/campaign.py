"""``Campaign`` — the first-class optimization-effort entity.

A campaign is one declared optimization effort: a dataset + pipeline
origin + context text + the optimizer meta-prompts it runs under. It is a
**forest** — it holds N *sessions* (every ``new`` on the same declaration
adds one) plus their fork descendants, all flat under ``cycles/``. The
campaign is the single owner of the frozen ``CampaignConfig`` snapshot.
``campaign.json`` at ``campaigns/{campaign_id}/`` is this model's dump.

``campaign_id`` is ``{dataset}__{declaration_hash}`` — the declaration
hash folds the **target** content hash (``root_content_hash``) together
with the **optimizer** meta-prompt hash (``optimizer_prompt_hash``); see
:func:`promptpotter.application.runner.identity.declaration_hash`. It is
therefore *stable*: re-running ``new`` on an unchanged declaration
resolves to the same campaign and adds a session; editing a target field
OR an optimizer meta-prompt mints a distinct campaign.

* ``root_content_hash`` — the target content hash. ``root_cycle_id`` is
  ``cycle_<root_content_hash>`` (the first session's root cycle, a
  convenience pointer; the forest may hold more sessions —
  ``CampaignStore.list_sessions``). Resume recomputes it for target-drift.
* ``optimizer_prompt_hash`` — the optimizer meta-prompt set's hash; resume
  recomputes it to detect optimizer-prompt drift.
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
