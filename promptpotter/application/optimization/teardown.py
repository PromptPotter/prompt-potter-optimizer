"""Optimization-loop teardown — finalize store, observability, emitter."""

from __future__ import annotations

from typing import TYPE_CHECKING

from promptpotter.domain.phases import StopReason

if TYPE_CHECKING:
    from promptpotter.application.campaign.campaign_setup import Session
    from promptpotter.application.campaign.config import CampaignConfig
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.infrastructure.persistence.session_emitter import (
        CampaignPersistenceEmitter,
    )
    from promptpotter.infrastructure.tracing import ObservabilityBridge


__all__ = ["finalize_optimization_run"]


def finalize_optimization_run(
    cycle: Cycle,
    session: Session,
    emitter: CampaignPersistenceEmitter | None,
    stop_reason: StopReason,
    finished_at: str,
    campaign_config: CampaignConfig,
) -> str | None:
    """Finalize store, obs logger, emitter; return cloud trace id (or None)."""
    if session.cycle_id:
        status = {
            StopReason.USER_PAUSED: "paused",
            StopReason.USER_STOPPED: "stopped",
            StopReason.INTERRUPTED: "interrupted",
        }.get(stop_reason, "completed")
        session.store.campaigns.mark_finished(
            session.backend_id,
            session.cycle_id,
            status=status,
            stop_reason=stop_reason,
            best_accuracy=cycle.best_accuracy,
            best_round=cycle.best_round,
            n_rounds=len(cycle.rounds),
            finished_at=finished_at,
        )
    obs: ObservabilityBridge | None = session.obs
    if obs:
        obs.end_campaign(
            session.obs_campaign_id,
            best_accuracy=cycle.best_accuracy,
            n_rounds=len(cycle.rounds),
            stop_reason=stop_reason,
            best_round=cycle.best_round,
        )
    if emitter:
        from promptpotter.application.intelligence.hard_sample_sorter import (
            build_hard_samples_artifact,
            empty_artifact,
        )

        hs_cfg = campaign_config.optimization.hard_sample_sorter
        if hs_cfg.enabled:
            artifact = build_hard_samples_artifact(
                cycle.rounds,
                cycle_id=session.cycle_id,
                top_k_candidates=hs_cfg.top_k_candidates,
                top_k_samples=hs_cfg.top_k_samples,
            )
        else:
            artifact = empty_artifact(cycle_id=session.cycle_id, disabled=True)
        emitter.write_hard_samples_artifact(artifact)
        emitter.finalize(stop_reason)
    if stop_reason == StopReason.INTERRUPTED or obs is None:
        return None
    return obs.get_langfuse_trace_id(session.obs_campaign_id)
