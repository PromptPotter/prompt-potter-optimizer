"""Held-out passes: verify, round 0's noise floor and the line bench, each filed on its cycle's ledger."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from promptpotter.application.bench.difficulty import (
    judging_ruler,
    ruler_clock,
    session_ruler_scope,
)
from promptpotter.application.bench.export import ExportProjection
from promptpotter.application.bench.line import (
    bank_campaign_result,
    bench_origin,
    bench_selection,
    cycle_verify,
    declare_line_bench,
    line_passes,
    live_cycle_run,
    own_level,
    read_cycle_bench,
)
from promptpotter.application.initialization.scoring_scope import (
    diagnostic_pass,
    open_diagnostic,
    verb_ledger,
)
from promptpotter.application.initialization.session import LaunchClock, drawn_partition
from promptpotter.application.jobs.launcher.admission import campaign_sends, paid_pass
from promptpotter.application.optimizer_manifest import select_optimizer
from promptpotter.application.run_phase_control import budget_stop
from promptpotter.application.run_stop import RUN_STOPS, run_stop_reason
from promptpotter.application.scoring.closed_rounds import walked_rows
from promptpotter.application.scoring.paired import MemberRows, grade_measurands, read_pair
from promptpotter.application.scoring.search_point_scorer import score_search_point
from promptpotter.application.views.readout import ReadoutProjection
from promptpotter.domain.arms import BankedSearchPointError, Concluded, arm_name
from promptpotter.domain.bench import COLUMN_GRADE, PASS_REFUSAL_INFO, BenchColumn, BenchScore
from promptpotter.domain.campaign import CampaignCycle
from promptpotter.domain.cycle_paths import CycleDir, CycleHop
from promptpotter.domain.paired_reading import (
    ROUND_LIFT_SPEC,
    ArmKey,
    ArmName,
    CellSetName,
    Estimate,
    MeasurandUnit,
    MemberAddress,
    ReadingState,
)
from promptpotter.domain.pass_kinds import PASSES, individual_cells
from promptpotter.domain.phase_acts import PassOpened
from promptpotter.domain.phases import stop_refused
from promptpotter.domain.records.held_out import PassGradedRecord
from promptpotter.domain.records.run_records import PhaseRecord, VerifySaturated
from promptpotter.domain.sample import Sample
from promptpotter.domain.scoring import MAX_PASS_ROWS, ROW_GRADES, CellSheet, Scorer, WalkedCell
from promptpotter.domain.search_point import JobSearchPoint
from promptpotter.domain.spend import Budget
from promptpotter.domain.verification import (
    BenchPasses,
    NoiseBand,
    NoiseFloorPass,
    VerifyPass,
    VerifyReading,
    VerifyStrategy,
)
from promptpotter.infrastructure.llm.spend_book import require_spend_book
from promptpotter.infrastructure.llm.telemetry import append_record
from promptpotter.infrastructure.projections.live_dashboard import DashboardCheckout
from promptpotter.infrastructure.run_scope import scoped
from promptpotter.infrastructure.store.campaign_store.ledger_scan import VERIFY_LEDGER, line_walks
from promptpotter.infrastructure.store.read_model import LedgerIndex, LedgerSpan, read_line
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import ConflictError, DiagnosticError, NextAct
from promptpotter.shared.measurement_context import MeasurementRole, RoleScope
from promptpotter.shared.statistics import difference_interval, paired_mean_t

if TYPE_CHECKING:
    from promptpotter.application.initialization.session import Session


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
def _bracketed(enter: PassOpened) -> Iterator[None]:
    """A pass on its cycle's ledger: entered here, graded by the caller, exited whatever ends it."""
    phase = PASSES[enter.verification].bracket
    append_record(PhaseRecord(phase=phase, step="enter", act=enter))
    try:
        yield
    finally:
        append_record(PhaseRecord(phase=phase, step="exit"))


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


def _difference(
    fresh: CellSheet, recorded: CellSheet, column: BenchColumn, unit: MeasurandUnit
) -> Estimate | None:
    """Fresh − recorded in *column*, with the band of the difference of two independent means
    (`difference_interval`); ``None`` where a side reads no cell."""
    grade = COLUMN_GRADE[column]
    a, b = (list(sheet.column(ROW_GRADES[grade]).values()) for sheet in (fresh, recorded))
    if not a or not b:
        return None
    lo, hi = difference_interval(a, b, binary=grade in fresh.two_valued) or (None, None)
    value = sum(a) / len(a) - sum(b) / len(b)
    return Estimate(
        value=value, ci_lo=lo, ci_hi=hi, n=len(a) + len(b), se=None, unit=unit, form="lift"
    )


def _held(strategy: VerifyStrategy, difference: Estimate | None) -> bool | None:
    """The difference's band reaches 0: the fresh cells cannot tell the level fell."""
    if strategy != "random" or difference is None or difference.ci_hi is None:
        return None
    return difference.ci_hi >= 0.0


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
    banked: VerifyPass,
    *,
    scorer: Scorer,
    fresh: CellSheet,
    recorded: CellSheet,
    measured: MemberRows,
    origin: MemberRows,
) -> VerifyReading:
    """Fresh beside recorded, never pooled: a pooled rate moves with how many cells each holds."""
    unit = scorer.fitness_unit
    fresh_level, recorded_level = own_level(fresh, unit), own_level(recorded, unit)
    difference = _difference(fresh, recorded, "fitness", unit)
    strategy = banked.strategy
    return VerifyReading(
        label=banked.arm.label,
        scorer_id=scorer.id,
        strategy=strategy,
        fresh=fresh_level,
        recorded=recorded_level,
        fitness_difference=difference,
        composite_difference=_difference(fresh, recorded, "composite", "score"),
        vs_origin=read_pair(
            a=origin,
            b=measured,
            cell_set=CellSetName.MEASURED_BY_BOTH,
            cells=None,
            masked=False,
            dataset_hash=None,
            measurands=grade_measurands(scorer),
            spec=ROUND_LIFT_SPEC,
            scope=RoleScope.REPORT,
            instrument_id=measured.instrument_id,
        ),
        held=_held(strategy, difference),
    )


def read_verify(stores: Stores, hop: CycleHop, banked: VerifyPass, scorer: Scorer) -> VerifyReading:
    chain = stores.campaigns.chain(hop)
    # A pass is read before it is banked, so it joins the walks whether or not the ledger holds it.
    walks = [*line_walks(chain), banked.walk()]

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
    instrument_id = drawn_partition(stores.campaigns, hop).instrument_id
    c0 = standing[0].close.origin
    origin = ArmName(key=ArmKey(c0.individual.id), round=0, label=c0.label)
    measured = banked.arm.key
    return verify_reading(
        banked,
        scorer=scorer,
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
        if isinstance(arm, Concluded) and arm.role.is_proposal:
            return round_num, arm
    raise DiagnosticError(
        f"no round of {hop.campaign_id}/{hop.cycle_id} scored candidate {candidate_id!r}."
    )


async def verify_candidate(
    *,
    stores: Stores,
    at: CampaignCycle,
    candidate_id: str,
    samples: int | None,
    strategy: VerifyStrategy,
    seed: int | None,
    acting: str | None = None,
) -> PassGradedRecord | VerifySaturated:
    """The graded pass, or what was already measured where that is every sample."""
    hop = at.hop
    round_num, entry = _scored_candidate(stores, hop, candidate_id)
    arm = arm_name(ArmKey(candidate_id), entry, round_num)
    label = arm.label

    async with open_diagnostic(stores, at, acting=acting) as opened:
        session, campaign_config, campaign = opened.session, opened.config, at.campaign
        schema = session.bound_resolution.chain
        # Before a cell is bought: fresh cells of another searchpoint would read as this one's.
        jsp = opened.points.measured(entry)
        sp_hash = jsp.sp_hash(schema)
        # Carries the prompt: keyed on config alone, candidates sharing a model read as one.
        predicate: dict[str, dict[str, Any]] = dict(
            schema.node_configs(jsp.pipeline_params.keyed_by(session.cell_identity))
        )

        # By sample key under every bank: a re-cut bank re-measures none of the rows it kept.
        prior = stores.archive.measurements_for_config(predicate)
        measured_keys = {m.cell.sample_key for m in prior} & session.bank.position_of.keys()
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
                (
                    banked.arm.round
                    for banked, _ in read_line(
                        [LedgerSpan(ledger_path)], VERIFY_LEDGER
                    ).graded.values()
                ),
                round_num=round_num,
            ),
            unmeasured=len(unmeasured),
        )
        if samples is not None and samples > budget:
            raise DiagnosticError(
                f"{samples} samples is above this candidate's verify budget of {budget} "
                f"({round_cells} cells per candidate per round, lifted by the "
                f"rounds run since the last verification, capped at {len(unmeasured)} unmeasured "
                f"and at {MAX_PASS_ROWS} per pass). "
                "Ask for fewer, or verify again after more rounds.",
                next_acts=(
                    NextAct(kind="verify-candidate", field="samples", value=f"{budget} or fewer"),
                ),
            )
        # The ruler the campaign's elections read now, less the candidate under verification.
        ruler = judging_ruler(
            stores.ruler,
            session_ruler_scope(session),
            ruler_clock(session),
            {sp_hash},
            objective_id=session.state.scoring.scorer.id,
            n_min=campaign_config.optimization.elimination_n_min,
        ).ruler
        picked = pick_fresh(
            unmeasured,
            budget if samples is None else samples,
            strategy=strategy,
            rng=random.Random(seed),
            delta={} if ruler is None else ruler.delta,
        )

        # Bound at the candidate's round: each cell's record names one, and the pass bills there.
        enter = PassOpened(
            verification=MeasurementRole.VERIFY,
            subject=None,
            arm=arm,
            sp_hash=sp_hash,
            rows=len(picked),
            strategy=strategy,
        )
        with verb_ledger(stores, hop), scoped(round=round_num), _bracketed(enter):
            scored = await diagnostic_pass(
                score_search_point(
                    jsp, picked, session, kind=MeasurementRole.VERIFY, measured=candidate_id
                ),
                session,
            )
            cells = scored.cells
            if scored.halt is not None:
                # A pause stops on no outcome, so the rows it took are a fresh pick of their own.
                picked = [s for s in picked if s.key in {key for key, _, _ in cells}]
                if not picked:
                    raise DiagnosticError(
                        f"the verify pass of {label!r} was paused before it measured a cell."
                    ) from None
            banked = VerifyPass(
                arm=arm,
                sp_hash=sp_hash,
                cells=cells,
                sample_keys=[s.key for s in picked],
                stop=None,
                scorer_id=session.state.scoring.scorer.id,
                strategy=strategy,
                seed=seed,
            )
            reading = read_verify(stores, hop, banked, session.state.scoring.scorer)
            if reading.fresh.fitness is None:
                raise DiagnosticError(
                    f"the fresh cells of {label!r} produced no scoreable sample — there is "
                    "nothing to read beside the recorded measurement."
                )
            graded = PassGradedRecord(
                verification=banked,
                subject=None,
                tolerance=None,
                reserve_usd=None,
                reserve_tokens=None,
                reading=reading,
                state=ReadingState.READ,
                refusal=None,
            )
            append_record(graded)
        return graded


async def verify_on_saturation(
    *,
    stores: Stores,
    at: CampaignCycle,
    round_num: int,
    fitness: float | None,
    winner_id: str | None,
    seed: int,
) -> None:
    """A verify the round owed and could not send banks its refusal on the pass, never stopping the run."""
    if fitness is None or fitness < 1.0 or not winner_id:
        return
    # Asked here: the loop consults its own ceiling only at the next round boundary, after this.
    if budget_stop(require_spend_book("the saturation verify")) is not None:
        return
    ledger_path = stores.campaigns.ledger(at.hop)
    mine = [
        banked.arm.round
        for banked, _ in read_line([LedgerSpan(ledger_path)], VERIFY_LEDGER).graded.values()
    ]
    if mine and rounds_since_verified(mine, round_num=round_num) < _VERIFY_LIFT_CAP:
        return
    try:
        await verify_candidate(
            stores=stores,
            at=at,
            candidate_id=winner_id,
            samples=None,
            strategy="random",
            seed=seed,
        )
    except (DiagnosticError, BankedSearchPointError) as exc:
        refused = VerifyPass(
            # No round may name the winner here, so its id is its label.
            arm=ArmName(key=ArmKey(winner_id), round=round_num, label=winner_id),
            sp_hash="",
            cells=[],
            sample_keys=[],
            stop=None,
            scorer_id="",
            strategy="random",
            seed=seed,
        )
        append_record(
            PassGradedRecord(
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
    session: Session, origin_sp: JobSearchPoint, k: int
) -> PassGradedRecord:
    """Round 0's origin re-scored *k* times fresh on the rows it recorded, one pass on the run's
    ledger; a band it cannot read files the pass unread, never stopping the run."""
    stores = session.store
    round_file = stores.campaigns.standing_rounds(session.hop, moment=None).rounds[0].close
    assert round_file.election is not None, "round 0 elects the origin it adopted"
    origin = round_file.origin
    recorded_cells = round_file.cells[round_file.election.ended_on]
    sample_keys = {key for key, _, _ in recorded_cells}
    scoring_set = [s for s in session.bank.samples if s.key in sample_keys]
    scorer = session.state.scoring.scorer
    kept = {s.key for s in scoring_set}
    # Re-folded on exactly the rows the band re-scores, never round 0's whole fold (bl3.B2).
    recorded = own_level(
        walked_rows(stores, [c for c in recorded_cells if c[0] in kept], scorer).standing(),
        scorer.fitness_unit,
    )
    arm = ArmName(key=ArmKey(origin.individual.id), round=0, label=origin.label)
    sp_hash = origin_sp.sp_hash(session.bound_resolution.chain)
    cells: list[WalkedCell] = []
    composites: list[float] = []
    fitnesses: list[float] = []
    enter = PassOpened(
        verification=MeasurementRole.NOISE_FLOOR,
        subject=None,
        arm=arm,
        sp_hash=sp_hash,
        rows=k * len(scoring_set),
        strategy=None,
    )
    with _bracketed(enter):
        for _ in range(k):
            scored = await score_search_point(
                origin_sp, scoring_set, session, kind=MeasurementRole.NOISE_FLOOR, measured=arm.key
            )
            cells += scored.cells
            fitness, composite = scored.scores.fitness, scored.scores.composite_fitness
            if scored.stopped is None and fitness is not None and composite is not None:
                composites.append(composite)
                fitnesses.append(fitness)
        # The run-to-run spread, never the support's bound: k replicates of one prompt.
        band = paired_mean_t(composites, [0.0] * k) if len(composites) == k else None

        reading = (
            None
            if band is None or recorded.composite is None
            else NoiseBand(
                raw=composites,
                band=Estimate(
                    value=band[0],
                    ci_lo=band[1],
                    ci_hi=band[2],
                    n=k,
                    se=band[4],
                    unit="score",
                    form="level",
                ),
                fresh_fitness=Estimate(
                    value=sum(fitnesses) / len(fitnesses),
                    ci_lo=None,
                    ci_hi=None,
                    n=len(fitnesses),
                    se=None,
                    unit=scorer.fitness_unit,
                    form="level",
                ),
                recorded=recorded,
            )
        )
        graded = PassGradedRecord(
            verification=NoiseFloorPass(
                arm=arm,
                sp_hash=sp_hash,
                cells=cells,
                sample_keys=[s.key for s in scoring_set],
                stop=None,
                scorer_id=scorer.id,
            ),
            subject=None,
            tolerance=None,
            reserve_usd=None,
            reserve_tokens=None,
            reading=reading,
            state=ReadingState.MEMBER_UNSCOREABLE if reading is None else ReadingState.READ,
            refusal=(
                f"{len(composites)} of {k} replicates and "
                f"{'no' if recorded.composite is None else 'a'} recorded composite read on its "
                "rows: a band would describe the outage, not the backend."
            )
            if reading is None and not cells
            else None,
        )
        append_record(graded)
    return graded


def refuse_unverifiable(stores: Stores, at: CampaignCycle) -> None:
    """An asked verify is admitted exactly where the served answer says so
    (`CycleEntryFacts.verify`), read before its own pass holds the cycle; the loop's own verify
    runs under the loop's hold and asks nothing."""
    if (refusal := cycle_verify(stores, at).refusal) is not None:
        raise DiagnosticError(PASS_REFUSAL_INFO[refusal])


class BenchTriggerError(ConflictError):
    """The bench pass cannot be sent for this cycle as it stands on disk."""


async def grade_line_bench(*, stores: Stores, at: CampaignCycle, acting: str) -> BenchScore:
    """Admitted exactly where the served status says so (``BenchStatus.can_run``)."""
    async with paid_pass(
        stores=stores,
        pass_kind=MeasurementRole.BENCH,
        at=at,
        declared=Budget(),
        sends=campaign_sends(stores, at),
        acting=acting,
    ):
        status = read_cycle_bench(stores, at).status
        if status.refusal is not None:
            held = "" if status.held_by is None else f" Holder: {status.held_by}."
            raise BenchTriggerError(PASS_REFUSAL_INFO[status.refusal] + held)
        return await _grade(stores, at, acting)


async def _grade(stores: Stores, at: CampaignCycle, acting: str) -> BenchScore:
    hop = at.hop
    standing = stores.campaigns.standing_rounds(hop, moment=None).rounds
    rounds = [standing[n].close for n in sorted(standing)]
    async with open_diagnostic(stores, at, acting=acting) as opened:
        session, config = opened.session, opened.config
        origin_sp = opened.points.measured(rounds[0].origin)
        clock = LaunchClock.now()
        optimizer = select_optimizer(config.optimization)
        cycle_dir = CycleDir(stores.campaigns.cycle_dir(hop))
        dashboard = DashboardCheckout(cycle_dir)
        assert dashboard.live.value() is not None, "a launched cycle with rounds is declared"
        dashboard.drain()
        readout = ReadoutProjection(
            stores,
            session.bound_resolution,
            session.dataset,
            measured_unit=session.backend_client.connector.measured_unit,
            sink=None,
        )
        banked: BenchPasses | None = None
        # The round an `at_end` launch grades under: its last.
        with (
            verb_ledger(stores, hop) as ledger,
            scoped(round=rounds[-1].round),
            LedgerIndex.of(ledger.path).projecting() as projecting,
        ):
            dashboard = projecting.add(dashboard)
            readout.open_readout(hop)
            projecting.add(readout)
            projecting.add(ExportProjection(stores, at, session.dataset))
            try:
                try:
                    banked = await bench_origin(
                        session,
                        origin_sp,
                        origin_id=rounds[0].origin.individual.id,
                    )
                    assert banked is not None, "the line holder holds its origin's pass"
                    banked = await bench_selection(
                        session,
                        rounds,
                        points=opened.points,
                        banked=banked,
                        trigger=config.bench_trigger,
                    )
                except (KeyboardInterrupt, asyncio.CancelledError) as stop:
                    # The pass banked itself stopped, so the line's reading over it is the answer.
                    if not session.state.control.paused(stop):
                        raise
                    banked = line_passes(stores, session.bound_campaign)
                score = declare_line_bench(
                    session,
                    config,
                    passes=banked,
                    selecting=False,
                    ending=live_cycle_run(stores, hop).ending,
                )
            except RUN_STOPS as stop:
                raise stop_refused(run_stop_reason(stop)) from None
            finally:
                # The verb's own clock and cost; each pass it sent banked itself as it was graded.
                if banked is not None:
                    bank_campaign_result(
                        session,
                        clock=clock,
                        finished_at=utcnow_iso(),
                        optimizer_phases=optimizer.phases,
                    )
        return score
