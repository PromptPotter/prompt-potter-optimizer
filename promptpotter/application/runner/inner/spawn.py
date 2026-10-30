"""Instrument mode is declared in ``shared/measurement_context.py`` alone; read it before changing what it binds."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Any, NamedTuple

from promptpotter.application.diagnostics.seed_screen import class_floor, draw_bank, draw_bench
from promptpotter.application.initialization.wiring import init_services
from promptpotter.application.jobs.launcher.admission import inner_share
from promptpotter.application.jobs.mint import (
    ConfigDriftError,
    declare,
    mint,
    resolve_cycle_plan,
    resolve_resume,
)
from promptpotter.application.optimizer_manifest import (
    SelectedOptimizer,
    binding_prompt_overrides,
    resolved_overrides,
)
from promptpotter.application.pipeline_resolve import configure_and_apply_pipeline
from promptpotter.application.run_observers import build_run_observers
from promptpotter.application.run_phase_control import RunControl
from promptpotter.application.runner.entry import run_optimization
from promptpotter.application.runner.inner.spawn_context import (
    InnerSpawnContext,
    enter_inner_cell,
    inner_spawn_context,
)
from promptpotter.application.runner.inner.tasks import (
    InnerCells,
    InnerTaskSpec,
    inner_instrument_config,
    panel_cell,
)
from promptpotter.application.views.render.primitives import fmt_pct
from promptpotter.domain.arm_reading import ArmFit, order_floor
from promptpotter.domain.arms import Cut, Measured
from promptpotter.domain.campaign_config import CampaignConfig, effective_config
from promptpotter.domain.cells import Seat
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.l4.inner_facts import SeriesGap, series_reason_head
from promptpotter.domain.l4.proxies import (
    INNER_RESULT_KEY,
    compute_outer_proxies,
    effective_round_budget,
    floor_reason,
    inner_cell_facts,
    parent_levels,
    unread_rounds,
)
from promptpotter.domain.phases import REFUSAL_STOPS, StopReason
from promptpotter.domain.run_mode import RunMode
from promptpotter.domain.run_records import SpawnedBy
from promptpotter.domain.wounds import COLLAPSE_WORDS, collapse_counts
from promptpotter.infrastructure.llm.telemetry import current_round
from promptpotter.infrastructure.runtime_flags import derive_run_state
from promptpotter.infrastructure.store.io import write_json
from promptpotter.infrastructure.store.layout import dataset_id_prefix, sandbox_owner_path
from promptpotter.infrastructure.store.session_pointer import save_active_pointer
from promptpotter.infrastructure.store.stores import build_stores
from promptpotter.shared.errors import (
    CellSendRefusedError,
    CellUnscoreableError,
    ErrorCategory,
)
from promptpotter.shared.hashing import shapes_optimizer_prompt, stable_hash
from promptpotter.shared.measurement_context import (
    MAX_INSTRUMENT_DEPTH,
    MeasurementRole,
    instrument_depth,
    measured_pass,
    measured_pass_context,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from promptpotter.application.initialization.session import Session
    from promptpotter.domain.campaign import Campaign
    from promptpotter.domain.l4.inner_facts import InnerCellFacts
    from promptpotter.domain.l4.proxies import OuterSampleProxies
    from promptpotter.domain.results import CycleResult
    from promptpotter.domain.sample import Sample
    from promptpotter.infrastructure.store.campaign_store.store import CampaignStore
    from promptpotter.infrastructure.store.stores import Stores

logger = logging.getLogger(__name__)


# An inner cycle's budget is ROUNDS, never a spend or token cap: those trip on token counts, which jitter.

_REFUSALS: dict[StopReason | None, ErrorCategory] = {
    stop: category for category, stop in REFUSAL_STOPS.items()
}

# Per round, for ONE inner cell; never instrument-depth-dependent, or an outcome moves with what is in flight.
OUTER_SAMPLE_WALL_S_PER_ROUND = 600.0

Overrides = dict[str, dict[str, Any]]


def _spawn_provenance(ctx: InnerSpawnContext, round_num: int | None, query: str) -> dict[str, Any]:
    cand = measured_pass()
    return {
        "round": round_num,
        "individual_id": cand.individual_id if cand else None,
        "role": cand.role if cand else None,
        "cycle_id": ctx.enclosing[-1].name if cand else None,
        "task": query,
    }


@shapes_optimizer_prompt
def _clip(text: str, cap: int) -> str:
    text = " ".join(text.split())
    if len(text) <= cap:
        return text
    return text[: cap - 1].rsplit(" ", 1)[0] + "…"


@shapes_optimizer_prompt
def _lift_shape(result: CycleResult) -> str:
    l1 = [rr for rr in result.rounds if rr.round > 0]
    marks = " ".join(f"r{rr.round}{'+' if rr.improved else '.'}" for rr in l1)
    if not marks:
        return "lifts: none — no L1 round closed."
    n = sum(1 for rr in l1 if rr.improved)
    budget = effective_round_budget(result)
    return f"lifts: {marks} ({n}/{budget}; target: early and often, thinning late)"


@shapes_optimizer_prompt
def _inner_narrative(
    result: CycleResult, facts: InnerCellFacts, proxies: OuterSampleProxies, head: str
) -> str:
    """Authored under ``TRANSCRIPT_REASONING_CAP``, so the outer transcripts render never clips it."""
    if (floor := floor_reason(result)) is not None:
        return (
            f"{head}: {floor} — scored at the floor "
            f"(optimizer-prompt-owned); stop={result.stop_reason}."
        )
    # Both guaranteed by `compute_outer_proxies`, which ran first.
    bench_origin, bench_final = facts.bench_origin_level, facts.bench_final_lift
    assert bench_origin is not None and bench_final is not None
    held = parent_levels(result)
    levels = [] if held is None else held[1]
    origin = facts.origin_level
    if origin is None:
        reason = facts.series_reason
        assert reason is not None
        holes = f", round(s) {unread_rounds(result)}" if reason is SeriesGap.UNREAD_ROUND else ""
        search = f" | search-split th: withheld — {series_reason_head(reason)}{holes}"
    else:
        assert facts.mean_round_delta is not None
        assert facts.final_lift is not None and facts.peak_lift is not None
        search = (
            f" | search-split th: floor {origin:+.2f} -> mean D{facts.mean_round_delta:+.3f}"
            f", ended {origin + facts.final_lift:+.2f}, peak {origin + facts.peak_lift:+.2f}"
            f" over {facts.rounds_ran} of {facts.round_budget} rounds"
        )
    lines = [
        f"{head}: held-out {result.bench.bench_size} rows:"
        f" floor {bench_origin:.3f} -> mean-over-rounds B{proxies.mean_bench_lift:+.3f}"
        f" (the scored lift), ended {bench_origin + bench_final:.3f} (B{bench_final:+.3f})"
        f"{search}; stop={result.stop_reason}.",
        _lift_shape(result),
    ]
    by_round = {rnd.round: rnd for rnd in result.rounds}
    critiques = {r: rnd.closed_state.feedback() for r, rnd in by_round.items()}
    highlight = next(
        (
            f"{cluster['mechanism']}: {cluster['claim']}"
            for r in sorted(by_round)
            if (c := critiques.get(r))
            for cluster in c.get("failure_highlights") or []
        ),
        None,
    )
    if highlight:
        lines.append(f"saw: {_clip(highlight, 200)}")
    # Positional: the window's levels run beside its rounds, whatever round it starts at.
    for at, rnd in enumerate(sorted(result.rounds, key=lambda rr: rr.round)):
        if rnd.round == 0:
            continue
        parts = []
        if origin is not None and at < len(levels):
            parts.append(f"level {levels[at]:.3f} (D{levels[at] - origin:+.3f})")
        steer = critiques.get(rnd.round - 1)
        if steer and steer.get("priority_fix"):
            parts.append(f"steer: {_clip(steer['priority_fix'], 130)}")
        fits = {key: rnd.fit(key) or ArmFit() for key in rnd.panel}
        scored = [
            (key, c, fold)
            for key, c in rnd.panel.items()
            if isinstance(c, Cut | Measured) and (fold := rnd.folds.get(key)) is not None
        ]
        if scored:
            # Lift over the MATCHED parent only: an arm that never covered its panel has no lift.
            floors = {
                key: reading.reference_level("fitness")
                if (reading := fits[key].vs_reference)
                else None
                for key, _, _ in scored
            }
            margins = {
                key: float("-inf")
                if (matched := floors[key]) is None or fold.accuracy is None
                else fold.accuracy - matched
                for key, _, fold in scored
            }
            top_key, top, top_fold = max(
                scored, key=lambda kc: (margins[kc[0]], order_floor(kc[2].composite_fitness))
            )
            reference = floors[top_key]
            fit = fits[top_key]
            theta = (
                f", th {fit.theta:.2f}+/-{fit.theta_se:.2f}"
                if fit.theta is not None and fit.theta_se is not None
                else ""
            )
            versus = (
                f" vs matched-parent {reference:.3f}"
                if reference is not None
                else " (stopped before it covered the origin's samples, so nothing to compare)"
            )
            parts.append(
                f"tried {top.label} (acc {fmt_pct(top_fold.accuracy, '{:.3f}')}{versus}{theta}): "
                f"{_clip(top.changes_description, 100)}"
            )
        else:
            parts.append("no scored candidates")
        collapses = collapse_counts(c.validation_failures for c in rnd.panel.values())
        anomalies = [
            f"{word} x{n}"
            for reason, word in COLLAPSE_WORDS.items()
            if (n := collapses.get(reason))
        ]
        if anomalies:
            parts.append(", ".join(anomalies))
        lines.append(f"R{rnd.round} " + " | ".join(parts))
    # Earliest rounds go first: the panel's own head-keep clip would cut the latest instead.
    n_head = 3 if highlight else 2
    head_lines, round_lines = lines[:n_head], lines[n_head:]
    elided = False
    while len(round_lines) > (2 if elided else 1) and (
        len("\n".join(head_lines + round_lines)) > 1150
    ):
        round_lines.pop(0 if not elided else 1)
        if not elided:
            round_lines.insert(0, "[earlier rounds elided]")
            elided = True
    return "\n".join(head_lines + round_lines)


def _purpose(role: MeasurementRole) -> str:
    return "backfill" if role is MeasurementRole.BACKFILL else "own"


def inner_campaign_id(
    spec: InnerTaskSpec,
    overrides: Overrides,
    role: MeasurementRole = MeasurementRole.PANEL,
) -> str:
    """Content, never asker: the TREATMENT, never the budget, so a deepened cell continues the rounds already banked."""
    digest = stable_hash(
        [spec.treatment(), resolved_overrides(overrides), _purpose(role)], length=6
    )
    return f"{dataset_id_prefix(spec.inner_dataset)}__{digest}"


class _InnerCell(NamedTuple):
    """Resolved in the OUTER task, which alone carries the role."""

    ctx: InnerSpawnContext
    cell: Seat
    spec: InnerTaskSpec
    overrides: Overrides
    role: MeasurementRole
    campaign_id: str


def _resolve_inner_cell(sample: Sample, payload: dict[str, Any]) -> _InnerCell:
    ctx = inner_spawn_context()
    if ctx is None:
        raise RuntimeError(
            "promptpotter connector: this run has no cycle to spawn an inner campaign under"
        )
    cells = _cells(ctx)
    cell = panel_cell(sample)
    arm = cells.own(cell, payload.get("optimizer_prompt_overrides") or {})
    role = cand.role if (cand := measured_pass()) else MeasurementRole.PANEL
    spec = cells.spec(cell)
    return _InnerCell(ctx, cell, spec, arm, role, inner_campaign_id(spec, arm, role))


def sandbox_stores(ctx: InnerSpawnContext) -> Stores:
    """Only campaign STATE is sandboxed; the caches stay on the real tenant tree via ``shared_root``."""
    return build_stores(
        ctx.identity, projects_root=ctx.inner_sandbox_root, shared_root=ctx.shared_root
    )


def claim_sandbox(ctx: InnerSpawnContext) -> Stores:
    """Inside a cell, never at `publish_inner_spawn_context`, which fires for EVERY cycle."""
    write_json(
        sandbox_owner_path(ctx.inner_sandbox_root),
        {
            "tenant_id": str(ctx.identity.tenant_id),
            "campaign_id": ctx.spawn_campaign_id,
            "cycle_id": ctx.spawn_cycle_id,
        },
    )
    return sandbox_stores(ctx)


def _banked_inner_rounds(store: CampaignStore, root: CycleHop) -> int:
    index = store.load(store.line_holder(root), moment=None)
    return len(index.rounds) if index else 0


def inner_cell_envelope_s(sample: Sample, payload: dict[str, Any]) -> float:
    """``max(1, …)`` grants a fully-banked cycle the round it needs to replay and finalize."""
    cell = _resolve_inner_cell(sample, payload)
    store = sandbox_stores(cell.ctx).campaigns
    campaign = store.load_campaign(cell.campaign_id)
    banked = _banked_inner_rounds(store, campaign.root_hop) if campaign else 0
    return OUTER_SAMPLE_WALL_S_PER_ROUND * max(1, (cell.spec.n_rounds + 1) - banked)


def inner_cell_hop(sample: Sample, payload: dict[str, Any]) -> CycleHop | None:
    """The hop holding the cell's campaign line, as the outer cell's heartbeat names it."""
    cell = _resolve_inner_cell(sample, payload)
    store = sandbox_stores(cell.ctx).campaigns
    campaign = store.load_campaign(cell.campaign_id)
    return None if campaign is None else store.line_holder(campaign.root_hop)


def _continue_inner_campaign(
    session: Session, campaign: Campaign, train_data: Sequence[Sample]
) -> tuple[Session, SelectedOptimizer]:
    """``stop_reason_outcome`` governs scoring, never resumption."""
    store = session.store.campaigns
    # A rebase retires the root under `superseded_by`; the banked trajectory is its successor's.
    hop = store.line_holder(campaign.root_hop)
    run = derive_run_state(store.cycle_dir(hop))
    if store.load(hop, moment=None) is None:
        raise CellUnscoreableError(
            f"its campaign {campaign.campaign_id} names successor {hop.cycle_id}, which has no "
            "index",
            spent={},
        )
    if not run.resumable:
        raise CellUnscoreableError(
            f"its campaign {campaign.campaign_id}/{hop.cycle_id} reads {run.run_phase} — another "
            "producer owns it, and two runs writing one cycle is not a measurement",
            spent={},
        )
    bound = session.bound(hop, purpose=campaign.declaration.purpose)
    try:
        optimizer = resolve_resume(bound, campaign, train_data)
    except ConfigDriftError as drift:
        raise CellUnscoreableError(str(drift), spent={}) from drift

    save_active_pointer(session.store.base_dir, hop)
    logger.info(
        "inner campaign %s/%s CONTINUES from %d banked round record(s) (was %s)",
        campaign.campaign_id,
        hop.cycle_id,
        _banked_inner_rounds(store, campaign.root_hop),
        run.run_phase,
    )
    return configure_and_apply_pipeline(bound, effective_config(campaign.declaration)), optimizer


def _cells(ctx: InnerSpawnContext) -> InnerCells:
    assert ctx.cells is not None, "run init refuses an outer dataset that holds no panel"
    return ctx.cells


class _Opened(NamedTuple):
    session: Session
    config: CampaignConfig
    train_data: list[Sample]
    # The constant-answer floor over the bank the seat drew.
    bank_floor: float | None
    optimizer: SelectedOptimizer


async def _open_seat(cell: _InnerCell, store: Stores) -> _Opened:
    ctx, spec = cell.ctx, cell.spec
    enter_inner_cell(ctx, store.campaigns, cell.campaign_id)
    # enable_tracing=False: cloud traces of an ephemeral measurement pile spans in process memory.
    session = await init_services(
        dataset_name=spec.inner_dataset,
        identity=ctx.identity,
        stores=store,
        enable_tracing=False,
    )
    session.state.control = RunControl(enclosing=ctx.enclosing)
    all_samples = session.bank.samples
    if not all_samples:
        raise ValueError(f"inner dataset {spec.inner_dataset!r} loaded zero samples")
    n = min(max(spec.n_samples, spec.n_samples_origin or 0), len(all_samples))
    # The draw `seed-screen` chose the seeds under: drawn differently, the bank is one nobody screened.
    search = draw_bank(all_samples, n, spec.run_seed)
    bench = draw_bench(all_samples, search, spec.n_samples_bench, spec.run_seed)
    if len(bench) < spec.n_samples_bench:
        # Before anything is minted or sent: a short bench is a panel geometry error, not a measurement.
        raise CellUnscoreableError(
            f"inner dataset {spec.inner_dataset!r} holds {len(all_samples)} rows; the bank takes "
            f"{len(search)} and leaves {len(bench)} for a bench of {spec.n_samples_bench}. Lower "
            "`n_samples_origin` / `n_samples_bench` or give the cell a larger dataset.",
            spent={},
        )
    train_data = [*search, *bench]
    config = inner_instrument_config(
        spec,
        CampaignConfig.model_validate(
            dict(_cells(ctx).by_dataset[spec.inner_dataset].campaign_config)
        ),
        solver=session.pipeline_schema.prompt_nodes(),
        n_search=len(search),
    )
    declaration = declare(session, config, purpose=ctx.purpose)
    # The campaign that exists decides, never an id its origin content derives: a drifted origin
    # reaches the drift refusal rather than a fresh root.
    campaign = session.store.campaigns.load_campaign(cell.campaign_id)
    if campaign is None:
        minted = mint(
            resolve_cycle_plan(session, declaration, train_data),
            campaign_id=cell.campaign_id,
            billed=None,
        )
        session, optimizer = minted.session, minted.optimizer
    else:
        session, optimizer = _continue_inner_campaign(session, campaign, train_data)
    return _Opened(
        session, effective_config(declaration), train_data, class_floor(search), optimizer
    )


async def _run_inner_campaign(
    cell: _InnerCell,
    minted: list[CycleHop],
    spawned_by: dict[str, Any],
) -> CycleResult:
    """Runs in a FRESH task, so every ContextVar bound below is this cell's alone."""
    store = claim_sandbox(cell.ctx)
    # Over the mint too: the treatment it declares hashes the overridden prompts.
    with binding_prompt_overrides(cell.overrides):
        opened = await _open_seat(cell, store)
        session, config = opened.session, opened.config
        session.store.campaigns.record_spawned(session.hop, SpawnedBy.model_validate(spawned_by))
        minted.append(session.hop)
        observers = build_run_observers(session=session, campaign_config=config)
        try:
            # Each send is admitted by its own book AND the ROOT's; its share bounds every checkpoint.
            with inner_share(
                config,
                stores=session.store,
                dataset_name=cell.spec.inner_dataset,
                hop=session.hop,
            ) as held:
                _, result = await run_optimization(
                    opened.train_data,
                    config,
                    session=session,
                    observers=observers,
                    budget=held,
                    mode=RunMode(),
                    optimizer=opened.optimizer,
                )
        finally:
            # The optimizer LLM clients and the Langfuse SDK are process-SHARED: never shut one down.
            with contextlib.suppress(Exception):
                await session.backend_client.aclose()
            if session.langfuse is not None:
                with contextlib.suppress(Exception):
                    session.langfuse.reset()
    # Reported, never enforced: one origin pass sits inside its own error bar.
    origin_acc = (
        None
        if result.origin is None or result.origin.accuracy is None
        else result.origin.accuracy.value
    )
    floor = opened.bank_floor
    if origin_acc is not None and floor is not None and floor >= origin_acc:
        logger.warning(
            "inner cell %s MAY REWARD COLLAPSE: constant-answer floor %.3f >= this run's origin "
            "%.3f. One pass sits inside its own error bar — re-screen the seat "
            "(`python -m promptpotter seed-screen`) before trusting the panel.",
            cell.cell.id,
            floor,
            origin_acc,
        )
    return result


def _narrative_head(spec: InnerTaskSpec) -> str:
    # Two cells of a pool share a dataset and a seed, so the model is what tells them apart.
    env = f"{spec.inner_dataset} on {spec.inner_model}" if spec.inner_model else spec.inner_dataset
    return f"INNER {env} seed-{spec.run_seed}"


async def run_inner_cycle(sample: Sample, payload: dict[str, Any]) -> dict[str, Any]:
    """One cell: an inner hop in the asker's sandbox, read by one law."""
    query = sample.query
    depth = instrument_depth()
    if depth >= MAX_INSTRUMENT_DEPTH:
        raise RuntimeError(
            f"promptpotter connector: inner recursion is already {depth} level(s) deep "
            f"(MAX_INSTRUMENT_DEPTH={MAX_INSTRUMENT_DEPTH}); refusing to spawn another inner "
            "campaign. An inner dataset whose own backend_type is 'promptpotter' recurses "
            "without bound — check the inner_benchmark named in the dataset's panel."
        )
    cell = _resolve_inner_cell(sample, payload)
    minted: list[CycleHop] = []
    # Captured in the OUTER task: the inner task's context copy rebinds the round.
    spawned_by = _spawn_provenance(cell.ctx, current_round(), query)

    # Awaited DIRECTLY, never under `asyncio.shield`/`wait`, which orphan it to keep billing.
    # The context binds no OUTER candidate, or inner optimizer calls bill under the outer cell's role.
    # Its wait beats on the outer ledger through the measuring call's own heartbeat (`cell_hop`).
    inner_task = asyncio.create_task(
        _run_inner_campaign(cell, minted, spawned_by),
        context=measured_pass_context(None),
    )
    try:
        result = await inner_task
    except asyncio.CancelledError:
        logger.warning(
            "inner cell %s abandoned; its partial campaign is at %s",
            query,
            sandbox_stores(cell.ctx).campaigns.cycle_dir(minted[0])
            if minted
            else "<not yet minted>",
        )
        if not inner_task.done():
            inner_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await inner_task
        raise
    if (refused := _REFUSALS.get(result.stop_reason)) is not None:
        # The inner run spends the outer's key and ceiling, so the refusal halts the walk for every later cell.
        raise CellSendRefusedError(
            f"its inner campaign {cell.campaign_id} stopped on {result.stop_reason}",
            category=refused,
            spent={},
        )
    # Raises `CellUnscoreableError` on every other no-evidence shape, which `measure_sample` resolves.
    proxies = compute_outer_proxies(result)
    facts = inner_cell_facts(result)
    delta = "—" if facts.mean_round_delta is None else f"{facts.mean_round_delta:+.3f}"

    data: dict[str, Any] = {
        # A summary line, not an answer to be matched: this cell carries no label, so add no hit/miss reader.
        INNER_RESULT_KEY: [f"inner:{query} B{proxies.mean_bench_lift:+.3f} D{delta}"],
        "reasoning_trace": _inner_narrative(result, facts, proxies, _narrative_head(cell.spec)),
        **proxies.model_dump(),
        "inner": facts.model_dump(mode="json"),
        # An inner campaign consumes its optimizer's ENTIRE outer config, so the stamp is its terminal.
        "terminal_node": _cells(cell.ctx).terminal(cell.cell),
        # No `step_tokens`: every call was billed as it settled, and returning it bills the cell twice.
    }
    return {"data": data}
