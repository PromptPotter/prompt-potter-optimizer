"""Runs outside any run, on the cycle's own ledger, through the steps an ``at_end`` launch takes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from promptpotter.application.initialization.loop_start import open_diagnostic, verb_ledger
from promptpotter.application.initialization.session import LaunchClock
from promptpotter.application.jobs.launcher.admission import paid_pass
from promptpotter.application.optimizer_manifest import select_optimizer
from promptpotter.application.run_callbacks import RunCallbacks
from promptpotter.application.runner.bench import bench_selection
from promptpotter.application.runner.campaign_result import (
    bank_campaign_result,
    bench_origin,
    declare_line_bench,
    line_run,
    read_cycle_bench,
)
from promptpotter.application.runner.output import publish_export, write_review_md
from promptpotter.application.runner.termination import RUN_STOPS, run_stop_reason
from promptpotter.application.views.readout import ReadoutProjection
from promptpotter.domain.cycle_paths import CycleDir
from promptpotter.domain.measurement_provenance import RunSource
from promptpotter.domain.phases import STOP_REASON_INFO
from promptpotter.domain.spend import Budget
from promptpotter.infrastructure.llm.telemetry import at_round
from promptpotter.infrastructure.projections.live_dashboard.projection import DashboardCheckout
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import ConflictError

if TYPE_CHECKING:
    from promptpotter.domain.bench import BenchPasses, BenchScore
    from promptpotter.domain.cycle_paths import CycleHop
    from promptpotter.infrastructure.store.stores import Stores


class BenchTriggerError(ConflictError):
    """The bench pass cannot be sent for this cycle as it stands on disk."""


async def grade_line_bench(*, stores: Stores, hop: CycleHop) -> BenchScore:
    """Admitted exactly where the served status says so (``BenchStatus.can_grade``)."""
    async with paid_pass(stores=stores, verb="bench", hop=hop, declared=Budget()):
        campaign = stores.campaigns.load_campaign(hop.campaign_id)
        if campaign is None:
            raise BenchTriggerError(f"campaign {hop.campaign_id!r} has no manifest on disk.")
        refusal = read_cycle_bench(stores, campaign, hop).status.refusal
        if refusal is not None:
            raise BenchTriggerError(refusal)
        return await _grade(stores, hop)


async def _grade(stores: Stores, hop: CycleHop) -> BenchScore:
    standing = stores.campaigns.standing_rounds(hop, moment=None).rounds
    rounds = [standing[n].close for n in sorted(standing)]
    opened = await open_diagnostic(stores, hop, source=RunSource.OPTIMIZATION_LOOP)
    session, config, framing = opened.session, opened.config, opened.framing
    origin_sp = opened.points.measured(rounds[0].origin)
    clock = LaunchClock.now()
    optimizer = select_optimizer(config.optimization)
    cycle_dir = CycleDir(stores.campaigns.cycle_dir(hop))
    dashboard = DashboardCheckout.over(cycle_dir)
    assert dashboard is not None, "a cycle with standing rounds was launched, so it is declared"
    readout = ReadoutProjection.for_campaign(session, config, sink=None)
    banked: BenchPasses | None = None
    # The round an `at_end` launch grades under: its last.
    with verb_ledger(stores, hop) as ledger, at_round(rounds[-1].round):
        ledger.bind(dashboard)
        readout.open_readout(cycle_dir)
        ledger.bind(readout)
        cb = RunCallbacks(ledger)
        try:
            banked = await bench_origin(
                session,
                origin_sp,
                origin_id=rounds[0].origin.individual.id,
                spend=dashboard.state.spend,
                cb=cb,
            )
            assert banked is not None, "the line holder holds its origin's pass"
            banked = await bench_selection(
                session, rounds, points=opened.points, banked=banked, cb=cb
            )
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
            # The verb's own clock and cost; each pass it sent banked itself as it was graded.
            if banked is not None:
                bank_campaign_result(
                    stores,
                    hop,
                    ledger=ledger,
                    clock=clock,
                    finished_at=utcnow_iso(),
                    optimizer_phases=optimizer.phases,
                )
            dashboard.drain()
    stores.campaigns.restate_export_bench(hop, score)
    publish_export(session)
    write_review_md(
        session,
        accuracy_ceiling=config.accuracy_ceiling,
        optimizer=optimizer,
        framing=framing,
    )
    return score
