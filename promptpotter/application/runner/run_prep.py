from __future__ import annotations

from dataclasses import dataclass

from promptpotter.application.campaign_config import CampaignConfig, apply_cycle_seed
from promptpotter.application.initialization.loop_start import populate_session_scoring
from promptpotter.application.initialization.session import Session
from promptpotter.application.jobs.quota import move_campaign_money
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.application.origin import (
    CampaignOrigin,
    establish_campaign_origin,
)
from promptpotter.application.run_observers import RunObservers, declare_run_wiring
from promptpotter.application.runner.spend_arming import arm_run_controls, stand_held_budget
from promptpotter.application.scoring.query_loop import NEXT_CELL
from promptpotter.application.scoring.sample_measurement import cell_bound
from promptpotter.domain.measurement_provenance import RunSource
from promptpotter.domain.pipeline_overlay import (
    apply_node_overlay,
    overlay_sets_model_outside_allowed,
    permitted_models_from_narrowing,
)
from promptpotter.domain.run_records import CycleSeed
from promptpotter.domain.sample import Sample
from promptpotter.domain.spend import HeldBudget
from promptpotter.infrastructure.llm.spend_book import bound_spend_book


def _read_cycle_seed(session: Session) -> CycleSeed | None:
    if not session.state.cycle_id:
        return None
    return session.store.campaigns.read_cycle_seed(session.hop)


@dataclass(frozen=True)
class PreparedRun:
    """No ceiling of its own: the held one is SET INTO ``campaign_config``, so the value that halts is served."""

    origin: CampaignOrigin
    campaign_config: CampaignConfig
    step_rounds: int | None


async def prepare_run(
    dataset: list[Sample],
    campaign_config: CampaignConfig,
    *,
    session: Session,
    observers: RunObservers,
    budget: HeldBudget,
    langfuse_session_id: str | None,
) -> PreparedRun:
    cb = observers.callbacks

    seed = _read_cycle_seed(session)
    if seed is not None and seed.pipeline_overlay:
        session.pipeline_params = apply_node_overlay(
            session.pipeline_params or {}, seed.pipeline_overlay, session.pipeline_schema
        )
    if seed is not None:
        campaign_config = apply_cycle_seed(campaign_config, seed)
        if (
            overlay_sets_model_outside_allowed(
                seed.pipeline_overlay,
                permitted_models_from_narrowing(campaign_config.optimizer_narrowing),
            )
            and session.state.cycle_id
        ):
            # The ADR-0005 babysit act, stamped here: the mint seam cannot, the index being created at init.
            session.store.campaigns.record_intervention(
                session.hop, kind="disallowed_model_override"
            )
            session.human_intervened = True

    # LAST, after the seed's other knobs; SET, never a `min`: admission already layered seed and standing.
    campaign_config = campaign_config.model_copy(
        update={
            "optimization": campaign_config.optimization.model_copy(
                update={"budget": budget.admitted}
            )
        }
    )
    session.held = budget
    # Declared before origin scoring; the record is the readout, the arming below the enforcement.
    observers.tracing.start(
        config_snapshot=campaign_config.model_dump(mode="json"),
        dataset=dataset,
        langfuse_session_id=langfuse_session_id,
    )
    declare_run_wiring(session, campaign_config, cb.ledger, tracing=observers.tracing)
    if session.state.cycle_id:
        # A fresh mint was admitted before its campaign existed: its ceiling is stored here.
        move_campaign_money(
            budget.standing,
            stores=session.store,
            job_registry=JobRegistry.attach(),
            campaign_id=session.hop.campaign_id,
        )
        stand_held_budget(session.store, session.hop, budget)

    # After the declaration, and before the origin pass, which spends.
    arm_run_controls(session, observers)

    populate_session_scoring(session, campaign_config, source=RunSource.ORIGIN)
    if (book := bound_spend_book()) is not None and (
        cell := await cell_bound(session, session.pipeline_params or {})
    ) is not None:
        # After the scorer, which the stop this raises is finalized with.
        book.refuse_unpriced(cell, NEXT_CELL)
    origin = await establish_campaign_origin(
        session,
        dataset,
        campaign_config,
        seed=seed,
        listener=cb,
    )
    if origin.locked_scoring is not None:
        campaign_config = campaign_config.model_copy(update={"scoring": origin.locked_scoring})

    return PreparedRun(
        origin=origin, campaign_config=campaign_config, step_rounds=budget.step_rounds
    )


__all__ = ["PreparedRun", "prepare_run"]
