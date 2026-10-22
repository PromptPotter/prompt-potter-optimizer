from __future__ import annotations

import asyncio
import functools
import selectors
import time
from collections.abc import Callable, Coroutine, Iterable, Mapping, Sequence
from itertools import count
from pathlib import Path
from types import EllipsisType
from typing import Any, cast

from offline_world import FakeBackend

from promptpotter.application.bench.cycle import Cycle
from promptpotter.application.bench.difficulty import DifficultyView
from promptpotter.application.campaign_config import (
    CampaignConfig,
    OptimizationConfig,
    declare_run,
)
from promptpotter.application.initialization.session import LaunchState, ScorerSetup, Session
from promptpotter.application.knobs import bench_defaults_pin
from promptpotter.application.optimizer_manifest import resolve_optimizer, select_optimizer
from promptpotter.application.optimizers.nodes import ReadArm, RoundContext, SliceMap
from promptpotter.application.optimizers.potter.knobs import PoBBKnobs
from promptpotter.application.optimizers.potter.records import (
    POTTER_MANIFEST,
    L1Readout,
    L2Memory,
    L3Memory,
    Ladder,
)
from promptpotter.application.run_callbacks import RunCallbacks
from promptpotter.application.run_phase_control import RunControl
from promptpotter.application.runner.inner.tasks import (
    InnerCell,
    InnerCells,
    InnerTaskSpec,
)
from promptpotter.application.scoring.formula.compiler import compile_scorer
from promptpotter.connectors.protocol import PROBE_WORKLOAD, Connector, cell_envelope_of
from promptpotter.connectors.termnorm import termnorm_wire_adapter
from promptpotter.domain.bench import (
    BENCH_HEADLINE,
    BandedValue,
    BankPartition,
    BenchScore,
    LiftCost,
    LineRun,
    OwnLevel,
    bench_status,
    partition_bank,
)
from promptpotter.domain.campaign import Campaign, Optimize, Purpose
from promptpotter.domain.cells import Seat
from promptpotter.domain.cycle_paths import CycleDir, CycleHop, WorkspaceDir
from promptpotter.domain.measurement_provenance import RunSource
from promptpotter.domain.opt_search_point import NodePrompt, OptSearchPoint
from promptpotter.domain.optimizer_state import Critique, OptimizerState
from promptpotter.domain.paired_reading import ArmKey, PairedReading, ReadingState
from promptpotter.domain.phases import RunPhase, StopReason
from promptpotter.domain.pipeline_schema import NodePromptInfo, PipelineNode, PipelineSchema
from promptpotter.domain.pool import BlockPool, DemoPool
from promptpotter.domain.results import (
    ArmRole,
    ArmSlot,
    ArmState,
    Broken,
    Cut,
    CutKind,
    CycleResult,
    DegradationHealth,
    Election,
    Eliminated,
    Elimination,
    Invalid,
    Measured,
    OverlapReading,
    Proposed,
    RoundResult,
    ScoreSummary,
    Skipped,
    arm_end,
)
from promptpotter.domain.ruler import (
    AbilityReading,
    LevelSeries,
    RulerScope,
    RulerStamp,
    ThetaCaveat,
    flat_ruler_id,
)
from promptpotter.domain.run_records import RunPhaseRecord, SampleScoredRecord
from promptpotter.domain.sample import Bank, Sample
from promptpotter.domain.scoring import CellSheet, GradedCell, Scorer, WalkedCell
from promptpotter.domain.spend import Budget, CeilingMeter, SpendBucket, SpendRollup
from promptpotter.domain.wounds import NurseOwner, RuntimeFailure, ValidationFailure
from promptpotter.infrastructure.backend import BackendClient
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.llm.spend_book import Hold, SpendBook
from promptpotter.infrastructure.llm.telemetry import at_round
from promptpotter.infrastructure.producer_lock import hold_cycle
from promptpotter.infrastructure.store.campaign_store.store import CampaignStore
from promptpotter.infrastructure.store.dataset_access import ResolvedDataset
from promptpotter.infrastructure.store.io import write_json
from promptpotter.infrastructure.store.layout import inner_sandbox_dir, sandbox_owner_path
from promptpotter.infrastructure.store.stores import Stores, build_stores
from promptpotter.shared.identity import default_identity
from promptpotter.shared.measurement_context import MeasurementRole


class _SpentWaits(selectors.DefaultSelector):
    """A selector that spends a wait on the clock instead of sitting through it."""

    now = 0.0

    def select(self, timeout: float | None = None) -> Any:
        if timeout is None:  # nothing scheduled: only real I/O can wake the loop
            return super().select(None)
        self.now += max(timeout, 0.0)
        return super().select(0)


class _JumpingClockLoop(asyncio.SelectorEventLoop):
    """Its clock jumps to the next timer: sleeps keep their ORDER and cost no wall clock."""

    def __init__(self) -> None:
        self._waits = _SpentWaits()
        super().__init__(self._waits)

    def time(self) -> float:
        return self._waits.now


def jumping_clock[T](main: Coroutine[Any, Any, T]) -> T:
    with asyncio.Runner(loop_factory=_JumpingClockLoop) as runner:
        return runner.run(main)


def on_jumping_clock[**P, T](test: Callable[P, Coroutine[Any, Any, T]]) -> Callable[P, T]:
    @functools.wraps(test)
    def run(*args: P.args, **kwargs: P.kwargs) -> T:
        return jumping_clock(test(*args, **kwargs))

    return run


# Two labels on purpose: with as many truths as rows, no constant answerer is detectable.
_TRUTH = ["TRUE", "FALSE", "TRUE", "FALSE"]


def prompted(node: str = "solve", **fields: str) -> OptSearchPoint:
    """An individual setting *fields* on one prompt node and nothing else."""
    return OptSearchPoint(prompts={node: NodePrompt(fields=fields)})


def pipeline_schema(nodes: Sequence[PipelineNode], **fields: Any) -> PipelineSchema:
    return PipelineSchema(
        declared_nodes=list(nodes), pipelines={"default": [n.name for n in nodes]}, **fields
    )


def measurement(
    sample_id: int,
    fitness: float | None = 1.0,
    *,
    objective: float | None = None,
    **wire: Any,
) -> GradedCell:
    """``wire`` bends any key of the cell's record; an ungraded cell is ``fitness=None``. Its
    ``sample_key`` is the slot spelled as a string unless *wire* names one."""
    grade = (
        {}
        if fitness is None
        else {
            "fitness": fitness,
            "objective": round(fitness * 0.6, 6) if objective is None else objective,
        }
    )
    return GradedCell.from_wire({"sample_key": str(sample_id), **grade, **wire})


def measurements(
    grades: Sequence[float], sample_ids: Sequence[int] | None = None, **wire: Any
) -> list[GradedCell]:
    ids = range(len(grades)) if sample_ids is None else sample_ids
    return [measurement(sid, g, **wire) for sid, g in zip(ids, grades, strict=True)]


def sheet(cells: Iterable[GradedCell], scorer_id: str = "test") -> CellSheet:
    """Each cell under the grade it already carries: no formula runs."""
    return CellSheet(scorer_id, tuple(cells))


def score_summary(
    *,
    accuracy: float = 0.5,
    composite_fitness: float | None = None,
    total: int = 4,
    walked: int | None = None,
) -> ScoreSummary:
    return ScoreSummary(
        total=total,
        accuracy=accuracy,
        composite_fitness=accuracy if composite_fitness is None else composite_fitness,
        deprecated=0,
        degraded=0,
        recall_at={},
        walked=total if walked is None else walked,
        cached=0,
        evaluators={},
        mean_fitness_ci_lo=None,
        mean_fitness_ci_hi=None,
        tokens=None,
    )


def arm_state(
    name: str = "c0",
    *,
    slot: ArmSlot | None = None,
    total: int = 4,
    cut: CutKind | None = None,
    invalid_reason: str | None = None,
    locked_in: bool = False,
    elimination: dict[str, Any] | None = None,
    **overrides: Any,
) -> Cut | Measured:
    """A concluded arm whose individual is named by *name*; ``slot=None`` is a reference arm, a
    round-0 slot the origin."""
    failures = (
        [ValidationFailure(axis="prompt_fields", value="", allowed=[], reason=invalid_reason)]
        if invalid_reason
        else []
    )
    role = (
        MeasurementRole.PARENT
        if slot is None
        else MeasurementRole.ORIGIN
        if slot.round == 0
        else MeasurementRole.PANEL
    )
    fields: dict[str, Any] = {
        "role": role,
        "slot": slot,
        "individual": prompted(instruction=name),
        "validation_failures": failures,
        "sp_hash": name,
        "resolved_params": {},
        "expected_samples": total,
    } | overrides
    settled = Elimination(reason="pobb", context=elimination or {})
    if invalid_reason:
        return Cut(reason=Invalid(), **fields)
    match cut:
        case None:
            return Measured(locked_in=settled if locked_in else None, **fields)
        case "eliminated":
            return Cut(reason=Eliminated(elimination=settled), **fields)
        case "skipped":
            return Cut(reason=Skipped(), **fields)
        case "broken":
            failure = RuntimeFailure(
                source="degradation_check",
                dominant_warning="solve:empty",
                node="solve",
                degraded_rate=1.0,
                degraded_count=total,
                total_scored=total,
                observed_config={},
                first_seen_round=0 if slot is None else slot.round,
                changes_description="",
                owner=NurseOwner.L1,
            )
            return Cut(reason=Broken(failure=failure, degradation={}), **fields)
        case "invalid":
            raise ValueError("an invalid arm is built by its `invalid_reason`")


def spend_book(
    usd_cap: float | None = None,
    *,
    usd_reserve: float | EllipsisType | None = ...,
    meters: CeilingMeter = "bill",
    wall_clock_s: float | None = None,
    **spent: Any,
) -> SpendBook:
    spent.setdefault("opened", time.monotonic())
    return SpendBook(
        declared=Budget(wall_clock_s=wall_clock_s),
        hold=Hold(Budget(usd=usd_cap), Budget(usd=usd_cap if usd_reserve is ... else usd_reserve)),
        meters=meters,
        **spent,
    )


def backend_client(**declared: Any) -> BackendClient:
    declared.setdefault("endpoint", lambda: f"http://{FakeBackend.AT}")
    declared.setdefault("cell_envelope_s", cell_envelope_of(600.0))
    connector = Connector("fake", termnorm_wire_adapter, **declared)
    return BackendClient(connector, workload=PROBE_WORKLOAD)


def workspace(root: Path) -> Stores:
    return build_stores(
        default_identity(), projects_root=root / "projects", install_datasets_root=root / "datasets"
    )


def loop_session(
    stores: Stores,
    schema: PipelineSchema,
    samples: Sequence[Sample],
    *,
    backend_id: str = "",
    client: BackendClient | None = None,
    scorer: Scorer | None = None,
    partition: BankPartition | None = None,
    control: RunControl | None = None,
    hop: CycleHop | None = None,
    ledger: CycleEventLog | None = None,
    dataset: ResolvedDataset | None = None,
    dataset_name: str | None = None,
) -> Session:
    return Session(
        store=stores,
        backend_id=backend_id,
        backend_client=backend_client(
            max_cells_in_flight=1, cancel_stops_billing=True, holds_own_sends=True
        )
        if client is None
        else client,
        pipeline_schema=schema,
        bank=Bank.of(samples),
        dataset_name=schema.name if dataset_name is None else dataset_name,
        dataset=dataset,
        campaign_id="" if hop is None else hop.campaign_id,
        drawn=None if hop is None else partition,
        state=LaunchState(
            cycle_id="" if hop is None else hop.cycle_id,
            ledger=ledger,
            scoring=ScorerSetup(scorer=scorer, partition=partition),
            source=RunSource.OPTIMIZATION_LOOP,
            control=RunControl() if control is None else control,
        ),
    )


PROMPT_INFO = NodePromptInfo(
    fields=(
        "persona",
        "task_intent",
        "problem_description",
        "instruction",
        "thinking_style",
        "answer_format",
    )
)

_SEAT_BANK = (Sample(id=0, query="q0", ground_truth="a"),)
_SEATS = count()


def seat(
    stores: Stores,
    *,
    round_num: int = 1,
    schema: PipelineSchema | None = None,
    bank: Sequence[Sample] = _SEAT_BANK,
    bench: int = 0,
    demo: int = 0,
    scorer: Scorer | None = None,
    optimization: dict[str, Any] | None = None,
    dataset_name: str = "seat",
    backend_id: str = "",
    control: RunControl | None = None,
    hop: CycleHop | None = None,
    origin: Callable[[Session], ReadArm] | None = None,
    **cycle: Any,
) -> RoundContext:
    """Round *round_num* of a cycle started from *origin*'s reading of its session, else of one
    whose origin was never measured; *cycle* bends its fields. Its one ledger is *hop*'s cycle's,
    else a dir of its own."""
    schema = schema or pipeline_schema(
        [PipelineNode(name="solve", tunes_llm=False, prompt_info=PROMPT_INFO)]
    )
    config = CampaignConfig.model_validate(
        {
            "optimization": {"degradation_threshold": 0.0, **(optimization or {})},
            "dataset_split": {"bench": bench, "demo": demo},
        }
    )
    ledger = CycleEventLog.open(
        CycleDir(
            stores.projects_root / "seats" / str(next(_SEATS))
            if hop is None
            else stores.campaigns.cycle_dir(hop)
        )
    )
    session = loop_session(
        stores,
        schema,
        bank,
        backend_id=backend_id,
        scorer=scorer
        or compile_scorer("label_match(predicted, ground_truth)", verifier_graded=False),
        partition=partition_bank(list(bank), config.dataset_split, seed=config.run_seed),
        control=control,
        hop=hop,
        ledger=ledger,
        dataset_name=dataset_name,
    )
    if origin is None:
        seated = Cycle(
            session=session,
            config=config,
            slices=SliceMap(select_optimizer(config.optimization).slices),
            difficulty=DifficultyView.open(session, config, origin="origin"),
            **cycle,
        )
    else:
        seated = Cycle.start(
            origin(session), schema=schema, session=session, config=config, **cycle
        )
    return RoundContext(seated, round_num, callbacks=RunCallbacks(ledger))


def set_line(cycle: Cycle, line: Sequence[RoundResult]) -> None:
    cycle.rounds = list(line)


def minted_campaign(
    hop: CycleHop,
    *,
    config: CampaignConfig | None = None,
    purpose: Purpose = Optimize(),  # noqa: B008 — frozen
    dataset_name: str = "ds1",
    **fields: Any,
) -> Campaign:
    """A manifest minted as the mint seam does: *config* declared, whole, under *purpose*."""
    config = config or CampaignConfig(optimization=OptimizationConfig(degradation_threshold=0.4))
    return Campaign(
        campaign_id=hop.campaign_id,
        dataset_name=dataset_name,
        created_at=fields.pop("created_at", ""),
        root_cycle_id=hop.cycle_id,
        root_content_hash=fields.pop("root_content_hash", "origin"),
        treatment=select_optimizer(config.optimization).treatment(),
        declaration=declare_run(
            config,
            purpose=purpose,
            dataset_hash=fields.pop("dataset_hash", "bank"),
            connector=fields.pop("connector", "termnorm"),
            bench_defaults_pin=bench_defaults_pin(),
        ),
        **fields,
    )


def walking_cycle(stores: Stores) -> tuple[CycleHop, Path]:
    """A minted cycle a producer holds and has declared running: what a loop command lands on."""
    hop = CycleHop(campaign_id="camp-cmd", cycle_id="cycle_cmd00000000")
    stores.campaigns.create_campaign(minted_campaign(hop))
    stores.campaigns.mint_cycle(hop)
    cycle_dir = stores.campaigns.cycle_dir(hop)
    CycleEventLog.open(CycleDir(cycle_dir)).append(RunPhaseRecord(run_phase=RunPhase.RUNNING))
    hold_cycle(cycle_dir)
    return hop, cycle_dir


def arm_walk(
    ledger: CycleEventLog,
    cells: Iterable[tuple[int, WalkedCell]],
    *,
    rnd: int = 0,
    individual: str = "c0",
    role: ArmRole = MeasurementRole.ORIGIN,
    running: ScoreSummary | None = None,
) -> list[SampleScoredRecord]:
    """*individual* announced as an arm of round *rnd*, then each filed cell scored at its slot."""
    announced = Proposed(role=role, slot=ArmSlot(round=rnd, idx=0, total=1), individual=prompted())
    RunCallbacks(ledger).on_arm(rnd, ArmKey(individual), announced)
    rows = [
        SampleScoredRecord(
            round=rnd,
            individual_id=individual,
            role=role,
            position=slot,
            result=GradedCell.from_wire({"sample_key": key, "answer": answer}),
            running=running,
        )
        for slot, (key, answer, _) in cells
    ]
    for row in rows:
        ledger.append(row)
    return rows


def close_round(
    ledger: CycleEventLog,
    line: Sequence[RoundResult],
    *,
    cycle: Cycle,
) -> None:
    """What the runner appends as the last round of *line* runs and closes over *cycle*'s bank:
    entered, each panel arm proposed then concluded, the election, the close."""
    rr = line[-1]
    n = rr.round
    callbacks = RunCallbacks(ledger)
    callbacks.on_round_entered(n)
    for key, arm in rr.panel.items():
        callbacks.on_arm(n, key, Proposed(role=arm.role, slot=arm.slot, individual=arm.individual))
    with at_round(n):
        for key, arm in rr.panel.items():
            callbacks.on_arm(n, key, arm)
    assert rr.election is not None, "a closed round elects"
    callbacks.on_election(n, rr.election)
    callbacks.on_round_close(
        rr,
        line,
        cycle.session.bank.slot_of,
        cycle.difficulty,
        lives=cycle.config.optimization.lives,
    )


SANDBOX_CAMPAIGN = "testds__20260101-000000"


def inner_sandbox(stores: Stores, owner_campaign_id: str, owner_cycle_id: str) -> Path:
    sandbox = inner_sandbox_dir(
        stores.shared_root,
        str(stores.tenant_id),
        CycleHop(campaign_id=owner_campaign_id, cycle_id=owner_cycle_id),
    )
    write_json(
        sandbox_owner_path(sandbox),
        {
            "tenant_id": str(stores.tenant_id),
            "campaign_id": owner_campaign_id,
            "cycle_id": owner_cycle_id,
        },
    )
    inner = CampaignStore(WorkspaceDir(sandbox / "tenant"))
    inner.mint_cycle(CycleHop(campaign_id="innerds__20260101-000000", cycle_id="inner-cycle-0"))
    return sandbox


def inner_cells(*optimizers: str) -> InnerCells:
    """One seat per named manifest, on a dataset of its own, so each cell runs its own optimizer."""
    by_dataset: dict[str, InnerCell] = {}
    specs: dict[str, InnerTaskSpec] = {}
    for name in optimizers:
        selected = resolve_optimizer(name, {}, gate="off")
        by_dataset[f"{name}ds"] = InnerCell({}, None, selected, selected.treatment().digest)
        specs[name] = InnerTaskSpec(
            inner_dataset=f"{name}ds",
            optimizer_treatment=selected.treatment().digest,
            run_seed=0,
            n_samples=1,
            n_samples_bench=1,
            n_rounds=1,
        )
    cells = tuple(Seat(kind="seat", id=name, inner_dataset=f"{name}ds") for name in optimizers)
    return InnerCells(cells=cells, by_dataset=by_dataset, specs=specs)


def pobb_knobs(**bend: Any) -> PoBBKnobs:
    shipped = cast("PoBBKnobs", resolve_optimizer("potter", {}, gate="off").knobs("pobb"))
    return shipped.model_copy(update=bend)


def degradation_health(
    *, samples: int = 24, degraded_rate: float = 0.0, no_result: int = 0
) -> DegradationHealth:
    return DegradationHealth(
        grade="healthy" if degraded_rate == 0.0 and not no_result else "degraded",
        samples=samples,
        structural_count=0,
        transient_count=0,
        degraded_rate=degraded_rate,
        consecutive_degraded_rounds=0,
        prior_clean_rounds=0,
    )


def optimizer_state(*, parse_failure: str | None = None) -> OptimizerState:
    held = (
        L2Memory(),
        L3Memory(),
        Ladder(),
        BlockPool(entries=()),
        DemoPool(keys=()),
        L1Readout(l1_parse_failure=parse_failure),
        Critique(),
    )
    return OptimizerState(
        manifest=POTTER_MANIFEST, prompt_hashes={}, slices={}, outputs={}
    ).holding(*held)


#: The reference arm every built round carries: a held round ends on it.
PARENT = ArmKey("parent")


def election(
    selected: Sequence[str] = (), *, ended_on: str | None = None, **overrides: Any
) -> Election:
    picks = [ArmKey(k) for k in selected]
    fields: dict[str, Any] = {
        "selected": picks,
        "leading": picks[0] if picks else None,
        "ended_on": ArmKey(ended_on) if ended_on else picks[0] if picks else PARENT,
        "electable": len(picks),
        "elects_on": "ability",
        "ruler": RulerStamp(
            id=flat_ruler_id("acc"),
            version=0,
            scope=RulerScope(scorer="acc", dataset="ds"),
            excluded=(),
        ),
    } | overrides
    return Election(**fields)


def round_result(
    rnd: int,
    *,
    improved: bool = True,
    degraded_rate: float = 0.0,
    no_result: int = 0,
    samples: int = 24,
    candidates_scored: int = 2,
    parse_failure: str | None = None,
    no_op: int = 0,
    dup: int = 0,
    collapsed: int = 0,
    cut: int = 0,
    selected: Sequence[str] | None = None,
    arms: Mapping[ArmKey, ArmState] | None = None,
    voted: dict[str, Any] | None = None,
    **overrides: Any,
) -> RoundResult:
    """*improved* selects the first measured arm unless *selected* names the keys; *voted* bends
    the election's other fields."""
    if parse_failure:
        candidates_scored = 0
    panel = candidates_scored + no_op + dup
    reasons = [
        r for r, n in (("no_op_variant", no_op), ("duplicate_variant", dup)) for _ in range(n)
    ]
    built: dict[ArmKey, ArmState] = {
        **{
            ArmKey(f"c{i}"): arm_state(
                f"c{i}",
                slot=ArmSlot(round=rnd, idx=i, total=panel),
                total=2 if i < cut else len(_TRUTH),
            )
            for i in range(candidates_scored)
        },
        **{
            ArmKey(f"x{i}"): arm_state(
                f"x{i}",
                slot=ArmSlot(round=rnd, idx=candidates_scored + i, total=panel),
                invalid_reason=reason,
            )
            for i, reason in enumerate(reasons)
        },
    }
    arms = (built | {PARENT: arm_state("parent")}) if arms is None else arms
    if selected is None:
        measured = [k for k, a in arms.items() if isinstance(a, Measured) and a.slot is not None]
        selected = measured[:1] if improved else []
    vote = election(selected, **{"electable": candidates_scored} | (voted or {}))
    # Merged rather than splatted after the literals, so ``overrides`` reaches EVERY field.
    base: dict[str, Any] = {
        "round": rnd,
        "arms": arms,
        "folds": {
            key: score_summary(total=arm.expected_samples)
            for key, arm in arms.items()
            if isinstance(arm, Cut | Measured) and arm_end(arm) != "invalid"
        },
        "election": vote,
        # No pair read: a test that bends the advance passes the reading that derives it.
        "overlap": OverlapReading.unpaired(
            ReadingState.NOT_HELD if rnd else ReadingState.SAME_INDIVIDUAL,
            rnd,
            rnd > 0 and bool(vote.selected),
        ),
        "cells": {
            ArmKey(f"c{i}"): sheet(
                measurement(
                    sid,
                    None,
                    predicted="Uncertain" if i < collapsed or i < cut else t,
                    ground_truth=t,
                )
                for sid, t in enumerate(_TRUTH[:2] if i < cut else _TRUTH)
            )
            for i in range(candidates_scored)
        },
        "health": degradation_health(
            samples=samples, degraded_rate=degraded_rate, no_result=no_result
        ),
        "optimizer_state": optimizer_state(parse_failure=parse_failure),
    }
    return RoundResult(**(base | overrides))


def level_series(
    origin: float | None,
    levels: Sequence[float | None],
    *,
    origin_se: float | None = None,
    ses: Sequence[float | None] | None = None,
    cold: bool = False,
    caveat: ThetaCaveat | None = None,
) -> LevelSeries:
    """Round 0 the origin, then one round per level; ``None`` is a round with no level on the scale.

    ``cold`` and ``caveat`` are set apart: `theta_caveat` ranks other states above a cold scale."""
    stamp = RulerStamp(
        id=flat_ruler_id("acc") if cold else "warm",
        version=1,
        scope=RulerScope(scorer="acc", dataset="ds"),
        excluded=(),
    )

    def read(theta: float | None, se: float | None) -> AbilityReading | None:
        if theta is None:
            return None
        return AbilityReading(
            theta=theta,
            se=se,
            ruler=stamp,
            cells=1,
            ruler_span=None,
            round_span=None,
            calibration_model=None if cold else "1PL",
            caveat=None,
            unlinked=0,
            pinned_share=None,
        )

    per_round = [None] * len(levels) if ses is None else ses
    return LevelSeries(
        stamp=stamp,
        levels={0: read(origin, origin_se)}
        | {
            rnd: read(theta, se)
            for rnd, (theta, se) in enumerate(zip(levels, per_round, strict=True), start=1)
        },
        caveat=caveat,
    )


def cycle_result(
    levels: list[float | None],
    origin: float | None,
    rounds: list[RoundResult],
    *,
    cost: float = 0.03,
    stop_reason: StopReason = StopReason.ROUNDS_BUDGET,
    unpriced_tokens: int = 0,
    billed: float | None = None,
    bench_origin: float | None = 0.5,
    bench_levels: list[float | None] | None = None,
    origin_se: float | None = None,
    ses: list[float | None] | None = None,
    cold: bool = False,
    caveat: ThetaCaveat | None = None,
    **overrides: Any,
) -> CycleResult:
    """L1 rounds only in `rounds`/`levels`; `cost` is INCURRED, `billed` diverges under replay.

    The held-out series defaults FLAT at the bench origin, one entry per level, so a test bending
    only the θ side still scores; `bench_origin=None` is an origin pass that read nothing.
    """
    if bench_levels is None:
        bench_levels = [bench_origin if bench_origin is not None else 0.5] * len(levels)
    unheld = PairedReading.unread(ReadingState.NOT_HELD)
    bench = BenchScore.of(
        bench_size=0,
        scorer_id="",
        headline=BENCH_HEADLINE,
        status=bench_status(
            trigger="at_end",
            bench_size=0,
            tolerance=0,
            on_line=True,
            held_by=None,
            origin=None,
            selected=None,
            run=LineRun(selecting=False, ending=stop_reason, selection=None, rounds_closed=0),
        ),
        origin=None,
        selected=None,
        vs_origin=unheld,
        cost=LiftCost.of(unheld, None),
        scopes=[],
    )
    return CycleResult(
        rounds=rounds,
        n_rounds_after_origin=len(rounds),
        result_accuracy=0.5,
        result_round=len(rounds),
        origin=None
        if origin is None
        else OwnLevel(
            accuracy=BandedValue(value=origin, ci_lo=None, ci_hi=None), composite=None, n=1
        ),
        bench=bench,
        level_series=level_series(
            origin, levels, origin_se=origin_se, ses=ses, cold=cold, caveat=caveat
        ),
        bench_origin_level=bench_origin,
        bench_round_levels=bench_levels,
        result_prompt_fields={},
        stop_reason=stop_reason,
        started_at="2026-01-01T00:00:00Z",
        finished_at="2026-01-01T01:00:00Z",
        spend=SpendRollup(
            total_used_usd=cost if billed is None else billed,
            total_incurred_usd=cost,
            by_kind={"optimizer": SpendBucket(incurred_unpriced_tokens=unpriced_tokens)},
        ),
        **overrides,
    )
