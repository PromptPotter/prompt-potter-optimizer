"""LoopEnv — infrastructure handles threaded through the feedback cycle.

Holds the scoring env, campaign store, cycle id, observability campaign id,
sampled scoring dataset, and degradation checks.  Populated by
``run_optimization`` immediately after building ``LoopState`` and threaded
alongside it into every round loop helper.

Keeping these off ``LoopState`` means the loop state dataclass stays pure
(no ``CampaignStore`` import, no layering violation) and gives every
infrastructure handle a single dedicated home.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from promptpotter.application.optimization.nodes.escalation import DegradationCheck
    from promptpotter.domain.scoring import ScoringEnv
    from promptpotter.infrastructure.store.campaign_store import CampaignStore

__all__ = ["LoopEnv"]


@dataclass
class LoopEnv:
    """Infrastructure handles for the optimization loop.

    ``campaign_store`` and ``cycle_id`` can be ``None`` when the caller
    bootstraps without a persistent campaign directory (tests, notebook
    one-shots).  Everything else is populated by ``run_optimization``
    before the round loop begins.
    """

    scoring_ctx: ScoringEnv | None = None
    campaign_store: CampaignStore | None = None
    cycle_id: str | None = None
    obs_campaign_id: str = ""
    scoring_dataset: list[dict[str, Any]] = field(default_factory=list)
    degradation_checks: list[DegradationCheck] = field(default_factory=list)
    resumed_from_round: int = 0
