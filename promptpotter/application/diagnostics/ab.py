"""The ``ab`` verb's session half; the replay itself is ``bench/resume_and_fork/ab_replay.py``."""

from __future__ import annotations

from typing import TYPE_CHECKING

from promptpotter.application.bench.resume_and_fork.ab_replay import (
    AbReplayError,
    AbReport,
    ab_replay_cycle,
)
from promptpotter.application.initialization.loop_start import arm_diagnostic_scoring
from promptpotter.application.initialization.wiring import bind_cycle_session
from promptpotter.domain.measurement_provenance import RunSource

if TYPE_CHECKING:
    from promptpotter.domain.cycle_paths import CycleHop
    from promptpotter.infrastructure.store.stores import Stores

__all__ = ["ab_replay_campaign"]


async def ab_replay_campaign(
    *,
    stores: Stores,
    hop: CycleHop,
) -> AbReport:
    campaign = stores.campaigns.load_campaign(hop.campaign_id)
    if campaign is None:
        raise AbReplayError(f"campaign {hop.campaign_id!r} has no manifest on disk.")
    session, campaign_config = await bind_cycle_session(stores, campaign, hop)
    arm_diagnostic_scoring(session, campaign_config, source=RunSource.AB)
    return ab_replay_cycle(hop, session, campaign_config)
