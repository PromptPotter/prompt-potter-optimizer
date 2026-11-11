"""Held-out passes: verify, the noise floor and the line bench, each filed on its cycle's ledger."""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from functools import partial
from typing import TYPE_CHECKING, Any

from promptpotter.application.bench.difficulty import (
    judging_ruler,
    ruler_clock,
    session_ruler_scope,
)
from promptpotter.application.initialization.scoring_scope import (
    diagnostic_pass,
    diagnostic_trace,
    open_diagnostic,
    verb_ledger,
)
from promptpotter.application.initialization.session import LaunchClock
from promptpotter.application.jobs.launcher.admission import campaign_sends, paid_pass
from promptpotter.application.optimizer_manifest import select_optimizer
from promptpotter.application.run_callbacks import RunCallbacks
from promptpotter.application.run_phase_control import budget_stop
from promptpotter.application.runner.bench import bench_selection, own_level
from promptpotter.application.runner.campaign_result import (
    bank_campaign_result,
    bench_origin,
    declare_line_bench,
    line_passes,
    line_run,
    read_cycle_bench,
)
from promptpotter.application.runner.output import RunArtifacts
from promptpotter.application.runner.termination import RUN_STOPS, run_stop_reason
from promptpotter.application.scoring.closed_rounds import cycle_instrument, walked_rows
from promptpotter.application.scoring.paired import MemberRows, grade_measurands, read_pair
from promptpotter.application.scoring.search_point_scorer import reread_cells, score_search_point
from promptpotter.application.views.readout import ReadoutProjection
from promptpotter.domain.arms import BankedSearchPointError, Concluded, arm_name
from promptpotter.domain.bench import BENCH_REFUSAL_INFO, BandedValue, PartitionRecord
from promptpotter.domain.cycle_paths import CycleDir, CycleHop
from promptpotter.domain.measurement_provenance import RunSource
from promptpotter.domain.paired_reading import (
    ROUND_LIFT_SPEC,
    ArmKey,
    ArmName,
    CellSetName,
    MemberAddress,
    ReadingState,
)
from promptpotter.domain.phase_views import VerificationEnterView, VerificationGradedView
from promptpotter.domain.phases import STOP_REASON_INFO, CampaignPhase
from promptpotter.domain.run_records import VerifySaturated
from promptpotter.domain.scoring import MAX_PASS_ROWS
from promptpotter.domain.spend import Budget
from promptpotter.domain.verification import (
    NoiseBand,
    RescoreCount,
    VerificationPass,
    VerifyHeldAbsent,
    VerifyReading,
    VerifyStrategy,
)
from promptpotter.infrastructure.ledger import ledger_chain
from promptpotter.infrastructure.llm.spend_book import require_spend_book
from promptpotter.infrastructure.llm.telemetry import active_cycle_ledger, at_round
from promptpotter.infrastructure.projections.live_dashboard.projection import DashboardCheckout
from promptpotter.infrastructure.store.campaign_store.ledger_scan import (
    scan_ledger_verify,
    scan_ledger_walks,
)
from promptpotter.infrastructure.store.read_model import LedgerIndex
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import ConflictError, DiagnosticError
from promptpotter.shared.measurement_context import (
    MeasuredPass,
    MeasurementRole,
    RoleScope,
    individual_cells,
)
from promptpotter.shared.statistics import mean_ci

if TYPE_CHECKING:
    from promptpotter.domain.bench import BenchScore
    from promptpotter.domain.cycle_paths import CycleHop
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.scoring import CellSheet, Scorer, WalkedCell
    from promptpotter.domain.verification import BenchPasses
    from promptpotter.infrastructure.store.stores import Stores


logger = logging.getLogger(__name__)


_VERIFY_LIFT_CAP = 5


def derive_verify_samples(
    *, round_cell_budget: int, rounds_unverified: int, unmeasured: int
) -> int:
    lift = min(max(rounds_unverified, 1), _VERIFY_LIFT_CAP)
    return max(0, min(round_cell_budget * lift, unmeasured, MAX_PASS_ROWS))


def rounds_since_verified(verified_rounds: Iterable[int], *, round_num: int) -> int:
    """Over the cycle's OWN passes: a fork inherits its parent's measurements, not its assurance."""
    return round_num - max(verified_rounds, default=0)


@contextmanager
def _bracketed(enter: VerificationEnterView) -> Iterator[Callable[..., None]]:
    """A pass on its cycle's ledger: entered here, graded by the caller, exited whatever ends it."""
    ledger = active_cycle_ledger()
    assert ledger is not None
    on_phase = RunCallbacks(ledger).on_phase
    on_phase(CampaignPhase.VERIFY, "enter", view=enter)
    try:
        yield partial(on_phase, CampaignPhase.VERIFY, "graded")
    finally:
        on_phase(CampaignPhase.VERIFY, "exit")


def pick_fresh(
    unmeasured: Sequence[Sample],
    n: int,
    *,
    strategy: VerifyStrategy,
    rng: random.Random,
    delta: Mapping[str, float],
) -> list[Sample]:
    if strategy == "random":
        return rng.sample(unmeasured, n)
    placed = sorted((s for s in unmeasured if s.key in delta), key=lambda s: (-delta[s.key], s.id))
    unplaced = [s for s in unmeasured if s.key not in delta]
    return [*placed, *rng.sample(unplaced, len(unplaced))][:n]


def _held(
    strategy: VerifyStrategy, n_fresh: int, fresh: BandedValue | None, recorded: BandedValue | None
) -> tuple[bool | None, VerifyHeldAbsent | None]:
    if strategy != "random":
        return None, "hard_picks"
    if n_fresh < 2 or fresh is None or recorded is None or fresh.ci_hi is None:
        return None, "under_two_fresh"
    # The tolerance absorbs the float error of two means taken over different row counts.
    return fresh.ci_hi + 1e-9 >= recorded.value, None


def verify_member(
    hop: CycleHop,
    arm: ArmName,
    sheet: CellSheet,
    *,
    bought: int,
    instrument_id: str,
) -> MemberRows:
    return MemberRows(
        address=MemberAddress(
            path=(hop,),
            individual_id=arm.key,
            arm=arm,
            # A fold over every pass report scope may see, so no one pass names it.
            pass_role=None,
        ),
        sheet=sheet,
        bought=bought,
        cut=False,
        scope=RoleScope.REPORT,
        instrument_id=instrument_id,
        dataset_hash=None,
        cell_set_id=None,
    )


def verify_reading(
    banked: VerificationPass,
    *,
    fresh: CellSheet,
    recorded: CellSheet,
    measured: MemberRows,
    origin: MemberRows,
) -> VerifyReading:
    """Fresh beside recorded, never pooled: a pooled rate moves with how many cells each holds."""
    scorer_id = fresh.scorer_id
    fresh_level, recorded_level = own_level(fresh), own_level(recorded)

    def increment(a: BandedValue | None, b: BandedValue | None) -> float | None:
        return None if a is None or b is None else a.value - b.value

    strategy = banked.strategy
    assert strategy is not None, "a verify pass names its draw"
    held, held_absent = _held(
        strategy, fresh_level.n, fresh_level.accuracy, recorded_level.accuracy
    )
    return VerifyReading(
        label=banked.arm.label,
        scorer_id=scorer_id,
        strategy=strategy,
        fresh=fresh_level,
        recorded=recorded_level,
        accuracy_increment=increment(fresh_level.accuracy, recorded_level.accuracy),
        composite_increment=increment(fresh_level.composite, recorded_level.composite),
        vs_origin=read_pair(
            a=origin,
            b=measured,
            cell_set=CellSetName.MEASURED_BY_BOTH,
            cells=None,
            masked=False,
            dataset_hash=None,
            measurands=grade_measurands(scorer_id),
            spec=ROUND_LIFT_SPEC,
            scope=RoleScope.REPORT,
            instrument_id=measured.instrument_id,
        ),
        held=held,
        held_absent=held_absent,
    )


def read_verify(
    stores: Stores, hop: CycleHop, banked: VerificationPass, scorer: Scorer
) -> VerifyReading:
    chain = ledger_chain(CycleDir(stores.campaigns.cycle_dir(hop)), moment=None)
    # A pass is read before it is banked, so it joins the walks whether or not the ledger holds it.
    walks = [*scan_ledger_walks(chain), banked.walk()]

    def rows(cells: list[WalkedCell]) -> CellSheet:
        return walked_rows(stores, cells, scorer)

    def folded(individual_id: str, scope: RoleScope) -> CellSheet:
        return rows(individual_cells(walks, individual_id, scope)).standing()

    fresh = rows(list(banked.walk().cells))
    if not fresh:
        raise DiagnosticError(
            f"the verify pass of {banked.arm.label} holds no answer in the archive."
        )
    standing = stores.campaigns.standing_rounds(hop, moment=None).rounds
    if 0 not in standing:
        raise DiagnosticError(
            f"{hop.campaign_id}/{hop.cycle_id} holds no closed round 0, so {banked.arm.label} has "
            "no origin to be read over."
        )
    instrument_id = cycle_instrument(stores.campaigns.last(hop, PartitionRecord), standing)
    assert instrument_id is not None, "a cycle that closed round 0 drew its bank"
    c0 = standing[0].close.origin
    origin = ArmName(key=ArmKey(c0.individual.id), round=0, label=c0.label)
    measured = banked.arm.key
    return verify_reading(
        banked,
        fresh=fresh,
        recorded=folded(measured, RoleScope.DECISION),
        measured=verify_member(
            hop,
            banked.arm if origin.key != measured else origin,
            folded(measured, RoleScope.REPORT),
            bought=sum(1 for *_, replayed in banked.cells if not replayed),
            instrument_id=instrument_id,
        ),
        origin=verify_member(
            hop,
            origin,
            folded(origin.key, RoleScope.REPORT),
            bought=0,
            instrument_id=instrument_id,
        ),
    )


def _scored_candidate(stores: Stores, hop: CycleHop, candidate_id: str) -> tuple[int, Concluded]:
    # Newest first: a repair re-measures an arm in place without re-proposing it.
    for round_num, held in reversed(
        stores.campaigns.standing_rounds(hop, moment=None).rounds.items()
    ):
        arm = held.close.arms.get(ArmKey(candidate_id))
        if isinstance(arm, Concluded) and arm.role != MeasurementRole.PARENT:
            return round_num, arm
    raise DiagnosticError(
        f"no round of {hop.campaign_id}/{hop.cycle_id} scored candidate {candidate_id!r}."
    )


async def verify_candidate(
    *,
    stores: Stores,
    hop: CycleHop,
    candidate_id: str,
    samples: int | None,
    strategy: VerifyStrategy,
    seed: int | None,
    acting: str | None = None,
) -> VerificationGradedView | VerifySaturated:
    """The graded pass, or what was already measured where that is every sample."""
    round_num, entry = _scored_candidate(stores, hop, candidate_id)
    arm = arm_name(ArmKey(candidate_id), entry, round_num)
    label = arm.label

    async with open_diagnostic(stores, hop, source=RunSource.VERIFY, acting=acting) as opened:
        session, campaign_config, campaign = opened.session, opened.config, opened.campaign
        schema = session.pipeline_schema
        # Before a cell is bought: fresh cells of another searchpoint would read as this one's.
        jsp = opened.points.measured(entry)
        sp_hash = jsp.sp_hash(schema)
        # Carries the prompt: keyed on config alone, candidates sharing a model read as one.
        predicate: dict[str, dict[str, Any]] = dict(schema.node_configs(jsp.pipeline_params))

        # By sample key under every bank: a re-cut bank re-measures none of the rows it kept.
        prior = stores.archive.measurements_for_config(predicate)
        measured_keys = {m.cell.sample_key for m in prior} & session.bank.slot_of.keys()
        # Search split only: `verify_on_saturation` runs inside the loop, which must decide on no bench row.
        search = session.state.scoring.partition.search
        unmeasured = [s for s in search if s.key not in measured_keys]
        round_cells = select_optimizer(campaign_config.optimization).round_cells(len(search))
        if not unmeasured:
            return VerifySaturated(
                dataset_name=campaign.dataset_name, already_measured=len(measured_keys)
            )

        ledger_path = stores.campaigns.ledger(hop)
        budget = derive_verify_samples(
            round_cell_budget=round_cells,
            rounds_unverified=rounds_since_verified(
                (banked.arm.round for banked, _ in scan_ledger_verify(ledger_path).graded.values()),
                round_num=round_num,
            ),
            unmeasured=len(unmeasured),
        )
        if samples is not None and samples > budget:
            raise DiagnosticError(
                f"--samples {samples} is above this candidate's verify budget of {budget} "
                f"({round_cells} cells per candidate per round, lifted by the "
                f"rounds run since the last verification, capped at {len(unmeasured)} unmeasured "
                f"and at {MAX_PASS_ROWS} per pass). "
                f"Pass {budget} or fewer, or verify again after more rounds."
            )
        # The ruler the campaign's elections read now, less the candidate under verification.
        ruler = judging_ruler(
            stores.ruler,
            session_ruler_scope(session),
            ruler_clock(session),
            {sp_hash},
            objective_id=session.state.scoring.scorer.id,
            n_min=campaign_config.optimization.elimination_n_min,
            enable_2pl=campaign_config.optimization.enable_2pl_graduation,
        ).ruler
        picked = pick_fresh(
            unmeasured,
            budget if samples is None else samples,
            strategy=strategy,
            rng=random.Random(seed),
            delta={} if ruler is None else ruler.delta,
        )

        logger.info(
            "verify %s (%s/%s): scoring %d new sample(s); %d already in archive for this config",
            label,
            hop.campaign_id,
            hop.cycle_id,
            len(picked),
            len(measured_keys),
        )
        # Bound at the candidate's round: each cell's record names one, and the pass bills there.
        enter = VerificationEnterView(
            verification="verify",
            subject=None,
            arm=arm,
            sp_hash=sp_hash,
            rows=len(picked),
            strategy=strategy,
        )
        with diagnostic_trace(stores, hop), at_round(round_num), _bracketed(enter) as grade:
            try:
                cells = (
                    await diagnostic_pass(
                        score_search_point(
                            jsp,
                            picked,
                            session,
                            label=MeasurementRole.VERIFY,
                            measured=MeasuredPass(candidate_id, MeasurementRole.VERIFY),
                        ),
                    )
                ).cells
            except (KeyboardInterrupt, asyncio.CancelledError) as stop:
                if not session.state.control.paused(stop):
                    raise
                # A pause stops on no outcome, so the rows walked are a fresh pick of their own;
                # each was unmeasured, so a row the archive holds now is one this pass bought.
                walked = reread_cells(jsp, picked, session, label=MeasurementRole.VERIFY)
                cells = [(key, answer, False) for key, answer, _ in walked.addresses()]
                picked = [s for s in picked if s.key in {key for key, _, _ in cells}]
                if not picked:
                    raise DiagnosticError(
                        f"the verify pass of {label!r} was paused before it measured a cell."
                    ) from None
            banked = VerificationPass(
                kind="verify",
                arm=arm,
                sp_hash=sp_hash,
                cells=cells,
                sample_keys=[s.key for s in picked],
                stop=None,
                scorer_id=session.state.scoring.scorer.id,
                strategy=strategy,
                seed=seed,
                reads_before=None,
            )
            reading = read_verify(stores, hop, banked, session.state.scoring.scorer)
            if reading.fresh.accuracy is None:
                raise DiagnosticError(
                    f"the fresh cells of {label!r} produced no scoreable sample — there is "
                    "nothing to read beside the recorded measurement."
                )
            graded = VerificationGradedView(
                verification=banked,
                subject=None,
                tolerance=None,
                reserve_usd=None,
                reserve_tokens=None,
                reading=reading,
                state=ReadingState.READ,
                refusal=None,
            )
            grade(view=graded)
        return graded


async def verify_on_saturation(
    *,
    stores: Stores,
    hop: CycleHop,
    round_num: int,
    accuracy: float | None,
    winner_id: str | None,
    seed: int,
    cb: RunCallbacks,
) -> None:
    """A verify the round owed and could not send banks its refusal on the pass, never stopping the run."""
    if accuracy is None or accuracy < 1.0 or not winner_id:
        return
    # Asked here: the loop consults its own ceiling only at the next round boundary, after this.
    if budget_stop(require_spend_book("the saturation verify")) is not None:
        return
    ledger_path = stores.campaigns.ledger(hop)
    mine = [banked.arm.round for banked, _ in scan_ledger_verify(ledger_path).graded.values()]
    if mine and rounds_since_verified(mine, round_num=round_num) < _VERIFY_LIFT_CAP:
        return
    try:
        await verify_candidate(
            stores=stores,
            hop=hop,
            candidate_id=winner_id,
            samples=None,
            strategy="random",
            seed=seed,
        )
    except (DiagnosticError, BankedSearchPointError) as exc:
        refused = VerificationPass(
            kind="verify",
            # No round may name the winner here, so its id is its label.
            arm=ArmName(key=ArmKey(winner_id), round=round_num, label=winner_id),
            sp_hash="",
            cells=[],
            sample_keys=[],
            stop=None,
            scorer_id="",
            strategy="random",
            seed=seed,
            reads_before=None,
        )
        cb.on_phase(
            CampaignPhase.VERIFY,
            "graded",
            view=VerificationGradedView(
                verification=refused,
                subject=None,
                tolerance=None,
                reserve_usd=None,
                reserve_tokens=None,
                reading=None,
                state=ReadingState.MEMBER_UNSCOREABLE,
                refusal=str(exc),
            ),
        )


async def measure_noise_floor(
    *,
    stores: Stores,
    hop: CycleHop,
    k: RescoreCount,
) -> VerificationGradedView:
    """The band is a pass on the cycle's ledger, graded as it banks."""
    async with paid_pass(
        stores=stores,
        verb="noise-floor",
        hop=hop,
        declared=Budget(),
        sends=campaign_sends(stores, hop),
    ):
        return await _rescore_origin(stores, hop, k)


async def _rescore_origin(stores: Stores, hop: CycleHop, k: RescoreCount) -> VerificationGradedView:
    standing = stores.campaigns.standing_rounds(hop, moment=None).rounds.get(0)
    if standing is None:
        raise DiagnosticError(
            f"round 0 never closed in {hop.campaign_id}/{hop.cycle_id} — the origin was never scored."
        )
    round_file = standing.close
    if round_file.election is None:
        raise DiagnosticError(f"round 0 of {hop.campaign_id}/{hop.cycle_id} carries no origin arm.")
    origin = round_file.origin
    recorded_cells = round_file.cells.arms[round_file.election.ended_on]
    if not recorded_cells:
        raise DiagnosticError(
            f"origin arm in {hop.campaign_id}/{hop.cycle_id} round 0 carries no scored samples."
        )
    sample_keys = {key for key, _, _ in recorded_cells}

    async with open_diagnostic(stores, hop, source=RunSource.NOISE_FLOOR) as opened:
        session = opened.session
        jsp = opened.points.measured(origin)
        scoring_set = [s for s in session.bank.samples if s.key in sample_keys]
        if not scoring_set:
            raise DiagnosticError(
                f"none of round 0's {len(sample_keys)} scored sample(s) is in the bank the cycle drew."
            )
        scorer = session.state.scoring.scorer
        kept = {s.key for s in scoring_set}
        # Re-folded on exactly the rows the band re-scores, never round 0's whole fold (bl3.B2).
        recorded = own_level(
            walked_rows(stores, [c for c in recorded_cells if c[0] in kept], scorer).standing()
        )
        if recorded.composite is None:
            raise DiagnosticError(
                f"round 0 of {hop.campaign_id}/{hop.cycle_id} read no cell on the band's rows, so "
                "there is no recorded composite for the band to stand beside."
            )

        arm = ArmName(key=ArmKey(origin.individual.id), round=0, label=origin.label)
        sp_hash = jsp.sp_hash(session.pipeline_schema)
        logger.info(
            "noise-floor %s/%s: re-scoring origin C0 %dx (force_fresh) on %d samples",
            hop.campaign_id,
            hop.cycle_id,
            k,
            len(scoring_set),
        )
        cells: list[WalkedCell] = []
        composites: list[float] = []
        accuracies: list[float] = []
        enter = VerificationEnterView(
            verification="noise_floor",
            subject=None,
            arm=arm,
            sp_hash=sp_hash,
            rows=k * len(scoring_set),
            strategy=None,
        )
        with diagnostic_trace(stores, hop), _bracketed(enter) as grade:
            for i in range(k):
                scored = await diagnostic_pass(
                    score_search_point(
                        jsp,
                        scoring_set,
                        session,
                        label=f"noise_floor_{i}",
                        measured=None,
                        force_fresh=True,
                    ),
                )
                accuracy, composite = scored.scores.accuracy, scored.scores.composite_fitness
                if accuracy is None or composite is None:
                    raise DiagnosticError(
                        f"noise-floor rescore {i + 1}/{k} of {hop.campaign_id}/{hop.cycle_id} "
                        "measured no cell — the band would describe the outage, not the backend."
                    )
                cells += scored.cells
                composites.append(composite)
                accuracies.append(accuracy)
                logger.info("noise-floor rescore %d/%d: composite=%.4f", i + 1, k, composite)

            band = mean_ci(composites)
            assert band is not None  # a RescoreCount is two or more
            mean_composite, ci_lo, ci_hi = band
            graded = VerificationGradedView(
                verification=VerificationPass(
                    kind="noise_floor",
                    arm=arm,
                    sp_hash=sp_hash,
                    cells=cells,
                    sample_keys=[s.key for s in scoring_set],
                    stop=None,
                    scorer_id=scorer.id,
                    strategy=None,
                    seed=None,
                    reads_before=None,
                ),
                subject=None,
                tolerance=None,
                reserve_usd=None,
                reserve_tokens=None,
                reading=NoiseBand(
                    raw=composites,
                    band=BandedValue(value=mean_composite, ci_lo=ci_lo, ci_hi=ci_hi),
                    fresh_accuracy=sum(accuracies) / len(accuracies),
                    recorded=recorded,
                ),
                state=ReadingState.READ,
                refusal=None,
            )
            grade(view=graded)
        return graded


class BenchTriggerError(ConflictError):
    """The bench pass cannot be sent for this cycle as it stands on disk."""


async def grade_line_bench(*, stores: Stores, hop: CycleHop, acting: str) -> BenchScore:
    """Admitted exactly where the served status says so (``BenchStatus.can_run``); *acting* is
    the command it grades for, whose `cancel` stops it."""
    async with paid_pass(
        stores=stores,
        verb="bench",
        hop=hop,
        declared=Budget(),
        sends=campaign_sends(stores, hop),
        acting=acting,
    ):
        campaign = stores.campaigns.load_campaign(hop.campaign_id)
        if campaign is None:
            raise BenchTriggerError(f"campaign {hop.campaign_id!r} has no manifest on disk.")
        status = read_cycle_bench(stores, campaign, hop).status
        if status.refusal is not None:
            held = "" if status.held_by is None else f" Holder: {status.held_by}."
            raise BenchTriggerError(BENCH_REFUSAL_INFO[status.refusal] + held)
        return await _grade(stores, hop, acting)


async def _grade(stores: Stores, hop: CycleHop, acting: str) -> BenchScore:
    standing = stores.campaigns.standing_rounds(hop, moment=None).rounds
    rounds = [standing[n].close for n in sorted(standing)]
    async with open_diagnostic(
        stores, hop, source=RunSource.OPTIMIZATION_LOOP, acting=acting
    ) as opened:
        session, config = opened.session, opened.config
        origin_sp = opened.points.measured(rounds[0].origin)
        clock = LaunchClock.now()
        optimizer = select_optimizer(config.optimization)
        cycle_dir = CycleDir(stores.campaigns.cycle_dir(hop))
        dashboard = DashboardCheckout(cycle_dir)
        assert dashboard.live.value() is not None, "a launched cycle with rounds is declared"
        dashboard.drain()
        readout = ReadoutProjection.for_campaign(session, config, sink=None)
        banked: BenchPasses | None = None
        # The round an `at_end` launch grades under: its last.
        with (
            verb_ledger(stores, hop) as ledger,
            at_round(rounds[-1].round),
            LedgerIndex.of(ledger.path).projecting() as projecting,
        ):
            dashboard = projecting.add(dashboard)
            readout.open_readout(cycle_dir)
            projecting.add(readout)
            projecting.add(RunArtifacts(session, config))
            cb = RunCallbacks(ledger)
            try:
                try:
                    banked = await bench_origin(
                        session,
                        origin_sp,
                        origin_id=rounds[0].origin.individual.id,
                        spend=dashboard.live.state.spend,
                        cb=cb,
                    )
                    assert banked is not None, "the line holder holds its origin's pass"
                    banked = await bench_selection(
                        session, rounds, points=opened.points, banked=banked, cb=cb
                    )
                except (KeyboardInterrupt, asyncio.CancelledError) as stop:
                    # The pass banked itself stopped, so the line's reading over it is the answer.
                    if not session.state.control.paused(stop):
                        raise
                    banked = line_passes(stores, hop.campaign_id)
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
            # Before the drain renders the export's headline and hands the registry its copy.
            stores.campaigns.restate_export_bench(hop, score)
        return score
