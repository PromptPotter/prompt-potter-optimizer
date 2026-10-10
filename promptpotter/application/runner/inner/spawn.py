"""Instrument mode is declared in ``shared/measurement_context.py`` alone; read it before changing what it binds."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import TYPE_CHECKING, Any, NamedTuple

from promptpotter.application.campaign_config import load_campaign_config
from promptpotter.application.diagnostics.seed_screen import class_floor, draw_bank
from promptpotter.application.initialization.wiring import init_services
from promptpotter.application.jobs.mint import prepare_fresh_cycle, resolve_cycle_plan
from promptpotter.application.jobs.quota import unadmitted_limits
from promptpotter.application.optimizer_manifest import (
    resolved_overrides,
    set_optimizer_prompt_overrides,
)
from promptpotter.application.run_observers import build_run_observers
from promptpotter.application.run_phase_control import RunControl
from promptpotter.application.runner.entry import run_optimization
from promptpotter.application.runner.inner.spawn_context import (
    InnerSpawnContext,
    inner_spawn_context,
)
from promptpotter.application.runner.inner.tasks import (
    InnerCells,
    InnerTaskSpec,
    inner_instrument_config,
    resolve_inner_task,
)
from promptpotter.application.served_dashboard import ServedDashboard, served_dashboard
from promptpotter.application.views.render.optimizer_prompt_text import fmt_pct
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.l4.proxies import (
    INNER_RESULT_KEY,
    PARENT_LEVEL_SE_KEY,
    compute_outer_proxies,
    effective_round_budget,
    floor_reason,
    inner_cell_facts,
    mean_parent_level_se,
    parent_level_series,
)
from promptpotter.domain.launch_limits import LaunchLimits, RunMode
from promptpotter.domain.phases import REFUSAL_STOPS, StopReason
from promptpotter.domain.results import ArmOutcome, candidate_label
from promptpotter.domain.run_records import SpawnedBy
from promptpotter.domain.wounds import collapse_counts
from promptpotter.infrastructure.llm.heartbeat import heartbeat
from promptpotter.infrastructure.llm.telemetry import _CURRENT_ROUND, _CYCLE_LEDGER
from promptpotter.infrastructure.runtime_flags import derive_run_state, standing_run_limits
from promptpotter.infrastructure.store.archive_queries import scope_memory_to_own_answers
from promptpotter.infrastructure.store.io import write_json
from promptpotter.infrastructure.store.layout import sandbox_owner_path
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
    enter_instrument_mode,
    instrument_depth,
    measured_candidate,
    measured_candidate_context,
)

if TYPE_CHECKING:
    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.application.initialization.session import Session
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


def _spawn_provenance(ctx: InnerSpawnContext, round_num: int | None, query: str) -> dict[str, Any]:
    cand = measured_candidate()
    return {
        # The ASKER, not the sandbox owner: a repair fork asks while the parent still owns the sandbox.
        "outer_cycle_id": ctx.asking_cycle_id,
        # A cycle_id is shared by every campaign minted from one origin: alone it is half an identity.
        "outer_campaign_id": ctx.spawn_campaign_id,
        "round": round_num,
        "candidate_idx": cand.idx if cand else None,
        "candidate_id": cand.candidate_id if cand else None,
        "candidate_label": (
            cand.label if cand else (candidate_label(0, 0) if round_num == 0 else None)
        ),
        "role": cand.role.value if cand else None,
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
def _inner_narrative(result: CycleResult, spec: InnerTaskSpec) -> str:
    """Authored under ``TRANSCRIPT_REASONING_CAP``, so the outer transcripts render never clips it."""
    if (floor := floor_reason(result)) is not None:
        return (
            f"INNER {spec.inner_dataset} seed-{spec.seed}: {floor} — scored at the floor "
            f"(optimizer-prompt-owned); stop={result.stop_reason}."
        )
    assert result.origin_level is not None
    origin = result.origin_level
    levels = result.round_levels
    # `parent_level_series`, not `levels`: the law averages over the round BUDGET, not the rounds run.
    series = parent_level_series(result)
    mean = sum(series) / len(series)
    lines = [
        f"INNER {spec.inner_dataset} seed-{spec.seed}: origin {origin:+.2f}"
        f" -> mean-over-rounds D{mean - origin:+.3f} (the scored lift)"
        f", ended {levels[-1]:+.2f} (D{levels[-1] - origin:+.3f}), peak {max(levels):+.2f}"
        f" over {result.n_rounds_after_origin} of {len(series)} rounds; stop={result.stop_reason}.",
        _lift_shape(result),
    ]
    by_round = {rnd.round: rnd for rnd in result.rounds}
    critiques = {r: rnd.optimizer_state.payload.feedback() for r, rnd in by_round.items()}
    highlight = next(
        (
            h
            for r in sorted(by_round)
            if (c := critiques.get(r))
            for h in c.get("failure_highlights") or []
            if h.strip()
        ),
        None,
    )
    if highlight:
        lines.append(f"saw: {_clip(highlight, 200)}")
    for r in sorted(by_round):
        if r == 0:
            continue
        rnd = by_round[r]
        parts = []
        if 0 <= r - 1 < len(levels):
            parts.append(f"level {levels[r - 1]:.3f} (D{levels[r - 1] - origin:+.3f})")
        steer = critiques.get(r - 1)
        if steer and steer.get("priority_fix"):
            parts.append(f"steer: {_clip(steer['priority_fix'], 130)}")
        scored = [c for c in rnd.candidate_scores if c.outcome is not ArmOutcome.INVALID]
        if scored:
            # Lift over the MATCHED parent only: an arm that never covered its panel has no lift.
            floors = {
                c.label: c.vs_reference.reference_level("fitness") if c.vs_reference else None
                for c in scored
            }
            margins = {
                c.label: float("-inf")
                if (matched := floors[c.label]) is None or c.accuracy is None
                else c.accuracy - matched
                for c in scored
            }
            top = max(scored, key=lambda c: (margins[c.label], c.composite_fitness))
            reference = floors[top.label]
            theta = (
                f", th {top.theta:.2f}+/-{top.theta_se:.2f}"
                if top.theta is not None and top.theta_se is not None
                else ""
            )
            versus = (
                f" vs matched-parent {reference:.3f}"
                if reference is not None
                else " (stopped before it covered the origin's samples, so nothing to compare)"
            )
            parts.append(
                f"tried {top.label} (acc {fmt_pct(top.accuracy, '{:.3f}')}{versus}{theta}): "
                f"{_clip(top.changes_description, 100)}"
            )
        else:
            parts.append("no scored candidates")
        collapses = collapse_counts(c.validation_failures for c in rnd.candidate_scores)
        anomalies = [
            f"{tag} x{n}"
            for tag, n in (
                ("no-op", collapses.get("no_op_variant", 0)),
                ("dup", collapses.get("duplicate_variant", 0)),
                ("repeat", collapses.get("repeat_variant", 0)),
            )
            if n
        ]
        if anomalies:
            parts.append(", ".join(anomalies))
        lines.append(f"R{r} " + " | ".join(parts))
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


def inner_campaign_id(
    spec: InnerTaskSpec,
    overrides: dict[str, dict[str, Any]],
    role: MeasurementRole = MeasurementRole.PANEL,
) -> str:
    """Content, never asker: the TREATMENT, never the budget, so a deepened cell continues the rounds already banked."""
    purpose = "backfill" if role is MeasurementRole.BACKFILL else "own"
    digest = stable_hash([spec.treatment(), resolved_overrides(overrides), purpose], length=6)
    return f"{spec.inner_dataset}__{digest}"


class _InnerCell(NamedTuple):
    """Resolved in the OUTER task, which alone carries the role."""

    ctx: InnerSpawnContext
    spec: InnerTaskSpec
    overrides: dict[str, dict[str, Any]]
    role: MeasurementRole
    campaign_id: str


def _resolve_inner_cell(sample: Sample, payload: dict[str, Any]) -> _InnerCell:
    ctx = inner_spawn_context()
    if ctx is None:
        raise RuntimeError(
            "promptpotter connector: no inner-spawn context published — "
            "run_optimization must call publish_inner_spawn_context first."
        )
    spec = resolve_inner_task(_cells(ctx), sample)
    overrides = payload.get("optimizer_prompt_overrides") or {}
    role = cand.role if (cand := measured_candidate()) else MeasurementRole.PANEL
    return _InnerCell(ctx, spec, overrides, role, inner_campaign_id(spec, overrides, role))


def _sandbox_stores(ctx: InnerSpawnContext) -> Stores:
    """Only campaign STATE is sandboxed; the caches stay on the real tenant tree via ``shared_root``."""
    return build_stores(
        ctx.identity, projects_root=ctx.inner_sandbox_root, shared_root=ctx.shared_root
    )


def _banked_inner_rounds(store: CampaignStore, root: CycleHop) -> int:
    index = store.load(store.line_holder(root))
    return len(index.rounds) if index else 0


def inner_cell_envelope_s(sample: Sample, payload: dict[str, Any]) -> float:
    """``max(1, …)`` grants a fully-banked cycle the round it needs to replay and finalize."""
    cell = _resolve_inner_cell(sample, payload)
    store = _sandbox_stores(cell.ctx).campaigns
    campaign = store.load_campaign(cell.campaign_id)
    banked = _banked_inner_rounds(store, campaign.root_hop) if campaign else 0
    return OUTER_SAMPLE_WALL_S_PER_ROUND * max(1, (cell.spec.n_rounds + 1) - banked)


def _open_inner_campaign(
    session: Session,
    campaign_config: CampaignConfig,
    train_data: list[Sample],
    *,
    campaign_id: str,
) -> None:
    """Continues unless something is LIVE on it: ``stop_reason_outcome`` governs scoring, never resumption."""
    plan = resolve_cycle_plan(session, campaign_config, train_data)
    store = session.store.campaigns
    root = CycleHop(campaign_id=campaign_id, cycle_id=plan.cycle_id)
    if store.load(root) is None:
        prepare_fresh_cycle(session, campaign_config, train_data, campaign_id=campaign_id, arm=None)
        return

    # A rebase retires the root under `superseded_by`; the banked trajectory is its successor's.
    hop = store.line_holder(root)
    existing = store.load(hop)
    if existing is None:
        raise CellUnscoreableError(
            f"its campaign {campaign_id} names successor {hop.cycle_id}, which has no index",
            spent={},
        )
    run = derive_run_state(store.cycle_dir(hop))
    if not run.resumable:
        raise CellUnscoreableError(
            f"its campaign {campaign_id}/{hop.cycle_id} reads {run.run_phase} — another producer "
            "owns it, and two runs writing one cycle is not a measurement",
            spent={},
        )

    session.campaign_id = campaign_id
    session.state.cycle_id = hop.cycle_id
    save_active_pointer(session.store.base_dir, hop)
    logger.info(
        "inner campaign %s/%s CONTINUES from %d banked round record(s) (was %s)",
        campaign_id,
        hop.cycle_id,
        _banked_inner_rounds(store, root),
        run.run_phase,
    )


def _cells(ctx: InnerSpawnContext) -> InnerCells:
    assert ctx.cells is not None, "run init refuses an outer dataset that holds no panel"
    return ctx.cells


async def _run_inner_campaign(
    cell: _InnerCell,
    minted: list[CycleHop],
    spawned_by: dict[str, Any],
) -> CycleResult:
    """Runs in a FRESH task, so every ContextVar bound below is this cell's alone."""
    ctx, spec = cell.ctx, cell.spec
    set_optimizer_prompt_overrides(cell.overrides or None)

    store = _sandbox_stores(ctx)
    # Not written at `publish_inner_spawn_context`, which fires for EVERY cycle.
    write_json(
        sandbox_owner_path(ctx.inner_sandbox_root),
        {
            "tenant_id": str(ctx.identity.tenant_id),
            "campaign_id": ctx.spawn_campaign_id,
            "cycle_id": ctx.spawn_cycle_id,
        },
    )

    # The ruler is the outer round's, so every candidate reads this cell's origin at the same θ.
    enter_instrument_mode(ruler=ctx.rulers.get(spec.inner_dataset))
    # The archive stays this cycle's CACHE and is withheld as cross-run MEMORY.
    scope_memory_to_own_answers(set())

    # enable_tracing=False: cloud traces of an ephemeral measurement pile spans in process memory.
    session = await init_services(
        dataset_name=spec.inner_dataset,
        identity=ctx.identity,
        stores=store,
        enable_tracing=False,
    )
    session.control = RunControl(enclosing=ctx.enclosing)
    all_samples = session.samples
    if not all_samples:
        raise ValueError(f"inner dataset {spec.inner_dataset!r} loaded zero samples")
    n = min(max(spec.n_samples, spec.n_samples_origin or 0), len(all_samples))
    # The draw `seed-screen` chose the seeds under: drawn differently, the bank is one nobody screened.
    train_data = draw_bank(all_samples, n, spec.seed)
    bank_floor = class_floor(train_data)

    campaign_config = inner_instrument_config(
        spec,
        load_campaign_config(dict(_cells(ctx).by_dataset[spec.inner_dataset].campaign_config)),
        llm_node=session.llm_node_name(),
        n_scored=len(train_data),
    )

    _open_inner_campaign(session, campaign_config, train_data, campaign_id=cell.campaign_id)
    if session.campaign_id and session.state.cycle_id:
        session.store.campaigns.record_spawned(session.hop, SpawnedBy.model_validate(spawned_by))
        minted.append(session.hop)
    observers = build_run_observers(session=session, campaign_config=campaign_config)
    try:
        result = await run_optimization(
            train_data,
            campaign_config,
            session=session,
            observers=observers,
            # Declares no ceiling of its own: the inner cycle spends under the ROOT's book.
            limits=unadmitted_limits(
                campaign_config,
                stores=session.store,
                hop=session.hop if session.state.cycle_id else None,
                requested=LaunchLimits(),
            ),
            mode=RunMode(),
        )
    finally:
        # The optimizer LLM clients and the Langfuse SDK are process-SHARED: never shut either down.
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
    if origin_acc is not None and bank_floor is not None and bank_floor >= origin_acc:
        logger.warning(
            "inner cell %s/seed-%d MAY REWARD COLLAPSE: constant-answer floor %.3f >= this "
            "run's origin %.3f over %d rows. One pass sits inside its own error bar — re-screen "
            "the seat (`python -m promptpotter seed-screen`) before trusting the panel.",
            spec.inner_dataset,
            spec.seed,
            bank_floor,
            origin_acc,
            len(train_data),
        )
    return result


async def run_inner_cycle(sample: Sample, payload: dict[str, Any]) -> dict[str, Any]:
    query = sample.query
    depth = instrument_depth()
    if depth >= MAX_INSTRUMENT_DEPTH:
        raise RuntimeError(
            f"promptpotter connector: inner recursion is already {depth} level(s) deep "
            f"(MAX_INSTRUMENT_DEPTH={MAX_INSTRUMENT_DEPTH}); refusing to spawn another inner "
            "campaign. An inner dataset whose own backend_type is 'promptpotter' recurses "
            "without bound — check the inner_benchmark named in inner_tasks.yaml."
        )
    cell = _resolve_inner_cell(sample, payload)
    ctx, spec, campaign_id = cell.ctx, cell.spec, cell.campaign_id
    start = time.monotonic()
    minted: list[CycleHop] = []

    # Still the OUTER ledger: the inner campaign emits only to its sandbox's, and the outer reads silence.
    outer_ledger = _CYCLE_LEDGER.get()
    # Captured in the OUTER task: the inner task's context copy rebinds `_CURRENT_ROUND`.
    spawned_by = _spawn_provenance(ctx, _CURRENT_ROUND.get(), query)

    sandbox = _sandbox_stores(ctx)

    def _inner_detail() -> str | None:
        dash = served_dashboard(sandbox, minted[0]) if minted else None
        if not isinstance(dash, ServedDashboard):
            return "inner campaign starting…"
        standing = standing_run_limits(sandbox.campaigns.cycle_dir(minted[0])).rounds
        max_rounds = spec.n_rounds if standing is None else standing.max_rounds
        score = dash.bench_score
        bench = None if score is None else score.vs_origin.headline
        stands = dash.run_standing
        lift = "nothing selected" if stands is None else stands.selection_line
        if score is not None and bench is not None:
            lift += f" · bench {score.headline} lift {bench.estimate.value:+.3f}"
        return f"inner r{dash.round}/{max_rounds or '?'} · {lift}"

    # Awaited DIRECTLY, never under `asyncio.shield`/`wait`, which orphan it to keep billing.
    # The context binds no OUTER candidate, or inner optimizer calls bill under the outer cell's role.
    inner_task = asyncio.create_task(
        _run_inner_campaign(cell, minted, spawned_by),
        context=measured_candidate_context(None),
    )

    heartbeat_task = asyncio.create_task(
        # `node` is NOT an optimizer node name, nor the `terminal_node` stamp below: do not align them.
        heartbeat(
            outer_ledger,
            call_id=f"inner:{query}",
            node="inner_campaign",
            round_num=_CURRENT_ROUND.get(),
            start_monotonic=start,
            detail_fn=_inner_detail,
        )
    )
    try:
        result = await inner_task
    except asyncio.CancelledError:
        logger.warning(
            "inner cell %s abandoned; its partial campaign is at %s",
            query,
            sandbox.campaigns.cycle_dir(minted[0]) if minted else "<not yet minted>",
        )
        if not inner_task.done():
            inner_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await inner_task
        raise
    finally:
        heartbeat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat_task
    if (refused := _REFUSALS.get(result.stop_reason)) is not None:
        # The inner run spends the outer's key and ceiling, so the refusal halts the walk for every later cell.
        raise CellSendRefusedError(
            f"its inner campaign {campaign_id} stopped on {result.stop_reason}",
            category=refused,
            spent={},
        )
    # Raises `CellUnscoreableError` on every other no-evidence shape, which `measure_sample` resolves.
    proxies = compute_outer_proxies(result)
    facts = inner_cell_facts(result, campaign_id)

    data: dict[str, Any] = {
        # A summary line, not an answer to be matched: this cell carries no label, so add no hit/miss reader.
        INNER_RESULT_KEY: [f"inner:{query} D{proxies.mean_round_delta:+.3f}"],
        "reasoning_trace": _inner_narrative(result, spec),
        **proxies.model_dump(),
        # An INFRA key, NOT an `OuterSampleProxies` field: those reach the scoring formula's namespace.
        PARENT_LEVEL_SE_KEY: mean_parent_level_se(result),
        **(facts.model_dump(mode="json") if facts is not None else {}),
        # An inner campaign consumes the ENTIRE outer config, so the stamp is `InnerCells.terminal`.
        "terminal_node": _cells(ctx).terminal,
        # No `step_tokens`: every call was billed as it settled, and returning it bills the cell twice.
    }
    return {"data": data}


__all__ = ["inner_cell_envelope_s", "run_inner_cycle"]
