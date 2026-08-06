"""``Campaign`` — one declared effort, holding a root cycle plus fork/diag/sweep descendants FLAT under ``cycles/``. Two
``new`` calls on an unchanged declaration share the root cycle id and origin score, then diverge from round 1."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import ConfigDict, Field

from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.strict_model import StrictModel


class Campaign(StrictModel):
    """Frozen manifest — identity, config and operator VISIBILITY INTENT only, never run state: that is per-cycle,
    and a stored campaign status was overwritten by whichever cycle finalized last."""

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

    @property
    def root_hop(self) -> CycleHop:
        """This campaign's root cycle as the pair that addresses it. Re-pairing at a call site risks one campaign's id with
        another's root cycle — easy, because a content-addressed ``root_cycle_id`` is shared by siblings."""
        return CycleHop(campaign_id=self.campaign_id, cycle_id=self.root_cycle_id)


__all__ = ["Campaign"]
