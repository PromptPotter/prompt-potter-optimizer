"""``Campaign`` — first-class optimization-effort entity (``campaign.json``).

One declared effort: dataset + origin + context + optimizer prompts. Holds
one session root (``cycle_<target_hash>``) plus fork/diag/sweep descendants, flat
under ``cycles/``. Single owner of the frozen ``CampaignConfig`` snapshot.

``campaign_id = {dataset}__{rand6_hex}`` — minted per ``new`` invocation. Two
``new`` calls on an unchanged declaration share the content-addressed root cycle
id + origin score but diverge from round 1 onward. ``root_content_hash`` +
``optimizer_prompt_hash`` let resume detect target / optimizer-prompt drift.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import ConfigDict, Field

from promptpotter.domain.strict_model import StrictModel


class Campaign(StrictModel):
    """Frozen manifest for one optimization campaign — ``campaign.json``.

    Identity + config + operator visibility intent ONLY — no run state. Run
    state is owned per-cycle (``index.json::status`` + ``run_phase``); campaign
    surfaces derive "how is this campaign doing" from its cycles on read (the
    old stored ``status``/``finished_at`` were overwritten by whichever cycle —
    root, fork, sweep, diag — finalized last, and never reset on resume).

    ``lifecycle_status`` / ``lifecycle_changed_at`` / ``lifecycle_reason`` =
    *operator visibility intent*. ``"archived"`` hides from the default
    sidebar; ``"deleted"`` is soft — data stays on disk so measurements
    still cache-hit for siblings. ``"checkin"`` is a campaign still authoring
    its origin (minted on first ingest action, no loop yet) — it flips to
    ``"active"`` at Start; ``root_content_hash`` / ``config`` are empty until
    then.
    """

    model_config = ConfigDict(frozen=True)

    campaign_id: str
    dataset_name: str
    label: str = ""
    created_at: str
    root_cycle_id: str
    root_content_hash: str = ""
    optimizer_prompt_hash: str = ""
    backend_id: str = ""
    owner_user_id: str = "default"
    lifecycle_status: Literal["active", "archived", "deleted", "checkin"] = "active"
    lifecycle_changed_at: str = ""
    lifecycle_reason: str = ""
    config: dict[str, Any] = Field(default_factory=dict)


__all__ = ["Campaign"]
