"""``Campaign`` — first-class optimization-effort entity (``campaign.json``).

One declared effort: dataset + origin + context + optimizer meta-prompts. Holds
one session root (``cycle_<target_hash>``) plus fork/diag/sweep descendants, flat
under ``cycles/``. Single owner of the frozen ``CampaignConfig`` snapshot.

``campaign_id = {dataset}__{rand6_hex}`` — minted per ``new`` invocation. Two
``new`` calls on an unchanged declaration share the content-addressed root cycle
id + origin score but diverge from round 1 onward. ``root_content_hash`` +
``optimizer_prompt_hash`` let resume detect target / optimizer-prompt drift.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Campaign(BaseModel):
    """Frozen manifest for one optimization campaign — ``campaign.json``.

    Two orthogonal status surfaces:

    * ``status`` / ``finished_at`` — *session run state* (``"active"`` while a
      session is running, ``"finished"`` / ``"interrupted"`` after teardown).
      Set by the runner.
    * ``lifecycle_status`` / ``lifecycle_changed_at`` / ``lifecycle_reason`` —
      *operator visibility intent*. ``"archived"`` hides from the default
      sidebar; ``"deleted"`` is soft — data stays on disk so measurements
      still cache-hit for siblings.
    """

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
    owner_user_id: str = "default"
    lifecycle_status: Literal["active", "archived", "deleted"] = "active"
    lifecycle_changed_at: str = ""
    lifecycle_reason: str = ""
    config: dict[str, Any] = Field(default_factory=dict)


__all__ = ["Campaign"]
