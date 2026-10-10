"""Runs outside any run, on the cycle's own ledger, through the steps an ``at_end`` launch takes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from promptpotter.application.bench.task_context import campaign_framing
from promptpotter.application.initialization.loop_start import arm_diagnostic_scoring, verb_ledger
from promptpotter.application.initialization.wiring import bind_cycle_session
from promptpotter.application.jobs.quota import paid_verb
from promptpotter.application.optimizer_manifest import select_optimizer
from promptpotter.application.run_observers import RunCallbacks
from promptpotter.application.runner.bench import bench_selection
from promptpotter.application.runner.campaign_result import (
    bank_campaign_result,
    bench_origin,
    declare_line_bench,
    line_run,
    read_cycle_bench,
)
from promptpotter.application.runner.output import write_review_md
from promptpotter.application.runner.termination import RUN_STOPS, run_stop_reason
from promptpotter.application.views.readout import ReadoutProjection
from promptpotter.domain.cycle_paths import CycleDir
from promptpotter.domain.measurement_provenance import RunSource
from promptpotter.domain.phases import STOP_REASON_INFO
from promptpotter.infrastructure.projections.live_dashboard.projection import materializing_over
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import ConflictError

if TYPE_CHECKING:
    from promptpotter.domain.bench import BenchPasses, BenchScore
    from promptpotter.domain.campaign import Campaign
    from promptpotter.domain.cycle_paths import CycleHop
    from promptpotter.infrastructure.store.stores import Stores

__all__ = ["BenchTriggerError", "grade_line_bench"]


class BenchTriggerError(ConflictError):
    """The bench pass cannot be sent for this cycle as it stands on disk."""


async def grade_line_bench(*, stores: Stores, hop: CycleHop) -> BenchScore:
    """Admitted exactly where the served status says so (``BenchStatus.can_grade``)."""
    async with paid_verb(stores=stores, bucket="bench", hop=hop):
        campaign = stores.campaigns.load_campaign(hop.campaign_id)
        if campaign is None:
            raise BenchTriggerError(f"campaign {hop.campaign_id!r} has no manifest on disk.")
        refusal = read_cycle_bench(stores, campaign, hop).status.refusal
        if refusal is not None:
            raise BenchTriggerError(refusal)
        return await _grade(stores, campaign, hop)


async def _grade(stores: Stores, campaign: Campaign, hop: CycleHop) -> BenchScore:
    standing = stores.campaigns.standing_rounds(hop).rounds
    rounds = [standing[n].close for n in sorted(standing)]
    session, config = await bind_cycle_session(stores, campaign, hop)
    arm_diagnostic_scoring(session, config, source=RunSource.OPTIMIZATION_LOOP)
    partition = session.scoring.require_partition()
    framing = campaign_framing(stores, config, session.dataset_name)
    origin_sp = rounds[0].origin.searchpoint(
        schema=session.pipeline_schema, framing=framing, demo=partition.demo
    )
    started_at = utcnow_iso()
    optimizer = select_optimizer(config.optimization)
    cycle_dir = CycleDir(stores.campaigns.cycle_dir(hop))
    dashboard = materializing_over(cycle_dir, hop)
    assert dashboard is not None, "a cycle with standing rounds was launched, so it is declared"
    readout = ReadoutProjection.for_campaign(session, config, sink=None)
    banked: BenchPasses | None = None
    with verb_ledger(stores, hop) as ledger:
        ledger.bind(dashboard)
        readout.open_readout(cycle_dir)
        ledger.bind(readout)
        cb = RunCallbacks(ledger)
        try:
            banked = await bench_origin(
                session,
                origin_sp,
                origin_id=rounds[0].origin.candidate_id,
                spend=dashboard.state.spend,
                cb=cb,
            )
            assert banked is not None, "the line holder holds its origin's pass"
            banked = await bench_selection(session, rounds, framing=framing, banked=banked, cb=cb)
            score = declare_line_bench(
                session,
                config,
                cb,
                passes=banked,
                selecting=False,
                ending=line_run(stores, hop).ending,
            )
        except RUN_STOPS as stop:
            info = STOP_REASON_INFO[run_stop_reason(stop)]
            raise BenchTriggerError(f"{info.label}. {info.next_step}".strip()) from None
        finally:
            dashboard.drain()
            # The verb's own clock and cost; each pass it sent banked itself as it was graded.
            if banked is not None:
                bank_campaign_result(
                    stores,
                    hop,
                    started_at=started_at,
                    finished_at=utcnow_iso(),
                    optimizer_phases=optimizer.phases,
                )
    stores.campaigns.restate_export_bench(hop, score)
    write_review_md(
        session,
        accuracy_ceiling=config.accuracy_ceiling,
        optimizer=optimizer,
        framing=framing,
    )
    return score
