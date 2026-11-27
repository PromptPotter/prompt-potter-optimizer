from __future__ import annotations

import asyncio
import functools
import selectors
import shutil
import time
from collections.abc import AsyncIterator, Callable, Coroutine, Iterable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import replace
from itertools import count
from pathlib import Path
from statistics import fmean
from types import EllipsisType
from typing import Any, cast

from offline_run import ByCallIdentity, cited_panel, text_templates
from offline_world import FakeBackend, Router

from promptpotter.application.bench.cycle import Cycle, RoundContext
from promptpotter.application.datasets.authored import (
    ConfigLayers,
    dataset_campaign_block,
)
from promptpotter.application.datasets.loaders import definition_source
from promptpotter.application.embedded_run import enter
from promptpotter.application.initialization.session import Session
from promptpotter.application.jobs.launcher.launch import admit
from promptpotter.application.jobs.launcher.mint_and_start import FreshMint, Hosted
from promptpotter.application.jobs.launcher.opening import claim_cycle
from promptpotter.application.jobs.launcher.run_job import HeldRun
from promptpotter.application.jobs.mint import run_spec
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.application.optimizer_manifest import (
    bench_choice,
    select_optimizer,
)
from promptpotter.application.optimizers.potter.knobs import ElectionKnobs, PoBBKnobs
from promptpotter.application.optimizers.potter.records import (
    L1Readout,
    L2Memory,
    L3Memory,
    Ladder,
)
from promptpotter.application.runner.bench import BenchLine
from promptpotter.application.runner.entry import launch_scope, open_line
from promptpotter.application.runner.inner import spawn
from promptpotter.application.runner.inner.tasks import cell_optimizer
from promptpotter.application.runner.loop import open_origin_round
from promptpotter.application.runner.run_init import enter_cycle, open_launch
from promptpotter.application.scoring.formula.compiler import compile_scorer
from promptpotter.application.scoring.paired import MemberRows, grade_measurands, mean_lift
from promptpotter.config.paths import (
    default_projects_root,
    install_datasets_root,
    research_datasets_root,
    user_data_root,
)
from promptpotter.connectors.protocol import (
    PROBE_WORKLOAD,
    Connector,
    Endpoint,
    cell_envelope_of,
)
from promptpotter.connectors.termnorm import CONNECTOR as TERMNORM
from promptpotter.connectors.termnorm import termnorm_wire_adapter
from promptpotter.domain.arms import (
    ArmPosition,
    ArmRole,
    ArmState,
    Broken,
    Cut,
    CutKind,
    Eliminated,
    Elimination,
    Measured,
    Proposed,
    Refused,
    Skipped,
    arm_end,
    degradation_reading,
)
from promptpotter.domain.bench import (
    BENCH_HEADLINE,
    BenchCurve,
    BenchScore,
    LiftCost,
    LiveCycleRun,
    OwnLevel,
    bench_status,
)
from promptpotter.domain.campaign import Campaign, Optimize, Purpose, RunDeclaration
from promptpotter.domain.campaign_config import CampaignConfig
from promptpotter.domain.cell_fold import ScoreSummary
from promptpotter.domain.cells import PinnedCell, Seat
from promptpotter.domain.cycle_paths import CycleDir, CycleHop
from promptpotter.domain.cycle_result import CycleResult, WallClock
from promptpotter.domain.fitness_fold import FoldBasis
from promptpotter.domain.job import CampaignLaunch, LaunchMove
from promptpotter.domain.l4.cells import InnerCell, InnerCells
from promptpotter.domain.opt_search_point import NodePrompt, OptSearchPoint
from promptpotter.domain.optimizer_state import Critique, OptimizerState, StateSlice
from promptpotter.domain.optimizer_template import PROMPT_FIELDS
from promptpotter.domain.origin_panel import OverlapReading, RoundEnding
from promptpotter.domain.paired_reading import (
    ArmKey,
    ArmName,
    Estimate,
    MemberAddress,
    ReadingState,
    RoundArm,
    UnreadPair,
)
from promptpotter.domain.phases import StopReason
from promptpotter.domain.pipeline_params import PipelineParams
from promptpotter.domain.pipeline_schema import (
    NodePromptInfo,
    PipelineNode,
    PipelineSchema,
)
from promptpotter.domain.pool import BlockPool, DemoPool
from promptpotter.domain.records import CycleRecord
from promptpotter.domain.records.fold_basis import FoldBasisRecord
from promptpotter.domain.records.run_records import (
    ArmRecord,
    Bill,
    CallPrice,
    CycleMintedRecord,
    ForkSpec,
    Hold,
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
    RulerScope,
    RulerStamp,
    ThetaCaveat,
    anchor_id_of,
    flat_ruler_id,
)
from promptpotter.domain.run_mode import RunMode
from promptpotter.domain.run_standing import RunStanding
from promptpotter.domain.sample import ArchiveEntry, Bank, Grading, Sample, sample_key
from promptpotter.domain.scoring import (
    CellSheet,
    GradedCell,
    MeasuredCell,
    Scorer,
    WalkedCell,
)
from promptpotter.domain.spend import (
    Budget,
    CeilingMeter,
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
    FAMILY_FILE,
    campaign_defaults,
    resolve_dataset,
)
from promptpotter.infrastructure.store.io import read_yaml, write_json, write_yaml
from promptpotter.infrastructure.store.layout import inner_sandbox_dir, sandbox_owner_path
from promptpotter.infrastructure.store.measurement_archive import MeasurementArchive, config_key
from promptpotter.infrastructure.store.read_model import ledger_line
from promptpotter.infrastructure.store.stores import Stores, build_stores
from promptpotter.shared.identity import IdentityContext, default_identity
from promptpotter.shared.measurement_context import MeasurementRole, RoleScope

# The call target a campaign editing no optimizer runs on, for a choice laying an overlay.
BENCH_TARGET = bench_choice("potter").call_target


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
    """Each cell under the grade it already carries, by a scorer known by its id alone, which
    declares what every scorer does (`hit`); a fact holding its scorer reads `scorer.graded`."""
    return CellSheet(scorer_id, frozenset({"hit"}), tuple(cells))


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
    position: ArmPosition | None = None,
    total: int = 4,
    cut: CutKind | None = None,
    invalid_reason: ValidationReason | None = None,
    locked_in: bool = False,
    elimination: dict[str, Any] | None = None,
    filed: ArchiveEntry | None = None,
    **overrides: Any,
) -> Cut | Measured:
    """A concluded arm whose individual is named by *name*; ``position=None`` is a reference arm, a
    round-0 position the origin. *filed* is the entry its cells are filed under, else *name* ids it."""
    failures = (
        [ValidationFailure(axis="prompt_fields", value="", allowed=[], reason=invalid_reason)]
        if invalid_reason
        else []
    )
    role = (
        MeasurementRole.PARENT
        if position is None
        else MeasurementRole.ORIGIN
        if position.round == 0
        else MeasurementRole.PANEL
    )
    fields: dict[str, Any] = {
        "role": role,
        "position": position,
        "individual": prompted(instruction=name),
        "validation_failures": failures,
        "sp_hash": name if filed is None else filed.prompt_fields_id,
        "config_key": name if filed is None else filed.config_key,
        "resolved_params": {},
        "expected_samples": total,
    } | overrides
    settled = Elimination(reason="pobb", context=elimination or {})
    if invalid_reason:
        return Cut(reason=None, **fields)
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
                first_seen_round=0 if position is None else position.round,
                changes_description="",
                owner=NurseOwner.L1,
            )
            degradation = degradation_reading(
                source=failure.source,
                degraded_count=total,
                total_scored=total,
                warning_types={failure.dominant_warning: total},
                dominant_warning=failure.dominant_warning,
            )
            return Cut(reason=Broken(failure=failure, degradation=degradation), **fields)
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


def workspace(identity: IdentityContext | None = None) -> Stores:
    """The workspace at this fact's data root (`conftest.py::own_home`); its install tier beside it,
    as a wheel's sits outside the data root."""
    return build_stores(
        default_identity() if identity is None else identity,
        projects_root=default_projects_root(),
        install_datasets_root=user_data_root().parent / "datasets",
    )


def scorer_of(
    per_sample: str = "label_match(predicted, ground_truth)",
    per_cell: str | None = None,
    *,
    verifier_graded: bool = False,
    two_valued_terms: frozenset[str] = frozenset(),
) -> Scorer:
    return compile_scorer(
        per_sample, per_cell, verifier_graded=verifier_graded, two_valued_terms=two_valued_terms
    )


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


LABEL_MATCH = Grading(scoring="label_match(predicted, ground_truth)", judges={})


def filed(
    archive: MeasurementArchive,
    cells: Iterable[MeasuredCell],
    under: ArchiveEntry | None = None,
    *,
    grade: str = "A",
    role: MeasurementRole = MeasurementRole.PANEL,
    created_at: str = "",
    output_keys: Mapping[str, tuple[str, ...]] | None = None,
    grading: Grading = LABEL_MATCH,
) -> list[WalkedCell]:
    """Each cell filed as a fresh answer under *under*, as a walk files what it bought."""
    held = list(cells)
    refs = archive.file_answers(
        filed_under() if under is None else under,
        [(cell, grade) for cell in held],
        role=role,
        created_at=created_at,
        output_keys={} if output_keys is None else output_keys,
        grading=grading,
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


PROMPT_INFO = NodePromptInfo(fields=PROMPT_FIELDS, rewrite_field="instruction")


def samples(n: int, truth: str | None = "a", *, query: str = "q") -> list[Sample]:
    """Ids ``0..n-1``, each asking *query* spelled with its id."""
    return [Sample(id=i, query=f"{query}{i}", ground_truth=truth) for i in range(n)]


LAUNCHED_DATASET = "justlogic/d234"


def launched_bank(stores: Stores, world: Router, bank: Sequence[Sample]) -> None:
    """`LAUNCHED_DATASET` installed in *stores* on *bank*, the offline *world* answering it."""
    world.backend.truth |= {s.query: s.ground_truth for s in bank}
    # The definition's chain, root-down: the wheel's defaults, the family, the leaf.
    family = LAUNCHED_DATASET.split("/")[0]
    shutil.copytree(
        install_datasets_root() / family, stores.install_datasets_root / family, dirs_exist_ok=True
    )
    # The family walks the graph the world serves (`FakeBackend.nodes`); a node it never declared
    # runs as `llm_only` does, on its connector's route and the family's task model.
    family_file = stores.install_datasets_root / family / FAMILY_FILE
    held = read_yaml(family_file)
    declared = held["pipeline"]["nodes"]
    route = TERMNORM.node_templates["llm_only"]["config"]
    owned = {"config": {**route, "model": declared["llm_only"]["config"]["model"]}}
    held["pipeline"]["nodes"] = {n: declared.get(n, owned) for n in world.backend.nodes}
    write_yaml(family_file, held)
    # The bank as the cut of the definition's own `source:`: no fetch reaches the hub.
    source = definition_source(resolve_dataset(stores, LAUNCHED_DATASET))
    assert source is not None, f"{LAUNCHED_DATASET} names a source"
    stores.tenant_datasets.record_cut(source.identity, Bank.of(bank))


L4_PANEL = "promptpotter-self/mixed"
# Each seat's inner optimizer, narrowed to the fewest cells its run init and two rounds walk.
_SEAT_NODES: dict[str, dict[str, Any]] = {
    "potter": {
        "adaptive_queue": {"config": {"sp_budget_round": 2}},
        "l1_generate": {"config": {"n_variants": 1, "k_max": 0}},
    },
    "capo": {
        "blocks": {"config": {"block_size": 2, "max_blocks": 2}},
        "survival": {"config": {"size": 2}},
        "capo_init": {"config": {"size": 2, "k_max": 0}},
    },
}


def l4_panel(stores: Stores, world: Router, scratch: Path) -> None:
    """`L4_PANEL` installed in *stores*: `promptpotter-self/e2e`'s outer over one potter and one
    CAPO seat, each on a copy of `LAUNCHED_DATASET`'s family selecting its optimizer, the offline
    *world* answering both optimizers' calls (`offline_run.py::ByCallIdentity`)."""
    root = stores.install_datasets_root
    family, leaf = LAUNCHED_DATASET.split("/")
    seats = {name: f"{family}-{name}/{leaf}" for name in _SEAT_NODES}
    templates: dict[str, str] = {}
    for name, nodes in _SEAT_NODES.items():
        seat_family = root / seats[name].split("/")[0]
        shutil.copytree(install_datasets_root() / family, seat_family, dirs_exist_ok=True)
        held = read_yaml(seat_family / FAMILY_FILE)
        held["campaign"]["optimization"] = {"optimizer": name, "nodes": nodes}
        write_yaml(seat_family / FAMILY_FILE, held)
        block = dataset_campaign_block(resolve_dataset(stores, seats[name]))
        templates |= text_templates(CampaignConfig.model_validate(block))
    tier = research_datasets_root()
    assert tier is not None, "a checkout holds the research tier"
    research, outer = tier / "promptpotter-self", root / "promptpotter-self"
    (outer / "mixed").mkdir(parents=True, exist_ok=True)
    shutil.copy(research / "task.md", outer / "task.md")
    # The panel's `inner_nodes` lay potter's over the potter seat alone: no copy narrows it.
    for layer in (FAMILY_FILE, f"e2e/{DEFINITION_FILE}"):
        held = read_yaml(research / layer)
        if layer != FAMILY_FILE:
            held["panel"]["tasks"] = [
                {"kind": "seat", "id": name, "inner_dataset": dataset}
                for name, dataset in seats.items()
            ]
        write_yaml(outer / layer.replace("e2e/", "mixed/"), held)
    config = CampaignConfig.model_validate(
        dataset_campaign_block(resolve_dataset(stores, L4_PANEL))
    )
    scratch.mkdir(parents=True, exist_ok=True)
    world.llm.says = ByCallIdentity(scratch, templates, cites=cited_panel(config))


def declaration_of(session: Session, config: CampaignConfig, purpose: Purpose) -> RunDeclaration:
    """*config* declared under *purpose*, as a mint numbers an arm."""
    return RunDeclaration(spec=run_spec(session, config, purpose), purpose=purpose)


@asynccontextmanager
async def opened_round(
    stores: Stores,
    world: Router,
    bank: Sequence[Sample],
    *,
    round_num: int = 1,
    optimization: dict[str, Any] | None = None,
    split: dict[str, int] | None = None,
    layer: dict[str, Any] | None = None,
    dataset: str = LAUNCHED_DATASET,
) -> AsyncIterator[RoundContext]:
    """Round *round_num* of a cycle run init opened on *dataset*, as every door opens one: the
    offline *world* answers *bank*'s origin walk and an empty price list, admission holds the
    budget, the mint declares the campaign, unframed, *layer* laid over it. An L4 *dataset*'s
    cells are inner campaigns on `LAUNCHED_DATASET`'s *bank*. Held open inside the launch's scope,
    so the block walks the round on the run's own ledger, book and client; a sync journey runs it
    on `jumping_clock`."""
    launched_bank(stores, world, bank)
    raw = dict(dataset_campaign_block(resolve_dataset(stores, dataset)))
    held = raw["optimization"]
    if "optimizer" in (optimization or {}):
        # The template's node overlay is written for the optimizer it selects, and only that one.
        held.pop("nodes", None)
    raw |= {
        "optimization": held | {"budget": {"usd": 1000.0}} | (optimization or {}),
        "dataset_split": split or {"bench": 0, "demo": 0},
        "task_framing": "off",
    } | (layer or {})
    entered = await enter(stores)
    layers = ConfigLayers(campaign_defaults(), raw)
    admitted = await asyncio.to_thread(
        admit,
        FreshMint(resolve_dataset(entered, dataset), Budget(), layers, None, hosted=Hosted()),
        RunMode(),
        stores=stores,
        job_registry=JobRegistry.attach(stores.jobs_dir),
        inline=None,
    )
    held = await admitted.held()
    async with (
        held.closing,
        launched(held, round_num=round_num) as ctx,
    ):
        yield ctx


@asynccontextmanager
async def launched(held: HeldRun, *, round_num: int) -> AsyncIterator[RoundContext]:
    """Run init on a held cycle as its door opens it, through round 0's door
    (`loop.py::open_origin_round`): round *round_num*, held open inside the launch's scope."""
    running, cycle_config = held.session, held.cycle
    observers = await asyncio.to_thread(enter_cycle, running)
    try:
        with launch_scope(running, observers, cycle_config.config):
            prep = await open_launch(
                list(running.bank.samples),
                cycle_config,
                session=running,
                observers=observers,
                budget=held.budget,
                optimizer=held.optimizer,
            )
            cycle, observers = await open_line(
                prep, session=running, observers=observers, mode=RunMode()
            )
            ended = await open_origin_round(cycle, prep.dataset, resumed_ending=None)
            assert ended is None, f"round 0's door ended the launch: {ended}"
            with scoped(round=round_num):
                yield RoundContext(cycle, round_num)
    finally:
        observers.release()


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


def run_wiring(measurement_node: str = "l1_score") -> RunWiringRecord:
    """A launch's wiring on one cell at a time, declaring no limit: what a dashboard folds first."""
    return RunWiringRecord(
        round_cells_ceiling=1,
        display_metric="fitness",
        elects_on="fitness",
        max_cells_in_flight=1,
        measured_unit="sample",
        measurement_node=measurement_node,
        budget=Budget(),
        optimizer_limits=[],
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


def laid_ledger(cycle_dir: Path) -> CycleEventLog:
    """A scratch ledger in a directory laid as a mint lays one: `open` never creates the cycle."""
    cycle_dir.mkdir(parents=True, exist_ok=True)
    return CycleEventLog.open(CycleDir(cycle_dir))


def walking_cycle(stores: Stores, hop: CycleHop) -> Path:
    """Minted *hop* a producer holds and has declared running: what a loop command lands on."""
    cycle_dir = stores.campaigns.cycle_dir(hop)
    CycleEventLog.open(CycleDir(cycle_dir)).append(RunPhaseRecord(state=Running()))
    hold_cycle(cycle_dir)
    return cycle_dir


def run_holding(stores: Stores, hop: CycleHop) -> str:
    """Minted *hop* held by a started run's job walking its loop: the job's hold on the cycle's
    ledger, the cycle declared running under its lock. The job's id."""
    registry = JobRegistry.attach(stores.jobs_dir)
    job = registry.create(
        user_id=str(stores.identity.user_id),
        principal_id=str(stores.identity.user_id),
        workspace=stores.campaigns.workspace,
        hop=hop,
        holder=CampaignLaunch(dataset_name="d"),
    )
    for move in (LaunchMove.ADMIT, LaunchMove.START):
        registry.move(job.job_id, move)
    claim_cycle(stores, hop, job.job_id)
    walking_cycle(stores, hop)
    return job.job_id


def arm_walk(
    ledger: CycleEventLog,
    cells: Iterable[tuple[int, WalkedCell]],
    *,
    rnd: int = 0,
    individual: str = "c0",
    role: ArmRole = MeasurementRole.ORIGIN,
) -> list[SampleScoredRecord]:
    """*individual* announced as an arm of round *rnd*, then each filed cell scored at its slot,
    under the default scorer's basis."""
    announced = Proposed(
        role=role, position=ArmPosition(round=rnd, idx=0, total=1), individual=prompted()
    )
    ledger.append(ArmRecord(round=rnd, arm=ArmKey(individual), state=announced))
    ledger.append(FoldBasisRecord(basis=FoldBasis.of(scorer_of(), pipeline_schema([]))))
    rows = [
        SampleScoredRecord(
            round=rnd,
            individual_id=individual,
            role=role,
            position=slot,
            result=GradedCell.from_wire({"sample_key": key, "answer": answer}),
        )
        for slot, (key, answer, _) in cells
    ]
    for row in rows:
        ledger.append(row)
    return rows


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
    inner = stores.campaigns.in_sandbox(sandbox)
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
    seats = inner_cells(optimizer, dataset=LAUNCHED_DATASET, campaign=laid)
    (cell_seat,) = seats.cells
    owner = CycleHop(campaign_id="outer", cycle_id="cycle-0")
    spawning = InnerSpawnContext(
        stores=stores,
        inside=(owner,),
        enclosing=(),
        acting=(),
        cells=seats,
        purpose=Optimize(),
        ruler=0,
    )
    pin = PinnedCell(cell=cell_seat, instrument="i").pin()
    asked = Sample(id=0, query=cell_seat.id, ground_truth=None, source_pin=pin)
    # Under the outer run's book, as a cell's spawn reads it.
    with scoped(account=stores), scoped(spawn=spawning, book=spend_book(1000.0)), scoped(round=0):
        cell = spawn._resolve_inner_cell(asked, {"optimizer_prompt_overrides": overrides})
        async with (
            spawn._open_seat(cell, spawn.claim_sandbox(spawning)) as held,
            held.closing,
            launched(held, round_num=round_num) as ctx,
        ):
            return ctx


def inner_cells(
    *optimizers: str,
    dataset: str | None = None,
    campaign: CampaignConfig | None = None,
) -> InnerCells:
    """One seat per named manifest, on a dataset of its own, so each cell runs its own optimizer;
    a lone seat runs on *dataset* where one is named, each cell's dataset laid as *campaign*."""
    assert dataset is None or len(optimizers) == 1, "two seats never share a dataset"
    on = {name: dataset or f"{name}ds" for name in optimizers}
    laid = campaign or defaulted_config({})
    by_dataset = {
        on[name]: InnerCell(
            laid, cell_optimizer(select_optimizer(bench_choice(name)), laid.scoring)
        )
        for name in optimizers
    }
    cells = tuple(Seat(kind="seat", id=name, inner_dataset=on[name]) for name in optimizers)
    return InnerCells(cells=cells, by_dataset=by_dataset, datasets=on, estimator="e")


def pobb_knobs(**bend: Any) -> PoBBKnobs:
    shipped = cast("PoBBKnobs", select_optimizer(bench_choice("potter")).knobs("pobb"))
    return shipped.model_copy(update=bend)


def election_knobs() -> ElectionKnobs:
    return cast(
        "ElectionKnobs",
        select_optimizer(bench_choice("potter")).knobs("theta_election"),
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


def optimizer_state(*, parse_failure: ValidationReason | None = None) -> OptimizerState:
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


def arm_key(name: str) -> ArmKey:
    """The key a round files `arm_state(name)` under: its individual's id."""
    return ArmKey(prompted(instruction=name).id)


#: The reference arm every built round carries: a held round ends on it.
PARENT = arm_key("parent")


def election(
    selected: str | None = None,
    *,
    moved_to: str | None = None,
    parent: str = PARENT,
    parent_round: int = 0,
    **overrides: Any,
) -> Election:
    """*selected* crowns, else the line moves to *moved_to* uncrowned, else it holds on *parent*,
    walked in *parent_round*."""
    pick = None if selected is None else ArmKey(selected)
    moved = None if moved_to is None else ArmKey(moved_to)
    fields: dict[str, Any] = {
        "leading": pick or moved,
        "ending": RoundEnding.CROWNED
        if pick is not None
        else RoundEnding.MOVED
        if moved is not None
        else RoundEnding.HELD,
        "parent": RoundArm(round=parent_round, arm=ArmKey(parent)),
        "electable": int(pick is not None),
        "elects_on": "ability",
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
    parse_failure: ValidationReason | None = None,
    no_op: int = 0,
    dup: int = 0,
    collapsed: int = 0,
    cut: int = 0,
    selected: str | None = None,
    arms: Sequence[ArmState] | None = None,
    voted: dict[str, Any] | None = None,
    **overrides: Any,
) -> RoundResult:
    """Each arm keyed by its individual, as `Parent.own_rows` reads it; *improved* selects the first
    measured arm unless *selected* names the pick; *voted* bends the election's other fields."""
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
    scored = [
        arm_state(
            f"c{i}",
            position=ArmPosition(round=rnd, idx=i, total=panel),
            total=2 if i < cut else len(_TRUTH),
        )
        for i in range(candidates_scored)
    ]
    rejected = [
        arm_state(
            f"x{i}",
            position=ArmPosition(round=rnd, idx=candidates_scored + i, total=panel),
            invalid_reason=reason,
        )
        for i, reason in enumerate(reasons)
    ]
    keyed = {
        ArmKey(a.individual.id): a
        for a in ([*scored, *rejected, arm_state("parent")] if arms is None else arms)
    }
    if selected is None and improved:
        measured = (
            k for k, a in keyed.items() if isinstance(a, Measured) and a.position is not None
        )
        selected = next(measured, None)
    vote = election(
        selected,
        **{"electable": candidates_scored, "parent_round": max(rnd - 1, 0)} | (voted or {}),
    )
    if not rnd and vote.ending is RoundEnding.CROWNED:
        # Round 0 adopts its origin, which no arm was elected against.
        vote = vote.model_copy(
            update={"ending": RoundEnding.ADOPTED, "parent": None, "electable": 0}
        )
    # Merged rather than splatted after the literals, so ``overrides`` reaches EVERY field.
    base: dict[str, Any] = {
        "round": rnd,
        "arms": keyed,
        "folds": {
            key: score_summary(total=arm.expected_samples)
            for key, arm in keyed.items()
            if isinstance(arm, Cut | Measured) and arm_end(arm) != "invalid"
        },
        "election": vote,
        # No pair read: a test that bends the advance passes the reading that derives it.
        "overlap": OverlapReading.unpaired(
            ReadingState.NOT_HELD if rnd else ReadingState.SAME_INDIVIDUAL,
            vote.ending,
        ),
        "cells": {
            ArmKey(arm.individual.id): sheet(
                measurement(
                    sid,
                    None,
                    predicted="Uncertain" if i < collapsed or i < cut else t,
                    ground_truth=t,
                )
                for sid, t in enumerate(_TRUTH[:2] if i < cut else _TRUTH)
            )
            for i, arm in enumerate(scored)
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
        refused={},
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
        "on_live_cycle": True,
        "inner": False,
        "refused": None,
        "held_by": None,
        "trigger": "at_end",
        "held_out": 4,
        "instrument_id": "i",
        "spend": None,
        "files": True,
    } | line
    return BenchLine(
        run=LiveCycleRun(
            selecting=selecting,
            passing=False,
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

    ``cold`` and ``caveat`` (every level's) are set apart: `theta_caveat` ranks other states above
    a cold scale."""
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
            caveat=caveat,
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
    )


def bench_curve(origin: float | None, levels: list[float | None]) -> BenchCurve:
    """Two bench rows reading *levels* alike, so the area is their mean less *origin*; an unread
    level reads `PAST_TOLERANCE`, a line that spent no round `NO_SELECTION`."""
    if origin is None or None in levels or not levels:
        state = ReadingState.NO_SELECTION if not levels else ReadingState.PAST_TOLERANCE
        return BenchCurve(state=state, origin=origin, levels=levels, area=None, rows={})
    read = [level for level in levels if level is not None]
    # Its rows carry LEVELS (each a mean over a pass's cells), never a cell's 0/1.
    area = mean_lift(grade_measurands(scorer_of())[0], [origin] * 2, [fmean(read)] * 2, pooled=True)
    rows = {key: [origin, *levels] for key in ("a", "b")}
    return BenchCurve(state=ReadingState.READ, origin=origin, levels=levels, area=area, rows=rows)


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

    The held-out curve defaults FLAT at the bench origin, one entry per level, so a test bending
    only the θ side still scores; `bench_origin=None` is an origin pass that read nothing.
    """
    if bench_levels is None:
        bench_levels = [bench_origin if bench_origin is not None else 0.5] * len(levels)
    curve = bench_curve(bench_origin, bench_levels)
    unheld = UnreadPair.of(ReadingState.NOT_HELD)
    bench = BenchScore(
        bench_size=0,
        scorer_id="",
        headline=BENCH_HEADLINE,
        status=bench_status(
            trigger="at_end",
            bench_size=0,
            on_live_cycle=True,
            inner=False,
            refused=None,
            held_by=None,
            origin=None,
            selected=None,
            run=LiveCycleRun(
                selecting=False,
                passing=False,
                pass_open=False,
                ending=stop_reason,
                selection=None,
                rounds_closed=0,
            ),
            files=True,
        ),
        origin=None,
        selected=None,
        vs_origin=unheld,
        cost=LiftCost.of(unheld, None),
        scopes=[],
    )
    return CycleResult(
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
        curve=curve,
        round_budget=overrides.pop("round_budget", len(levels)),
        result_prompt_fields={},
        stop_reason=stop_reason,
        started_at="2026-01-01T00:00:00Z",
        finished_at="2026-01-01T01:00:00Z",
        wall_clock=WallClock(elapsed_s=None),
        rounds_to_separable=None,
        rounds_to_improved=None,
        rounds_to_ceiling=None,
        fitness_ceiling=None,
        prompt_hashes={},
        scorer_id="",
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
