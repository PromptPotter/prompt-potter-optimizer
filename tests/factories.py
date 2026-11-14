from __future__ import annotations

import asyncio
import functools
import selectors
import shutil
import time
from collections.abc import Callable, Coroutine, Iterable, Mapping, Sequence
from dataclasses import replace
from itertools import count
from pathlib import Path
from types import EllipsisType
from typing import Any, cast

from offline_world import FakeBackend, Router

from promptpotter.application.bench.cycle import Cycle, ReadArm, RoundContext, SliceMap
from promptpotter.application.bench.difficulty import DifficultyView
from promptpotter.application.bench.node_context import closed_round
from promptpotter.application.datasets.authored import (
    ConfigLayers,
    dataset_campaign_block,
    declare_run,
)
from promptpotter.application.datasets.loaders import definition_source
from promptpotter.application.embedded_run import enter
from promptpotter.application.initialization.scoring_scope import session_scope
from promptpotter.application.initialization.session import LaunchState, ScorerSetup, Session
from promptpotter.application.initialization.wiring import init_services
from promptpotter.application.intelligence.earned_blocks import CampaignSurvey
from promptpotter.application.jobs.launcher.admission import (
    admit_slotless,
    land_job,
    slotless_share,
)
from promptpotter.application.jobs.mint import declare, mint_framed_cycle
from promptpotter.application.jobs.quota import sends_on
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.application.knobs import bench_defaults_pin
from promptpotter.application.optimizer_manifest import SelectedOptimizer, select_optimizer
from promptpotter.application.optimizers.potter.knobs import ElectionKnobs, PoBBKnobs
from promptpotter.application.optimizers.potter.records import (
    L1Readout,
    L2Memory,
    L3Memory,
    Ladder,
)
from promptpotter.application.pipeline_resolve import laid_session
from promptpotter.application.run_observers import build_run_observers
from promptpotter.application.run_phase_control import RunControl
from promptpotter.application.runner.bench import BenchLine
from promptpotter.application.runner.inner import spawn
from promptpotter.application.runner.inner.tasks import cell_optimizer
from promptpotter.application.runner.loop import walk_round_zero
from promptpotter.application.runner.mint import (
    close_init,
    open_cycle,
    open_launch,
    start_launch,
)
from promptpotter.application.scoring.formula.compiler import compile_scorer
from promptpotter.application.scoring.paired import MemberRows
from promptpotter.config.paths import install_datasets_root
from promptpotter.config.prompt_blocks import PackShelf
from promptpotter.connectors.protocol import (
    PROBE_WORKLOAD,
    Connector,
    Endpoint,
    cell_envelope_of,
)
from promptpotter.connectors.termnorm import termnorm_wire_adapter
from promptpotter.domain.arms import (
    ArmRole,
    ArmSlot,
    ArmState,
    Broken,
    Cut,
    CutKind,
    Eliminated,
    Elimination,
    Invalid,
    Measured,
    Proposed,
    Refused,
    Skipped,
    arm_end,
)
from promptpotter.domain.bench import (
    BENCH_HEADLINE,
    BankPartition,
    BenchScore,
    LiftCost,
    LineRun,
    OwnLevel,
    bench_status,
    partition_bank,
)
from promptpotter.domain.campaign import Campaign, Optimize, Purpose, run_clamp
from promptpotter.domain.campaign_config import (
    CampaignConfig,
    CycleConfig,
    OptimizerChoice,
)
from promptpotter.domain.cell_fold import ScoreSummary
from promptpotter.domain.cells import PinnedCell, Seat
from promptpotter.domain.crowning_bar import BarRead
from promptpotter.domain.cycle_paths import CycleDir, CycleHop, WorkspaceDir
from promptpotter.domain.cycle_result import CycleResult
from promptpotter.domain.dashboard_rows import RunLimits
from promptpotter.domain.job import EmbeddedRun, InnerRun
from promptpotter.domain.l4.cells import InnerCell, InnerCells, InnerTaskSpec
from promptpotter.domain.opt_search_point import NodePrompt, OptSearchPoint
from promptpotter.domain.optimizer_state import Critique, OptimizerState, StateSlice
from promptpotter.domain.optimizer_template import PROMPT_FIELDS
from promptpotter.domain.origin_panel import OverlapReading
from promptpotter.domain.paired_reading import (
    ArmKey,
    ArmName,
    Estimate,
    MemberAddress,
    PairedReading,
    ReadingState,
    RoundArm,
)
from promptpotter.domain.phases import StopReason
from promptpotter.domain.pipeline_params import PipelineParams
from promptpotter.domain.pipeline_schema import (
    NodePromptInfo,
    PipelineNode,
    PipelineSchema,
)
from promptpotter.domain.pool import BlockPool, DemoPool
from promptpotter.domain.prompt_block import Presumption
from promptpotter.domain.records import CycleRecord
from promptpotter.domain.records.run_records import (
    ArmRecord,
    Bill,
    CallPrice,
    CycleMintedRecord,
    ElectionRecord,
    ForkSpec,
    Hold,
    PipelineRead,
    RoundEnteredRecord,
    Running,
    RunPhaseRecord,
    RunWiringRecord,
    SampleScoredRecord,
    TokenUsageRecord,
    parent_address,
)
from promptpotter.domain.results import Election, RoundResult
from promptpotter.domain.round_health import DegradationHealth
from promptpotter.domain.ruler import (
    AbilityReading,
    DeltaRuler,
    LevelSeries,
    RulerReach,
    RulerScope,
    RulerStamp,
    ThetaCaveat,
    anchor_id_of,
    flat_ruler_id,
)
from promptpotter.domain.run_mode import RunMode
from promptpotter.domain.run_standing import LineOutlay, RunStanding
from promptpotter.domain.sample import ArchiveEntry, Bank, Sample, sample_key
from promptpotter.domain.scoring import CellSheet, GradedCell, MeasuredCell, Scorer, WalkedCell
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.domain.spend import (
    Budget,
    CeilingMeter,
    HeldBudget,
    KeySource,
    SpendBucket,
    SpendRollup,
    TokenUsageKind,
)
from promptpotter.domain.verification import BenchPass, BenchPasses
from promptpotter.domain.wounds import (
    NurseOwner,
    RuntimeFailure,
    ValidationFailure,
    ValidationReason,
)
from promptpotter.infrastructure.backend import BackendClient
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.llm.spend_book import SpendBook, bound_account, draws_on
from promptpotter.infrastructure.producer_lock import hold_cycle
from promptpotter.infrastructure.run_scope import InnerSpawnContext, scoped
from promptpotter.infrastructure.store.campaign_store.store import CampaignStore
from promptpotter.infrastructure.store.dataset_access import (
    DEFINITION_FILE,
    ResolvedDataset,
    campaign_defaults,
    install_dataset,
    resolve_dataset,
)
from promptpotter.infrastructure.store.io import write_json, write_yaml
from promptpotter.infrastructure.store.layout import inner_sandbox_dir, sandbox_owner_path
from promptpotter.infrastructure.store.measurement_archive import MeasurementArchive, config_key
from promptpotter.infrastructure.store.read_model import ledger_line
from promptpotter.infrastructure.store.stores import Stores, build_stores
from promptpotter.shared.identity import IdentityContext, default_identity
from promptpotter.shared.measurement_context import MeasurementRole, RoleScope


def ledger_records(ledger: CycleEventLog | Path) -> list[CycleRecord]:
    """A ledger file's own records, in line order; a fork's prefix is `ledger_chain`'s."""
    path = ledger.path if isinstance(ledger, CycleEventLog) else ledger
    lines = path.read_bytes().splitlines() if path.exists() else []
    return [ledger_line(line) for line in lines if line.strip()]


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
    fitness: float = 0.5,
    composite_fitness: float | None = None,
    total: int = 4,
    walked: int | None = None,
) -> ScoreSummary:
    return ScoreSummary(
        total=total,
        fitness=fitness,
        composite_fitness=fitness if composite_fitness is None else composite_fitness,
        fitness_unit="rate",
        fitness_terms=[],
        deprecated=0,
        degraded=0,
        recall_at={},
        field_accuracy={},
        walked=total if walked is None else walked,
        cached=0,
        evaluators={},
        fitness_band=Estimate(
            value=fitness, ci_lo=None, ci_hi=None, n=total, se=None, unit="rate", form="level"
        ),
        tokens=None,
    )


def arm_state(
    name: str = "c0",
    *,
    slot: ArmSlot | None = None,
    total: int = 4,
    cut: CutKind | None = None,
    invalid_reason: ValidationReason | None = None,
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
        case "refused":
            return Cut(reason=Refused(parameter="reasoning_effort"), **fields)
        case "invalid":
            raise ValueError("an invalid arm is built by its `invalid_reason`")


def spend_book(
    usd_cap: float | None = None,
    *,
    usd_reserve: float | EllipsisType | None = ...,
    meters: CeilingMeter = "bill",
    wall_clock_s: float | None = None,
    metered_on: Path | None = None,
    enclosing: SpendBook | None = None,
    **spent: Any,
) -> SpendBook:
    """A run's book on the bound account, its job's hold the reserve (an enclosed run's draws on
    its *enclosing* book's); *metered_on* meters a cycle's ledger, else what the job's hold drew."""
    spent.setdefault("opened", time.monotonic())
    charges = bound_account("a test's book").charges
    job = f"job-{next(_JOBS)}"
    reserve = Budget(usd=usd_cap if usd_reserve is ... else usd_reserve)
    charges.append(
        Hold(
            hold_id=job,
            parent=None if enclosing is None else draws_on(enclosing),
            key_source="host",
            bound=reserve if enclosing is None else Budget(),
            resource=None,
            node=None,
            ledger=None,
        )
    )
    book = SpendBook(
        declared=Budget(wall_clock_s=wall_clock_s),
        share=Budget(usd=usd_cap),
        charges=charges,
        hold_id=job,
        headroom=Budget(),
        metered_on=metered_on,
        meters=meters,
        **spent,
    )
    book.enclosing = enclosing
    return book


_JOBS = count()


def fake_connector(name: str = "fake", **declared: Any) -> Connector:
    """A `/matches` connector at the offline world's backend, host-paid and priced as TermNorm's."""
    return Connector(
        name,
        termnorm_wire_adapter,
        **{
            "endpoint": Endpoint("FAKE_ENDPOINTS", lambda: {"fake": f"http://{FakeBackend.AT}"}),
            "cell_envelope_s": cell_envelope_of(600.0),
            "cell_payer": "host",
            "sent_spend_bound": None,
            "cells_priced": True,
            **declared,
        },
    )


def backend_client(jobs_dir: Path, **declared: Any) -> BackendClient:
    return BackendClient(
        fake_connector(**declared), endpoint=None, workload=PROBE_WORKLOAD, jobs_dir=jobs_dir
    )


def workspace(root: Path, identity: IdentityContext | None = None) -> Stores:
    return build_stores(
        default_identity() if identity is None else identity,
        projects_root=root / "projects",
        install_datasets_root=root / "datasets",
    )


def scorer_of(
    per_sample: str = "label_match(predicted, ground_truth)",
    per_cell: str | None = None,
    *,
    verifier_graded: bool = False,
) -> Scorer:
    return compile_scorer(per_sample, per_cell, verifier_graded=verifier_graded)


type Configs = list[tuple[str, dict[str, Any]]]
LLM_ONLY: Configs = [("llm_only", {"model": "X"})]


def filed_under(
    configs: Configs = LLM_ONLY, dataset_hash: str = "ds", *, prompt_fields_id: str = "pf"
) -> ArchiveEntry:
    return ArchiveEntry(
        config_key=config_key(configs),
        prompt_fields_id=prompt_fields_id,
        node_configs=configs,
        pipeline_params=PipelineParams(),
        dataset_hash=dataset_hash,
    )


def archive_row(query: str, *, truth: str = "g", **wire: Any) -> MeasuredCell:
    """An answer to *query*, keyed on its content as a bank keys it; *wire* bends any fact."""
    key = sample_key(query=query, ground_truth=truth, question=None, source_pin=None)
    return MeasuredCell.from_wire(
        {"sample_key": key, "query": query, "ground_truth": truth, **wire}
    )


def filed(
    archive: MeasurementArchive,
    cells: Iterable[MeasuredCell],
    under: ArchiveEntry | None = None,
    *,
    grade: str = "A",
    role: MeasurementRole = MeasurementRole.PANEL,
    created_at: str = "",
) -> list[WalkedCell]:
    """Each cell filed as a fresh answer under *under*, as a walk files what it bought."""
    held = list(cells)
    refs = archive.file_answers(
        filed_under() if under is None else under,
        [(cell, grade) for cell in held],
        role=role,
        created_at=created_at,
    )
    return [(cell.sample_key, ref, False) for cell, ref in zip(held, refs, strict=True)]


def billed_call(
    cost_usd: float | None = None,
    *,
    kind: TokenUsageKind = "optimizer",
    model: str | None = None,
    provider: str | None = None,
    rate_priced_usd: float | None = None,
    key_source: KeySource = "host",
    hold_id: str = "billed",
    replay: bool = False,
    **fields: Any,
) -> TokenUsageRecord:
    """One call billed *cost_usd*, or a *replay* priced at it; *fields* bend the rest of the record."""
    price = CallPrice(
        kind=kind,
        model=model,
        provider=provider,
        cost_usd=cost_usd,
        rate_priced_usd=rate_priced_usd,
    )
    record: dict[str, Any] = {"node": "l1_generate", "input_tokens": 1, "output_tokens": 0} | fields
    tokens = record["input_tokens"] + record["output_tokens"]
    bill = Bill(hold_id=hold_id, key_source=key_source, price=price, tokens=tokens)
    return TokenUsageRecord(money=price if replay else bill, **record)


def loop_session(
    stores: Stores,
    schema: PipelineSchema,
    samples: Sequence[Sample],
    *,
    client: BackendClient | None = None,
    scorer: Scorer | None = None,
    partition: BankPartition | None = None,
    control: RunControl | None = None,
    hop: CycleHop | None = None,
    dataset: ResolvedDataset | None = None,
    dataset_name: str | None = None,
) -> Session:
    session = Session(
        store=stores,
        backend_client=backend_client(
            stores.jobs_dir, max_cells_in_flight=1, cancel_stops_billing=True, holds_own_sends=True
        )
        if client is None
        else client,
        pipeline_schema=schema,
        pipeline_read=PipelineRead(declaration={}, dataset_overlay={}),
        bank=Bank.of(samples),
        dataset_name=(
            dataset_name if dataset_name is not None else "" if dataset is None else dataset.name
        ),
        dataset=dataset,
        campaign_id="" if hop is None else hop.campaign_id,
        cycle_id="" if hop is None else hop.cycle_id,
        drawn=None if hop is None else partition,
        state=LaunchState(control=RunControl() if control is None else control),
    )
    session.state.scoring = ScorerSetup(
        scorer=scorer or scorer_of(),
        display_metric="fitness",
        partition=partition or BankPartition(None, tuple(samples), (), ()),
        degradation_checks=(),
        judges=(),
    )
    return session


PROMPT_INFO = NodePromptInfo(fields=PROMPT_FIELDS, rewrite_slot="instruction")


def samples(n: int, truth: str | None = "a", *, query: str = "q") -> list[Sample]:
    """Ids ``0..n-1``, each asking *query* spelled with its id."""
    return [Sample(id=i, query=f"{query}{i}", ground_truth=truth) for i in range(n)]


_SEAT_BANK = tuple(samples(1))
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
    control: RunControl | None = None,
    hop: CycleHop | None = None,
    origin: tuple[OptSearchPoint, Callable[[Session], CellSheet]] | None = None,
    framing: TaskDecomposition | None = None,
    origin_replicates: int | None = None,
) -> RoundContext:
    """Round *round_num* of a cycle started from *origin*, an individual and its reading of the
    session, else of one whose origin was never measured. It is bound to *hop*, else to a cycle of
    its own."""
    framing = framing or TaskDecomposition()
    schema = schema or pipeline_schema(
        [PipelineNode(name="solve", tunes_llm=False, prompt_info=PROMPT_INFO)]
    )
    config = defaulted_config(
        {
            "optimization": {"degradation_threshold": 0.0, **(optimization or {})},
            "dataset_split": {"bench": bench, "demo": demo},
            "scoring": "label_match(predicted, ground_truth)",
            "origin_replicates": origin_replicates,
        }
    )
    hop = hop or CycleHop(campaign_id="seats", cycle_id=f"seat{next(_SEATS)}")
    CycleEventLog.open(CycleDir(stores.campaigns.cycle_dir(hop)))
    session = loop_session(
        stores,
        schema,
        bank,
        scorer=scorer or scorer_of(),
        partition=partition_bank(list(bank), config.dataset_split, seed=config.run_seed),
        control=control,
        hop=hop,
        dataset_name=dataset_name,
    )
    selected = select_optimizer(config.optimization)
    if origin is None:
        seated = Cycle(
            session=session,
            config=config,
            optimizer=selected,
            slices=SliceMap(selected.slices),
            difficulty=DifficultyView.open(session, config, origin="origin"),
            survey=CampaignSurvey(stores.campaigns),
            framing=framing,
        )
    else:
        individual, read = origin
        sp = session.points(framing).job(individual)
        c0 = arm_state(
            "C0",
            slot=ArmSlot(round=0, idx=0, total=1),
            individual=individual,
            resolved_params=sp.config_params.dump(),
            sp_hash=sp.sp_hash(session.pipeline_schema),
        )
        seated = Cycle.start(
            ReadArm(ArmKey(individual.id), c0, read(session)),
            framing=framing,
            session=session,
            config=config,
            optimizer=selected,
            survey=CampaignSurvey(stores.campaigns),
        )
    return RoundContext(seated, round_num)


LAUNCHED_DATASET = "justlogic/d234"


def launched_bank(stores: Stores, world: Router, bank: Sequence[Sample]) -> None:
    """`LAUNCHED_DATASET` installed in *stores* on *bank*, the offline *world* answering it."""
    world.backend.truth |= {s.query: s.ground_truth for s in bank}
    if world.rates is None:
        world.rates = {}
    # The definition's chain, root-down: the wheel's defaults, the family, the leaf.
    family = LAUNCHED_DATASET.split("/")[0]
    shutil.copytree(
        install_datasets_root() / family, stores.install_datasets_root / family, dirs_exist_ok=True
    )
    # The bank as the cut of the definition's own `source:`: no fetch reaches the hub.
    source = definition_source(resolve_dataset(stores, LAUNCHED_DATASET))
    assert source is not None, f"{LAUNCHED_DATASET} names a source"
    stores.tenant_datasets.record_cut(source.identity, Bank.of(bank))


async def run_init_round(
    stores: Stores,
    world: Router,
    bank: Sequence[Sample],
    *,
    round_num: int = 1,
    optimization: dict[str, Any] | None = None,
    split: dict[str, int] | None = None,
) -> RoundContext:
    """Round *round_num* of a cycle run init opened, as every door opens one: the offline *world*
    answers *bank*'s origin walk and an empty price list, admission holds the budget, the mint
    declares the campaign, unframed. A sync journey runs it on `jumping_clock`."""
    launched_bank(stores, world, bank)
    raw = dict(dataset_campaign_block(install_dataset(install_datasets_root(), LAUNCHED_DATASET)))
    held = raw["optimization"]
    if "optimizer" in (optimization or {}):
        # The template's node overlay is written for the optimizer it selects, and only that one.
        held.pop("nodes", None)
    raw |= {
        "optimization": held | {"budget": {"usd": 1000.0}} | (optimization or {}),
        "dataset_split": split or {"bench": 0, "demo": 0},
        "task_framing": "off",
    }
    entered = await enter(stores)
    async with init_services(
        dataset_name=LAUNCHED_DATASET, identity=stores.identity, stores=entered
    ) as session:
        session, config = laid_session(session, ConfigLayers(campaign_defaults(), raw))
        declaration = declare(session, config, purpose=Optimize())

        async def run(held: HeldBudget) -> tuple[RoundContext, CycleResult]:
            minted = await mint_framed_cycle(
                session,
                declaration,
                session.bank.samples,
                task_text=None,
            )
            running, cycle_config = minted.session, minted.cycle
            await asyncio.to_thread(
                land_job,
                stores,
                JobRegistry.attach(stores.jobs_dir),
                held.job_id,
                running.hop,
                held.share,
            )
            return await launched_round(
                running, cycle_config, minted.optimizer, held, round_num=round_num
            )

        start, held = await asyncio.to_thread(
            admit_slotless,
            config,
            stores=stores,
            holder=EmbeddedRun(session.measured_dataset),
            hop=None,
            requested=Budget(),
            step_rounds=None,
            sends=sends_on(stores, config, declaration.environment.connector),
        )
        try:
            ctx, ended = await run(held)
        except BaseException as exc:
            await asyncio.to_thread(start.release, exc)
            raise
        await asyncio.to_thread(start.release, ended)
        return ctx


async def launched_round(
    running: Session,
    cycle_config: CycleConfig,
    optimizer: SelectedOptimizer,
    held: HeldBudget,
    *,
    round_num: int,
) -> tuple[RoundContext, CycleResult]:
    """Run init on a minted cycle as its door opens it, through round 0's walk."""
    observers = await asyncio.to_thread(
        build_run_observers, session=running, campaign_config=cycle_config.config
    )
    start_launch(running)
    determinism = cycle_config.config.optimization.determinism
    try:
        with (
            scoped(
                round=0,
                ledger=CycleEventLog.open(observers.cycle_dir),
                account=running.store,
                determinism=run_clamp(determinism, running.purpose),
            ),
            session_scope(running),
        ):
            prep = await open_launch(
                list(running.bank.samples),
                cycle_config,
                session=running,
                observers=observers,
                budget=held,
                optimizer=optimizer,
            )
            cycle, _ = await open_cycle(prep, session=running, mode=RunMode())
            close_init(cycle)
            # Round 0's walk, as its door takes it; its close is the round's caller's.
            await walk_round_zero(cycle, prep.dataset)
    finally:
        observers.release()
    ctx = RoundContext(cycle, round_num)
    return ctx, cycle_result([], None, [], cost=0.0)


def cycle_ledger(cycle: Cycle) -> CycleEventLog:
    return CycleEventLog.open(CycleDir(cycle.session.store.campaigns.cycle_dir(cycle.session.hop)))


def set_cycle(
    cycle: Cycle,
    *,
    line: Sequence[RoundResult] | None = None,
    population: Sequence[OptSearchPoint] | None = None,
    slices: Iterable[StateSlice] = (),
    standing: RunStanding | None = None,
) -> None:
    if line is not None:
        cycle.restate_rounds(list(line))
    if standing is not None:
        cycle.standing = standing
    if population is not None:
        cycle.population = list(population)
    for held in slices:
        cycle.slices.put(held)


def defaulted_config(layer: Mapping[str, Any] | None = None) -> CampaignConfig:
    """A config as every door builds one: *layer* laid over `defaults.yaml`."""
    return CampaignConfig.model_validate(
        ConfigLayers(campaign_defaults(), dict(layer or {})).folded().document
    )


def minted_campaign(
    hop: CycleHop,
    *,
    config: CampaignConfig | None = None,
    purpose: Purpose = Optimize(),  # noqa: B008 — frozen
    dataset_name: str = "ds1",
    **fields: Any,
) -> Campaign:
    """A manifest minted as the mint seam does: *config* declared, whole, under *purpose*."""
    config = config or defaulted_config({"optimization": {"degradation_threshold": 0.4}})
    return Campaign(
        campaign_id=hop.campaign_id,
        dataset_name=dataset_name,
        created_at=fields.pop("created_at", ""),
        root_cycle_id=hop.cycle_id,
        root_content_hash=fields.pop("root_content_hash", "origin"),
        treatment=select_optimizer(config.optimization).treatment(
            frozenset(Presumption), PackShelf(account=None)
        ),
        declaration=declare_run(
            config,
            purpose=purpose,
            dataset_hash=fields.pop("dataset_hash", "bank"),
            connector=fields.pop("connector", "termnorm"),
            bench_defaults_pin=bench_defaults_pin(),
        ),
        **fields,
    )


def run_wiring() -> RunWiringRecord:
    """A launch's wiring on one cell at a time, declaring no limit: what a dashboard folds first."""
    return RunWiringRecord(
        round_cells_ceiling=1,
        display_metric="fitness",
        elects_on="fitness",
        max_cells_in_flight=1,
        measured_unit="sample",
        measurement_node="l1_score",
        run_limits=RunLimits(),
        defaults_drift=None,
        backend_revision=None,
    )


def minted_dataset(stores: Stores, campaign: Campaign) -> None:
    """The definition *campaign* was minted from, naming the connector it froze: every run and
    pass on it reads it first (`admission.py::refuse_campaign`)."""
    write_yaml(
        stores.tenant_datasets.dataset_dir(campaign.dataset_name) / DEFINITION_FILE,
        {"pipeline": {"connector": campaign.connector}},
    )


def stored_campaign(stores: Stores, campaign: Campaign) -> None:
    """Stored as the mint stores it: the root's resolved run lands before the manifest."""
    node = {"type": "llm", "config": {"model": "m"}}
    stores.campaigns.write_resolved_run(
        campaign.root_hop,
        PipelineRead(
            declaration={
                "nodes": {"a": node},
                "pipelines": {"default": ["a"]},
            },
            dataset_overlay={},
        ),
        experiment=None,
        model_capabilities={},
    )
    stores.campaigns.create_campaign(campaign)


def walking_cycle(stores: Stores) -> tuple[CycleHop, Path]:
    """A minted cycle a producer holds and has declared running: what a loop command lands on."""
    hop = CycleHop(campaign_id="camp-cmd", cycle_id="cycle_cmd00000000")
    stores.campaigns.create_campaign(minted_campaign(hop))
    stores.campaigns.mint_cycle(hop)
    cycle_dir = stores.campaigns.cycle_dir(hop)
    CycleEventLog.open(CycleDir(cycle_dir)).append(RunPhaseRecord(state=Running()))
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
    ledger.append(ArmRecord(round=rnd, arm=ArmKey(individual), state=announced))
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
    ledger.append(RoundEnteredRecord(round=n))
    for key, arm in rr.panel.items():
        ledger.append(
            ArmRecord(
                round=n,
                arm=key,
                state=Proposed(role=arm.role, slot=arm.slot, individual=arm.individual),
            )
        )
    with scoped(round=n):
        for key, arm in rr.panel.items():
            ledger.append(ArmRecord(round=n, arm=key, state=arm))
    assert rr.election is not None, "a closed round elects"
    ledger.append(ElectionRecord(round=n, **dict(rr.election)))
    ledger.append(closed_round(rr, line, cycle, outlay=LineOutlay(search_usd=None, worked_s=None)))


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
    inner = CampaignStore(WorkspaceDir(sandbox / "tenant"), stores.jobs_dir)
    inner.mint_cycle(CycleHop(campaign_id="innerds__20260101-000000", cycle_id="inner-cycle-0"))
    return sandbox


async def inner_seat(
    stores: Stores,
    world: Router,
    optimizer: str,
    overrides: dict[str, dict[str, Any]],
    *,
    round_num: int = 1,
) -> RoundContext:
    """An L4 cell's inner campaign as the spawn opens it (`spawn.py::_open_seat`): *optimizer*'s
    seat on `LAUNCHED_DATASET`, *overrides* its arm, under an outer cycle's sandbox."""
    launched_bank(stores, world, samples(40, "TRUE"))
    laid = defaulted_config(
        {"optimization": {"optimizer": optimizer, "degradation_threshold": 0.0}}
    )
    seats = inner_cells(
        optimizer,
        dataset=LAUNCHED_DATASET,
        campaign_config=laid.model_dump(mode="json"),
        n_samples=30,
    )
    (cell_seat,) = seats.cells
    owner = CycleHop(campaign_id="outer", cycle_id="cycle-0")
    spawning = InnerSpawnContext(
        inner_sandbox_root=inner_sandbox(stores, owner.campaign_id, owner.cycle_id),
        identity=stores.identity,
        shared_root=stores.shared_root,
        install_datasets_root=stores.install_datasets_root,
        research_datasets_root=None,
        inside=(owner,),
        enclosing=(),
        acting=(),
        cells=seats,
        purpose=Optimize(),
        ruler=RulerReach(version=0, own=None),
    )
    pin = PinnedCell(cell=cell_seat, instrument="i").pin()
    asked = Sample(id=0, query=cell_seat.id, ground_truth=None, source_pin=pin)
    # Under the outer run's book, as a cell's spawn reads it.
    with scoped(account=stores), scoped(spawn=spawning, book=spend_book(1000.0)), scoped(round=0):
        cell = spawn._resolve_inner_cell(asked, {"optimizer_prompt_overrides": overrides})
        async with spawn._open_seat(cell, spawn.claim_sandbox(spawning)) as opened:
            session = opened.session
            ctx, _ = await slotless_share(
                opened.cycle.config,
                stores=session.store,
                holder=InnerRun(LAUNCHED_DATASET),
                hop=session.hop,
                sends=(),
                run=lambda held: launched_round(
                    session, opened.cycle, opened.optimizer, held, round_num=round_num
                ),
            )
            return ctx


def inner_cells(
    *optimizers: str,
    dataset: str | None = None,
    campaign_config: Mapping[str, Any] | None = None,
    n_samples: int = 1,
) -> InnerCells:
    """One seat per named manifest, on a dataset of its own, so each cell runs its own optimizer;
    a lone seat runs on *dataset* where one is named, each cell's dataset under *campaign_config*."""
    assert dataset is None or len(optimizers) == 1, "two seats never share a dataset"
    on = {name: dataset or f"{name}ds" for name in optimizers}
    by_dataset: dict[str, InnerCell] = {}
    specs: dict[str, InnerTaskSpec] = {}
    for name in optimizers:
        selected = select_optimizer(OptimizerChoice(optimizer=name))
        treatment = selected.treatment(frozenset(Presumption), PackShelf(account=None)).digest
        by_dataset[on[name]] = InnerCell(
            campaign_config or {}, None, cell_optimizer(selected), treatment
        )
        specs[name] = InnerTaskSpec(
            inner_dataset=on[name],
            optimizer_treatment=treatment,
            run_seed=0,
            n_samples=n_samples,
            n_samples_bench=1,
            n_rounds=1,
            instrument="e",
        )
    cells = tuple(Seat(kind="seat", id=name, inner_dataset=on[name]) for name in optimizers)
    return InnerCells(cells=cells, by_dataset=by_dataset, specs=specs, estimator="e")


def pobb_knobs(**bend: Any) -> PoBBKnobs:
    shipped = cast("PoBBKnobs", select_optimizer(OptimizerChoice(optimizer="potter")).knobs("pobb"))
    return shipped.model_copy(update=bend)


def election_knobs() -> ElectionKnobs:
    return cast(
        "ElectionKnobs",
        select_optimizer(OptimizerChoice(optimizer="potter")).knobs("theta_election"),
    )


def degradation_health(
    *, samples: int = 24, degraded_rate: float = 0.0, no_result: int = 0
) -> DegradationHealth:
    return DegradationHealth(
        grade="healthy" if degraded_rate == 0.0 and not no_result else "degraded",
        samples=samples,
        structural_count=0,
        hole_count=0,
        no_result_count=no_result,
        not_attempted=0,
        unscored=0,
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
    return OptimizerState(manifest="potter", prompt_hashes={}, slices={}, outputs={}).holding(*held)


#: The reference arm every built round carries: a held round ends on it.
PARENT = ArmKey("parent")


def election(
    selected: str | None = None, *, ended_on: str | None = None, **overrides: Any
) -> Election:
    pick = None if selected is None else ArmKey(selected)
    fields: dict[str, Any] = {
        "selected": pick,
        "leading": pick,
        "ended_on": ArmKey(ended_on) if ended_on else pick or PARENT,
        "electable": int(pick is not None),
        "elects_on": "ability",
        "bar": None if pick is None else BarRead(statistic="posterior", clears=True),
        "ruler": RulerStamp(
            id=flat_ruler_id("acc"),
            version=0,
            own=None,
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
    selected: str | None = None,
    arms: Mapping[ArmKey, ArmState] | None = None,
    voted: dict[str, Any] | None = None,
    **overrides: Any,
) -> RoundResult:
    """*improved* selects the first measured arm unless *selected* names the pick; *voted* bends
    the election's other fields."""
    if parse_failure:
        candidates_scored = 0
    panel = candidates_scored + no_op + dup
    reasons = [
        r
        for r, n in (
            (ValidationReason.NO_OP_VARIANT, no_op),
            (ValidationReason.DUPLICATE_VARIANT, dup),
        )
        for _ in range(n)
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
    if selected is None and improved:
        measured = (k for k, a in arms.items() if isinstance(a, Measured) and a.slot is not None)
        selected = next(measured, None)
    vote = election(selected, **{"electable": candidates_scored} | (voted or {}))
    # Merged rather than splatted after the literals, so ``overrides`` reaches EVERY field.
    base: dict[str, Any] = {
        "round": rnd,
        "arms": arms,
        "reference": RoundArm(round=rnd - 1, arm=PARENT) if rnd else None,
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
            rnd > 0 and vote.selected is not None,
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


def member_rows(
    individual: str,
    rows: Sequence[GradedCell],
    bank: Sequence[Sample],
    *,
    scorer_id: str = "s",
    address: MemberAddress | None = None,
    scope: RoleScope = RoleScope.DECISION,
    instrument_id: str = "i",
    dataset_hash: str | None = "d",
    cell_set_id: str | None = None,
) -> MemberRows:
    """*rows* keyed by slot, each re-keyed onto its *bank* sample."""
    key_of = {s.id: s.key for s in bank}
    return MemberRows(
        address=MemberAddress(
            path=(CycleHop(campaign_id="camp", cycle_id="cyc"),),
            individual_id=individual,
            arm=None,
            pass_role=None,
        )
        if address is None
        else address,
        sheet=sheet(
            (row.filed(replace(row.facts, sample_key=key_of[int(row.key)])) for row in rows),
            scorer_id,
        ),
        bought=0,
        cut=False,
        scope=scope,
        instrument_id=instrument_id,
        dataset_hash=dataset_hash,
        cell_set_id=cell_set_id,
    )


def delta_ruler(
    delta: Mapping[int, float],
    *,
    mu: float = 0.0,
    sigma: float = 2.0,
    se: float | Mapping[int, float] = 0.5,
    sigma_theta: float = 1.5,
) -> DeltaRuler:
    """Keyed by slot here, by the slot's `measurement` key on the ruler."""
    keyed = {str(sid): d for sid, d in delta.items()}
    return DeltaRuler(
        delta=keyed,
        delta_se=(
            {str(sid): s for sid, s in se.items()}
            if isinstance(se, Mapping)
            else dict.fromkeys(keyed, se)
        ),
        mu_delta=mu,
        sigma_delta=sigma,
        sigma_theta=sigma_theta,
        calibration_model="1PL",
        anchor_id=anchor_id_of(keyed, mu, sigma, "1PL"),
    )


def bench_pass(
    stores: Stores,
    individual: str,
    rows: Sequence[GradedCell],
    sample_keys: Sequence[str],
    *,
    filed_as: str | None = None,
    **bend: Any,
) -> BenchPass:
    """*individual*'s held-out pass, *rows* filed as its bench cells; *filed_as* parts two
    campaigns that send one individual."""
    round_num = int(individual != "origin")
    fields: dict[str, Any] = {
        "arm": ArmName(key=ArmKey(individual), round=round_num, label=f"C{round_num}"),
        "sp_hash": individual,
        "stop": None,
        "scorer_id": "",
        "reads_before": 0,
    } | bend
    under = filed_under(
        [("", {"individual": filed_as or f"bench_{individual}"})], prompt_fields_id=individual
    )
    return BenchPass(
        cells=filed(stores.archive, [row.facts for row in rows], under, role=MeasurementRole.BENCH),
        sample_keys=list(sample_keys),
        **fields,
    )


def bench_passes(origin: BenchPass, *held: BenchPass, tolerance: int = 0) -> BenchPasses:
    return BenchPasses(
        tolerance=tolerance,
        origin=origin,
        reserve_usd=0.0,
        reserve_tokens=0,
        selections={taken.arm.key: taken for taken in held},
    )


def bench_line(
    selection: ArmName,
    *,
    selecting: bool = False,
    ending: StopReason | None = StopReason.ROUNDS_BUDGET,
    **line: Any,
) -> BenchLine:
    """A line that closed one round on *selection*; *line* bends any other field."""
    fields: dict[str, Any] = {
        "hop": CycleHop(campaign_id="camp", cycle_id="cyc"),
        "on_line": True,
        "inner": False,
        "refused": None,
        "held_by": None,
        "trigger": "at_end",
        "held_out": 4,
        "instrument_id": "i",
        "spend": None,
    } | line
    return BenchLine(
        run=LineRun(
            selecting=selecting,
            pass_open=False,
            ending=ending,
            selection=selection,
            rounds_closed=1,
        ),
        **fields,
    )


def level_series(
    origin: float | None,
    levels: Sequence[float | None],
    *,
    origin_se: float = 0.2,
    ses: Sequence[float] | None = None,
    cold: bool = False,
    caveat: ThetaCaveat | None = None,
) -> LevelSeries:
    """Round 0 the origin, then one round per level; ``None`` is a round with no level on the scale.

    ``cold`` and ``caveat`` are set apart: `theta_caveat` ranks other states above a cold scale."""
    stamp = RulerStamp(
        id=flat_ruler_id("acc") if cold else "warm",
        version=1,
        own=None,
        scope=RulerScope(scorer="acc", dataset="ds"),
        excluded=(),
    )

    def read(theta: float | None, se: float) -> AbilityReading | None:
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

    per_round = [origin_se] * len(levels) if ses is None else ses
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
    origin_se: float = 0.2,
    ses: list[float] | None = None,
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
    bench = BenchScore(
        bench_size=0,
        scorer_id="",
        headline=BENCH_HEADLINE,
        status=bench_status(
            trigger="at_end",
            bench_size=0,
            on_line=True,
            inner=False,
            refused=None,
            held_by=None,
            origin=None,
            selected=None,
            run=LineRun(
                selecting=False,
                pass_open=False,
                ending=stop_reason,
                selection=None,
                rounds_closed=0,
            ),
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
        result_fitness=0.5,
        fitness_unit="rate",
        result_round=len(rounds),
        origin=None
        if origin is None
        else OwnLevel(
            fitness=Estimate(
                value=origin, ci_lo=None, ci_hi=None, n=1, se=None, unit="rate", form="level"
            ),
            composite=None,
            n=1,
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


def mint_fork(
    campaigns: CampaignStore,
    child: CycleHop,
    parent_dir: Path,
    spec: ForkSpec,
    *,
    cut: int,
    cut_round: int,
) -> None:
    """A fork's one mint record, as `fork_siblings.py::cut_fork` lays it, with no seed."""
    campaigns.mint_cycle(
        child,
        CycleMintedRecord(
            parent=parent_address(campaigns.cycle_dir(child), parent_dir),
            forked_at_offset=cut,
            cut_round=cut_round,
            fork=spec,
        ),
    )
