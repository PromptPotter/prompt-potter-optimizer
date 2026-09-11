"""The ``ab`` verb's session half: open a session on ANY campaign by id and replay it. The replay itself is
``optimization/resume_and_fork/ab_replay.py``, beside the replayers it shares with resume."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from promptpotter.application.initialization.loop_start import arm_diagnostic_scoring
from promptpotter.application.initialization.wiring import init_services
from promptpotter.application.optimization.resume_and_fork.ab_replay import (
    AbReplayError,
    AbReport,
    ab_replay_cycle,
)
from promptpotter.application.pipeline_resolve import resolve_campaign_config

if TYPE_CHECKING:
    from promptpotter.domain.cycle_paths import CycleHop
    from promptpotter.infrastructure.store.stores import Stores
    from promptpotter.shared.identity import IdentityContext

__all__ = ["ab_replay_campaign"]


async def ab_replay_campaign(
    *,
    stores: Stores,
    identity: IdentityContext,
    hop: CycleHop,
    log: Callable[[str], None] | None = None,
) -> AbReport:
    """Replay *hop*'s whole campaign under the current engine + scorer, on the config a resume of it would read.
    Zero LLM calls; *hop*'s round 0 is the origin the δ ruler is calibrated on."""
    campaign = stores.campaigns.load_campaign(hop.campaign_id)
    if campaign is None:
        raise AbReplayError(f"campaign {hop.campaign_id!r} has no manifest on disk.")
    session = await init_services(
        backend_id=campaign.backend_id,
        dataset_name=campaign.dataset_name,
        identity=identity,
        stores=stores,
    )
    session.campaign_id = hop.campaign_id
    session.state.cycle_id = hop.cycle_id
    campaign_config = resolve_campaign_config(stores, campaign, hop)
    arm_diagnostic_scoring(
        session, campaign_config, source=f"ab:{hop.campaign_id}:{hop.cycle_id}", log=log
    )
    return ab_replay_cycle(
        hop,
        session,
        campaign_config.optimization.elimination_n_min,
        enable_2pl=campaign_config.optimization.enable_2pl_graduation,
    )
