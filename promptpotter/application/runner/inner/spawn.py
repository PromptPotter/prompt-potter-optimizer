"""The L4 recursion arm. Instrument mode — what makes this a measurement rather than a campaign
— is declared in ``shared/instrument.py`` alone; read it before changing anything it binds."""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import logging
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.application.runner.inner.tasks import (
    InnerTaskSpec,
    inner_instrument_config,
    inner_tasks_path,
    load_inner_tasks,
    resolve_inner_task,
)
from promptpotter.application.seed_screen import class_floor, draw_bank
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.l4.proxies import (
    ADOPTED_LEVEL_SE_KEY,
    OUTER_PROXY_KEYS,
    InnerCycleUnscoreableError,
    compute_outer_proxies,
    floor_reason,
    held_levels,
    inner_cell_facts,
    mean_adopted_level_se,
)
from promptpotter.domain.phases import RunPhase
from promptpotter.domain.pipeline_schema import stable_hash
from promptpotter.infrastructure.llm.telemetry import emit_token_usage
from promptpotter.infrastructure.store.account_spend import (
    ZERO_SPEND,
    BilledSpend,
    billed_spend,
    forwarded_mark,
)
from promptpotter.infrastructure.store.campaign_store.store import CampaignStore
from promptpotter.infrastructure.store.io import read_json_optional, write_json
from promptpotter.infrastructure.store.layout import (
    CycleLayout,
    inner_sandbox_dir,
    sandbox_owner_path,
    tenant_workspace,
)
from promptpotter.shared.errors import graceful
from promptpotter.shared.instrument import (
    MAX_INSTRUMENT_DEPTH,
    MeasurementRole,
    instrument_depth,
    measured_candidate,
)

if TYPE_CHECKING:
    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.application.initialization.session import Session
    from promptpotter.domain.results import CycleResult
    from promptpotter.domain.sample import Sample
    from promptpotter.shared.identity import IdentityContext

logger = logging.getLogger(__name__)


# The outer pipeline's prediction key. `datasets/promptpotter-self/pipeline.yaml::
# nodes.l1_critique.optimizer.observation_mappings` must declare it and the proxy scalars, or
# they never reach `pipeline_data` and the outer formula scores a measurement it never got.
INNER_RESULT_KEY = "final_ranking"

# An inner cycle's budget is its ROUND budget (`max_rounds` + `lives`) and carries no spend or
# token cap by design: those trip on MEASURED token counts, which jitter run to run, making a
# truncated trajectory indistinguishable from "this optimizer prompt found nothing". The cost
# ceiling is the OUTER campaign's spend budget, which every inner dollar rolls up onto.

# Per-round wall-clock allowance for ONE outer sample. Every await inside an inner campaign is
# bounded but their SUM was not, so a 429 storm could stretch one sample across tens of minutes
# and silently truncate how many outer rounds completed. It only ever EXCLUDES a stuck sample.
OUTER_SAMPLE_WALL_S_PER_ROUND = 600.0


@dataclass(frozen=True)
class InnerSpawnContext:
    """``shared_root`` stays the REAL workspace root so ``measurements`` + ``optimizer_reuse`` remain
    tenant-global: sandboxing them re-scored every inner origin, injecting more noise than the lift."""

    inner_sandbox_root: Path
    dataset_config_dir: Path
    identity: IdentityContext
    shared_root: Path
    spawn_campaign_id: str
    spawn_cycle_id: str
    asking_cycle_id: str


_INNER_SPAWN: contextvars.ContextVar[InnerSpawnContext | None] = contextvars.ContextVar(
    "promptpotter_inner_spawn", default=None
)


def publish_inner_spawn_context(session: Session, campaign_config: CampaignConfig) -> None:
    cycle_id = session.state.cycle_id
    dataset_dir = session.dataset_config_dir
    if not cycle_id or dataset_dir is None or not session.campaign_id:
        return
    # Anchored on ``shared_root`` (the REAL workspace root, invariant across depth), never this
    # store's ``projects_root``, which inside a sandbox already IS the sandbox.
    shared_root = session.store.shared_root
    inner_root = inner_sandbox_dir(
        shared_root,
        session.store.tenant_id,
        CycleHop(campaign_id=session.campaign_id, cycle_id=cycle_id),
    )
    _verify_outer_panel_contract(session, campaign_config, Path(dataset_dir))
    _INNER_SPAWN.set(
        InnerSpawnContext(
            inner_sandbox_root=inner_root,
            dataset_config_dir=Path(dataset_dir),
            identity=session.store.identity,
            shared_root=shared_root,
            spawn_campaign_id=session.campaign_id,
            spawn_cycle_id=cycle_id,
            asking_cycle_id=cycle_id,
        )
    )


def retarget_inner_spawn(session: Session) -> None:
    ctx = _INNER_SPAWN.get()
    cycle_id = session.state.cycle_id
    if ctx is None or not cycle_id or ctx.asking_cycle_id == cycle_id:
        return
    _INNER_SPAWN.set(replace(ctx, asking_cycle_id=cycle_id))
    logger.info(
        "inner spawn provenance now names %s; sandbox stays owned by %s",
        cycle_id,
        ctx.spawn_cycle_id,
    )


def _spawn_provenance(ctx: InnerSpawnContext, round_num: int | None, query: str) -> dict[str, Any]:
    """A work-item is (candidate × ``task``), not a candidate — the panel runs every task per
    candidate, so ``task`` is the only thing telling one candidate's spawns apart."""
    from promptpotter.domain.results import candidate_label
    from promptpotter.shared.instrument import measured_candidate

    cand = measured_candidate()
    return {
        # The ASKER, not the sandbox owner — a repair fork asks while the parent still owns
        # the sandbox, and this is the join the lineage hangs an inner run off.
        "outer_cycle_id": ctx.asking_cycle_id,
        # A cycle_id is content-addressed on its origin, so it is SHARED by every campaign
        # minted from that origin: alone it is half an identity.
        "outer_campaign_id": ctx.spawn_campaign_id,
        "round": round_num,
        "candidate_idx": cand.idx if cand else None,
        "candidate_id": cand.candidate_id if cand else None,
        "candidate_label": (
            cand.label if cand else (candidate_label(0, 0) if round_num == 0 else None)
        ),
        # WHY this cell ran. A backfill is spawned outside the round's shared order to fill a
        # paired comparison, and reading one as the candidate's own panel cell makes a repaired
        # round unreproducible.
        "role": cand.role.value if cand else None,
        "task": query,
    }


def _verify_outer_panel_contract(
    session: Session, campaign_config: CampaignConfig, dataset_dir: Path
) -> None:
    """An emitted-but-undeclared key is dropped by ``sample_measurement`` and never reaches
    ``pipeline_data``, so the what-if panel would score a term nobody measured. Fail at arm time."""
    schema = session.pipeline_schema
    panel_path = inner_tasks_path(dataset_dir)
    if not panel_path.is_file():
        return
    declared = {key for node in schema.nodes for key in node.output_keys}
    missing = [k for k in (INNER_RESULT_KEY, *OUTER_PROXY_KEYS) if k not in declared]
    if missing:
        raise ValueError(
            f"{dataset_dir.name} runs inner campaigns but its pipeline.yaml declares no "
            f"observation_mappings for {missing} — every key an inner sample emits must be "
            "declared, or it never reaches pipeline_data and the outer formula scores a "
            "measurement that was silently dropped."
        )
    # The panel (`inner_tasks.yaml`) and the round budget (`campaign.yaml::sp_budget_ttest`) are
    # ONE declaration in two files. A budget BELOW the panel narrows it silently, and under
    # `per_round_resubset` rounds then draw different cells — candidates compared on bases that
    # never matched. `_check_sp_budget_vs_dataset` warns in the other direction only.
    n_cells = len(load_inner_tasks(panel_path).tasks)
    if campaign_config.sp_budget_ttest != n_cells:
        raise ValueError(
            f"{dataset_dir.name} declares a {n_cells}-cell inner panel "
            f"({panel_path.name}) but budgets sp_budget_ttest="
            f"{campaign_config.sp_budget_ttest} per round. The outer panel is a CENSUS, not "
            "a sample: every candidate must run every cell or the comparison is not paired. "
            "Set sp_budget_ttest to the cell count, or change the panel."
        )


def _clip(text: str, cap: int) -> str:
    text = " ".join(text.split())
    if len(text) <= cap:
        return text
    return text[: cap - 1].rsplit(" ", 1)[0] + "…"


def _lift_shape(result: CycleResult) -> str:
    """Which rounds LIFTED, read off ``RoundResult.improved`` — a within-round paired verdict that
    touches neither noise term the scalar carries. Denominator is the ROUND BUDGET it divides by."""
    l1 = [rr for rr in result.rounds if rr.round > 0]
    marks = " ".join(f"r{rr.round}{'+' if rr.improved else '.'}" for rr in l1)
    if not marks:
        return "lifts: none — no L1 round closed."
    n = sum(1 for rr in l1 if rr.improved)
    budget = max(result.round_budget, len(l1))
    return f"lifts: {marks} ({n}/{budget}; target: early and often, thinning late)"


def _inner_narrative(result: CycleResult, spec: InnerTaskSpec) -> str:
    """Human-grade digest of one inner campaign — the outer loop's MODEL REASONING, riding the
    ``reasoning_trace`` infra key. Authored under ``TRANSCRIPT_REASONING_CAP`` so the render never clips."""
    # A floored cycle has no trajectory to narrate — say why it was floored instead.
    if (floor := floor_reason(result)) is not None:
        return (
            f"INNER {spec.inner_dataset} seed-{spec.seed}: {floor} — scored at the floor "
            f"(optimizer-prompt-owned); stop={result.stop_reason}."
        )
    # Narrated only for a cycle that carried evidence (`compute_outer_proxies` raised
    # otherwise). No `or 0.0`: an origin that was never scored has no level to narrate.
    assert result.origin_level is not None
    origin = result.origin_level
    levels = result.round_adopted_levels
    # Lead with `mean_round_delta`, the term the outer formula scores — a headline the generator
    # is not graded on teaches the wrong lesson, so this line moves whenever the measurand does.
    # `held_levels`, not `levels`: the law averages over the round BUDGET, and dividing by the
    # rounds that ran would disagree with the score.
    held = held_levels(result)
    mean = sum(held) / len(held)
    lines = [
        f"INNER {spec.inner_dataset} seed-{spec.seed}: origin {origin:+.2f}"
        f" -> mean-over-rounds D{mean - origin:+.3f} (the scored lift)"
        f", ended {levels[-1]:+.2f} (D{levels[-1] - origin:+.3f}), peak {max(levels):+.2f}"
        f" over {result.n_l1_rounds} of {len(held)} rounds; stop={result.stop_reason}.",
        _lift_shape(result),
    ]
    by_round = {rnd.round: rnd for rnd in result.rounds}
    highlight = next(
        (
            h
            for r in sorted(by_round)
            if (c := by_round[r].critique)
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
        prior = by_round.get(r - 1)
        if prior is not None and prior.critique and prior.critique.get("priority_fix"):
            parts.append(f"steer: {_clip(prior.critique['priority_fix'], 130)}")
        scored = [c for c in rnd.candidate_scores if not c.invalid]
        if scored:
            # Rank by lift over the MATCHED origin, and never invent the comparison where there
            # is none: `accuracy - 0.0` hands an arm that never covered the origin's panel its
            # whole accuracy as lift, floating the shortest prefix to the top of the very
            # sentence the outer optimizer learns from.
            top = max(
                scored,
                key=lambda c: (
                    c.accuracy - c.matched_parent_accuracy
                    if c.matched_parent_accuracy is not None
                    else float("-inf"),
                    c.composite_fitness,
                ),
            )
            theta = (
                f", th {top.theta:.2f}+/-{top.theta_se:.2f}"
                if top.theta is not None and top.theta_se is not None
                else ""
            )
            versus = (
                f" vs matched-origin {top.matched_parent_accuracy:.3f}"
                if top.matched_parent_accuracy is not None
                else " (stopped before it covered the origin's samples, so nothing to compare)"
            )
            parts.append(
                f"tried {top.label} (acc {top.accuracy:.3f}{versus}{theta}): "
                f"{_clip(top.changes_description, 100)}"
            )
        else:
            parts.append("no scored candidates")
        anomalies = [
            f"{tag} x{n}"
            # `repeat` is the anomaly the OUTER generator most needs: the inner loop stopped
            # forming new hypotheses, which is an optimizer prompt defect, not task difficulty.
            for tag, n in (
                ("no-op", rnd.l1_n_no_op),
                ("dup", rnd.l1_n_duplicate),
                ("repeat", rnd.l1_n_repeat),
            )
            if n
        ]
        if anomalies:
            parts.append(", ".join(anomalies))
        lines.append(f"R{r} " + " | ".join(parts))
    # Drop the EARLIEST round lines first — the trajectory's tail is the informative end, and
    # the panel's head-keep clip would silently cut the latest rounds instead.
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
    """Content, never asker: the cell and the optimizer prompts it runs under ARE its identity, so a
    re-measured cell continues the rounds a previous attempt banked. ``cycle_id`` collides across candidates."""
    purpose = "backfill" if role is MeasurementRole.BACKFILL else "own"
    digest = stable_hash([spec.model_dump(mode="json"), overrides, purpose])[:6]
    return f"{spec.inner_dataset}__{digest}"


def _banked_inner_rounds(ctx: InnerSpawnContext, campaign_id: str) -> int:
    """Read in the OUTER task before the inner campaign is spawned — the wall-clock deadline is the
    outer's to set, and a continued cell must be budgeted over the rounds that REMAIN."""
    indexes = ctx.inner_sandbox_root.glob(f"*/campaigns/{campaign_id}/cycles/*/index.json")
    return max(
        (len((read_json_optional(p) or {}).get("rounds") or []) for p in indexes),
        default=0,
    )


def _forward_inner_spend(
    ctx: InnerSpawnContext,
    campaign_id: str,
    spec: InnerTaskSpec,
    start: float,
    cycle_dir_box: dict[str, Path],
) -> None:
    """Roll what this cell has spent since the last forward onto the OUTER ledger, then raise its
    high-water mark. Runs at teardown on EVERY terminal outcome, because a cell that dies has still
    spent its money; carries a DELTA, because a continued cell's own rollup is cumulative across
    attempts and billing that whole would charge its history again.

    Emit-then-mark is the crash policy: interrupted between the two, the next attempt re-forwards
    and the run halts early, which is recoverable. The other order loses the money silently."""
    if "dir" not in cycle_dir_box:
        # This caller never opened the campaign — it was refused because ANOTHER producer owns it,
        # or it died before the mint. Forwarding here would bill that producer's in-flight spend to
        # this sample and raise a mark it did not earn.
        return
    store = CampaignStore(tenant_workspace(ctx.inner_sandbox_root, str(ctx.identity.tenant_id)))
    pending: list[tuple[CycleHop, BilledSpend]] = []
    total = ZERO_SPEND
    for cycle_dir in store.campaign_cycle_dirs(campaign_id):
        held = billed_spend([CycleLayout(cycle_dir).ledger])
        delta = held.since(forwarded_mark(cycle_dir))
        if delta == ZERO_SPEND:
            continue
        pending.append((CycleHop(campaign_id=campaign_id, cycle_id=cycle_dir.name), held))
        total = total.plus(delta)
    if not pending:
        return
    # `l1_critique` names no call this made — a whole campaign ran here. It is the node the outer
    # sample's `step_timings` already keys, so the two spend surfaces agree on one attribution.
    emit_token_usage(
        node="l1_critique",
        kind="backend",
        input_tokens=total.input_tokens,
        output_tokens=total.output_tokens,
        cache_read_tokens=total.cache_read_tokens,
        cache_write_tokens=total.cache_write_tokens,
        duration_s=time.monotonic() - start,
        model=f"inner:{spec.inner_dataset}",
        cost_usd=total.used_usd,
    )
    for hop, held in pending:
        store.mark_spend_forwarded(hop, held)


def _open_inner_campaign(
    session: Session,
    campaign_config: CampaignConfig,
    train_data: list[Sample],
    *,
    campaign_id: str,
) -> int:
    """Continue unless something is LIVE on it — every terminal class resumes, reaped included.
    ``stop_reason_outcome`` governs scoring (``domain/l4/proxies.py``), never resumption."""
    from promptpotter.application.jobs.mint import prepare_fresh_cycle, resolve_cycle_plan
    from promptpotter.infrastructure.runtime_flags import derive_run_phase
    from promptpotter.infrastructure.store.session_pointer import save_active_pointer

    plan = resolve_cycle_plan(session, campaign_config, train_data)
    store = session.store.campaigns
    existing = store.load(CycleHop(campaign_id=campaign_id, cycle_id=plan.cycle_id))
    if existing is None:
        prepare_fresh_cycle(session, campaign_config, train_data, campaign_id=campaign_id)
        return 0

    phase = derive_run_phase(
        store.cycle_dir(CycleHop(campaign_id=campaign_id, cycle_id=plan.cycle_id)),
        is_terminal=bool(existing.get("finished_at")),
    )
    if phase in (RunPhase.RUNNING, RunPhase.GATE, RunPhase.CHECKIN):
        raise InnerCycleUnscoreableError(
            f"its campaign {campaign_id}/{plan.cycle_id} reads {phase} — another producer "
            "owns it, and two runs writing one cycle is not a measurement"
        )
    session_id = str(existing.get("parent_session_id") or "")
    if not session_id:
        raise InnerCycleUnscoreableError(
            f"its campaign {campaign_id}/{plan.cycle_id} names no parent session, so there "
            "is no session record to continue under"
        )

    session.session_id = session_id
    session.campaign_id = campaign_id
    session.state.cycle_id = plan.cycle_id
    save_active_pointer(
        session.store.base_dir,
        session_id,
        CycleHop(campaign_id=campaign_id, cycle_id=plan.cycle_id),
    )
    banked = len(existing.get("rounds") or [])
    logger.info(
        "inner campaign %s/%s CONTINUES from %d banked round record(s) (was %s)",
        campaign_id,
        plan.cycle_id,
        banked,
        phase,
    )
    return banked


async def _run_inner_campaign(
    ctx: InnerSpawnContext,
    spec: InnerTaskSpec,
    optimizer_prompt_overrides: dict[str, dict[str, Any]],
    cycle_dir_box: dict[str, Path],
    spawned_by: dict[str, Any],
    spawn_role: MeasurementRole,
) -> CycleResult:
    """Runs in a FRESH task, so the per-task ContextVars are isolated. ``.spend`` is captured from
    live state rather than the sandbox's debounced ``dashboard.json``, which would race."""
    # Lazy: `run_optimization` would be an import cycle — `entry.py` imports
    # `publish_inner_spawn_context` from here.
    from promptpotter.application.campaign_config import load_campaign_config
    from promptpotter.application.datasets.authored import (
        dataset_campaign_path,
        read_campaign_config_file,
    )
    from promptpotter.application.initialization.wiring import init_services
    from promptpotter.application.optimization.dispatch.llm_call.prompts import (
        set_optimizer_prompt_overrides,
    )
    from promptpotter.application.run_observers import build_run_observers
    from promptpotter.application.runner.entry import RunMode, run_optimization
    from promptpotter.infrastructure.store.archive_views import capture_evidence_epoch
    from promptpotter.infrastructure.store.stores import build_stores
    from promptpotter.shared.instrument import enter_instrument_mode

    # Set in THIS task's context copy, so they cannot reach the outer's optimizer. The SPECIMEN
    # under test, not part of the instrument — the same channel carries a normal outer cycle's
    # own optimizer prompt SET, so it is not mode-gated.
    set_optimizer_prompt_overrides(optimizer_prompt_overrides or None)

    # Only campaign STATE is sandboxed. The content-addressed caches stay on the REAL tenant
    # tree via `shared_root`: a hit there is the same measurement by content hash, and
    # re-scoring it would re-pay for it AND redraw its stochastic value under the outer fitness.
    store = build_stores(
        ctx.identity,
        projects_root=ctx.inner_sandbox_root,
        shared_root=ctx.shared_root,
    )
    # The directory name is a hash, so this file is the only place the three owner names
    # survive for a human or the orphan reaper. Written here rather than at
    # `publish_inner_spawn_context`, which fires for EVERY cycle and would mint empty sandboxes.
    write_json(
        sandbox_owner_path(ctx.inner_sandbox_root),
        {
            "tenant_id": str(ctx.identity.tenant_id),
            "campaign_id": ctx.spawn_campaign_id,
            "cycle_id": ctx.spawn_cycle_id,
        },
    )

    # THE declaration: this cycle is a measurement instrument, not a campaign. ONE call binds
    # every hermetic property in this task's context copy, so none can be forgotten piecemeal by
    # a future code path. The clamp's seed is the cell's, matching the target model's, so every
    # candidate for a cell shares one random stream (CRN).
    clamp = (
        None
        if spec.inner_optimizer_temperature is None
        else {"temperature": spec.inner_optimizer_temperature, "seed": spec.seed}
    )
    enter_instrument_mode(
        evidence_epoch=capture_evidence_epoch(store),
        optimizer_clamp=clamp,
    )

    # enable_tracing=False: cloud traces for an ephemeral fitness measurement piled
    # payload-bearing spans in the logger's _trace_metadata until the process was OOM-killed.
    # The local FileSink still records inner traces to disk.
    session = await init_services(
        dataset_name=spec.inner_dataset,
        identity=ctx.identity,
        stores=store,
        enable_tracing=False,
    )
    all_samples = session.samples
    if not all_samples:
        raise ValueError(f"inner dataset {spec.inner_dataset!r} loaded zero samples")
    n = min(max(spec.n_samples, spec.n_samples_origin or 0), len(all_samples))
    # Spelled once, in the screen that CHOOSES seeds — a runner drawing differently would run a
    # bank nobody screened, and nothing reports the divergence.
    train_data = draw_bank(all_samples, n, spec.seed)
    # Computed here because this is the first moment the drawn rows exist. `seed-screen` owns
    # the disqualifier but is hand-run, so nothing recomputes it for the seats actually seated.
    bank_floor = class_floor(train_data)

    file_config: dict[str, Any] = {}
    if session.dataset_config_dir is not None:
        cfg_path = dataset_campaign_path(session.dataset_config_dir)
        if cfg_path.exists():
            file_config = read_campaign_config_file(cfg_path)
    profile = session.store.backends.load_connector_profile(session.backend_id) or {}
    campaign_config = inner_instrument_config(
        spec,
        load_campaign_config({**profile, **file_config}),
        llm_node=session.llm_node_name(),
        n_scored=len(train_data),
    )

    # A retry must not orphan the rounds the previous attempt banked.
    _open_inner_campaign(
        session,
        campaign_config,
        train_data,
        campaign_id=inner_campaign_id(spec, optimizer_prompt_overrides, spawn_role),
    )
    if session.campaign_id and session.state.cycle_id:
        # An L4-only fact, so it stays out of the generic mint seam every campaign shares. The
        # cycle index is a raw dict, so an older cycle simply has no `spawned_by`.
        session.store.campaigns.update(session.hop, {"spawned_by": spawned_by})
        # Publish the minted dir so the outer heartbeat's detail_fn can tail this dashboard.
        cycle_dir_box["dir"] = session.store.campaigns.cycle_dir(session.hop)
    observers = build_run_observers(
        session=session,
        campaign_config=campaign_config,
        dataset=train_data,
        display=None,
        resumed_from_round=None,
        origin_accuracy=0.0,
    )
    try:
        result = await run_optimization(
            train_data,
            campaign_config,
            session=session,
            observers=observers,
            # No `spend_budget_usd=`: that argument is the RUN-SCOPED override, and restating the
            # config's own value through it is a second spelling of the same cap. Omitted, the
            # inner cycle is bound by `campaign_config.optimization.spend_budget_usd` directly.
            mode=RunMode(),
        )
    finally:
        # One process runs dozens of inner campaigns back-to-back, so anything holding an OS
        # handle or a large payload piles up until the process is OOM-killed with no traceback.
        # The optimizer LLM clients and the Langfuse SDK are process-SHARED, so neither may be
        # shut down here — that would break every later campaign.
        with contextlib.suppress(Exception):
            await session.backend_client.aclose()
        if session.langfuse is not None:
            with contextlib.suppress(Exception):
                session.langfuse.reset()
    # A bank paying MORE for answering one label than for reasoning cannot measure an optimizer
    # prompt. REPORTED, never enforced: one origin pass sits inside its own error bar, so
    # rejecting a seat on it is the single-pass error the screen itself stopped making.
    if bank_floor >= result.origin_accuracy:
        logger.warning(
            "inner cell %s/seed-%d MAY REWARD COLLAPSE: constant-answer floor %.3f >= this "
            "run's origin %.3f over %d rows. One pass sits inside its own error bar — re-screen "
            "the seat (`python -m promptpotter seed-screen`) before trusting the panel.",
            spec.inner_dataset,
            spec.seed,
            bank_floor,
            result.origin_accuracy,
            len(train_data),
        )
    return result


async def run_inner_cycle(query: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Projects the three proxy metrics onto the ``{"data": {…}}`` shape ``measure_sample`` parses
    from an HTTP body, so the outer scorer reads an inner result identically to a remote one."""
    ctx = _INNER_SPAWN.get()
    if ctx is None:
        raise RuntimeError(
            "promptpotter connector: no inner-spawn context published — "
            "run_optimization must call publish_inner_spawn_context first."
        )
    depth = instrument_depth()
    if depth >= MAX_INSTRUMENT_DEPTH:
        raise RuntimeError(
            f"promptpotter connector: inner recursion is already {depth} level(s) deep "
            f"(MAX_INSTRUMENT_DEPTH={MAX_INSTRUMENT_DEPTH}); refusing to spawn another inner "
            "campaign. An inner dataset whose own backend_type is 'promptpotter' recurses "
            "without bound — check the inner_benchmark named in inner_tasks.yaml."
        )
    spec = resolve_inner_task(ctx, query)
    overrides = payload.get("optimizer_prompt_overrides") or {}

    # A FAILED outcome surfaces as a returned ``stop_reason``; the runner does not raise, so it
    # is classified below with every other no-evidence shape rather than caught here. A run that
    # merely failed to improve is a SUCCESS with poor proxies — measured, so a bad mutation is
    # still penalised. `spawn_role` is captured in the OUTER task, like `spawned_by` below.
    spawn_role = cand.role if (cand := measured_candidate()) else MeasurementRole.PANEL
    campaign_id = inner_campaign_id(spec, overrides, spawn_role)
    start = time.monotonic()
    # Filled by the inner task once the campaign is open; the forwarder reads it as the proof that
    # this caller — not a concurrent one — owns what the sandbox holds.
    cycle_dir_box: dict[str, Path] = {}
    try:
        return await _measure_inner_cell(
            ctx, spec, query, campaign_id, overrides, spawn_role, start, cycle_dir_box
        )
    finally:
        # Every exit — measured, unscoreable, cancelled — spent the money either way. `graceful`
        # keeps a forwarding failure from replacing the outcome the caller is already carrying.
        with graceful(f"could not forward inner spend for {campaign_id}"):
            _forward_inner_spend(ctx, campaign_id, spec, start, cycle_dir_box)


async def _measure_inner_cell(
    ctx: InnerSpawnContext,
    spec: InnerTaskSpec,
    query: str,
    campaign_id: str,
    overrides: dict[str, dict[str, Any]],
    spawn_role: MeasurementRole,
    start: float,
    cycle_dir_box: dict[str, Path],
) -> dict[str, Any]:
    # This runs in the OUTER task, so ``_CYCLE_LEDGER`` still holds the outer ledger. The
    # heartbeat below appends to it, because the inner campaign emits only to its OWN sandbox
    # ledger and the outer surfaces would read the silence as a vanished producer.
    from promptpotter.application.optimization.dispatch.llm_call.heartbeat import heartbeat
    from promptpotter.infrastructure.llm.telemetry import _CURRENT_ROUND, _CYCLE_LEDGER

    outer_ledger = _CYCLE_LEDGER.get()
    # Captured in the OUTER task and handed over explicitly: the inner task gets a COPY of this
    # context and rebinds `_CURRENT_ROUND`, so a read over there attributes the campaign to
    # itself.
    spawned_by = _spawn_provenance(ctx, _CURRENT_ROUND.get(), query)

    def _inner_detail() -> str | None:
        cycle_dir = cycle_dir_box.get("dir")
        if cycle_dir is None:
            return "inner campaign starting…"
        dash = read_json_optional(CycleLayout(cycle_dir).dashboard)
        if not dash:
            return "inner campaign starting…"
        rnd = dash.get("round")
        best = dash.get("best")
        max_rounds = (dash.get("run_limits") or {}).get("max_rounds")
        # The SERVED ``ability_delta``, the same number the webapp headline reads, so the two
        # surfaces cannot disagree. It is LOGITS, not a fraction — printed as `%` it read
        # `Δ+19%` off a cell whose ability never moved.
        delta = dash.get("ability_delta")
        if isinstance(delta, int | float) and isinstance(best, int | float):
            lift = f"Δθ{delta:+.2f} (best measured {best:.0%})"
        elif isinstance(best, int | float):
            lift = f"best measured {best:.0%}"
        else:
            lift = "best —"
        return f"inner r{rnd if rnd is not None else '?'}/{max_rounds or '?'} · {lift}"

    inner_task = asyncio.create_task(
        _run_inner_campaign(ctx, spec, overrides, cycle_dir_box, spawned_by, spawn_role)
    )
    # The ONE bound on this sample's total wall clock. Awaiting `inner_task` DIRECTLY makes it
    # this coroutine's `_fut_waiter`, so the cancellation propagates and the campaign really
    # stops: never wrap it in `asyncio.shield` or `asyncio.wait`, both of which orphan it to
    # keep calling the optimizer and billing tokens against a sample nobody will read.
    # Budget the rounds that REMAIN — charging a continued cell the full budget for its tail
    # leaves the wall bounding almost nothing. `max(1, …)` grants a fully-banked cycle the one
    # round it needs to replay its priors and finalize.
    banked = _banked_inner_rounds(ctx, campaign_id)
    deadline_s = OUTER_SAMPLE_WALL_S_PER_ROUND * max(1, (spec.n_rounds + 1) - banked)
    # Constructed here, not inline in the `async with`, so the suspend hook below can close over
    # it: `asyncio.timeout` fixes its absolute `when` at CALL time.
    deadline = asyncio.timeout(deadline_s)

    def _give_back_suspended_time(overshoot: float) -> None:
        """The deadline bounds how long this cell may SPEND, and a suspended machine spends nothing —
        without this an overnight sleep hands the next tick a budget that expired while nothing ran."""
        when = deadline.when()
        if when is None:  # pragma: no cover — only for `timeout(None)`, never used here
            return
        try:
            deadline.reschedule(when + overshoot)
        except RuntimeError:
            # The `async with` already exited; nothing left to extend.
            return
        logger.warning(
            "inner cell %s: machine suspended ~%.0fs mid-campaign; extending the "
            "%.0fs wall-clock deadline by that much rather than charging it as work",
            query,
            overshoot,
            deadline_s,
        )

    heartbeat_task = asyncio.create_task(
        # NOT an optimizer node name: a whole campaign runs here, so naming it after one node
        # charges that node with the entire wall clock and a healthy run reads as a hang. The
        # `step_timings`/`step_tokens` keying below answers a different question (spend
        # attribution) — do not re-align this display label to it.
        # Created UNCONDITIONALLY, even with no ledger to append to: this task also carries the
        # deadline's suspend guard, and gating it on a telemetry sink would disarm that guard.
        heartbeat(
            outer_ledger,
            call_id=f"inner:{query}",
            node="inner_campaign",
            round_num=_CURRENT_ROUND.get(),
            start_monotonic=start,
            detail_fn=_inner_detail,
            on_suspend=_give_back_suspended_time,
        )
    )
    # ``None`` once the deadline has bitten — the one state both exits below funnel into, so
    # the guard has a single answer to "is there a measurement here" and a single raise.
    result: CycleResult | None = None
    try:
        try:
            async with deadline:
                result = await inner_task
            # ``asyncio.timeout`` raises only if a CancelledError comes back up, so anything in
            # the inner chain answering a cancellation with a normal return makes the guard
            # vanish and an over-deadline campaign scores as a real measurement. Ask the clock.
            if deadline.expired():
                result = None
        except TimeoutError:
            result = None
    finally:
        # A campaign that outlived its deadline without answering the cancellation is still
        # billing tokens against a sample nobody will read. Insist.
        if result is None and not inner_task.done():
            inner_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await inner_task
        # Whether the inner run returned or raised — an in-flight heartbeat would otherwise keep
        # appending against a finished sample.
        heartbeat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat_task
    if result is None:
        # The outer ERROR row names only the cell, so without this the abandoned campaign —
        # holding real banked rounds — is unfindable from either surface.
        logger.warning(
            "inner cell %s abandoned at its %.0fs wall-clock deadline; its partial campaign "
            "is at %s",
            query,
            deadline_s,
            cycle_dir_box.get("dir", "<not yet minted>"),
        )
        raise InnerCycleUnscoreableError(
            f"it ran past its {deadline_s:.0f}s wall-clock deadline "
            f"({max(1, (spec.n_rounds + 1) - banked)} round(s) still to run of "
            f"{spec.n_rounds} + origin, {banked} already banked, at "
            f"{OUTER_SAMPLE_WALL_S_PER_ROUND:.0f}s each) and was cancelled"
        )
    elapsed = time.monotonic() - start
    # No exclusion decision here: `compute_outer_proxies` raises `InnerCycleUnscoreableError`,
    # which `measure_sample`'s catch-all turns into this sample's EXCLUDED row.
    proxies = compute_outer_proxies(result)
    facts = inner_cell_facts(result, campaign_id)

    data: dict[str, Any] = {
        # The connector's `_extract_experiment` sets `ground_truth` to the same `inner:{query}`
        # prefix — keep the two in sync. The outcome suffix means the two can never be EQUAL, so
        # every outer sample reads as a MISS. Nothing that grades reads it that way (the outer
        # score is `fitness`, and a hit at `>= 1.0` is unreachable under this campaign's formula)
        # — but the failure PANELS did, and a critique then diagnosed the artifact as a real
        # defect. They now ask `panels._miss_is_placeholder` and stay silent instead; do not
        # reintroduce a hit/miss reader here without teaching it that predicate.
        INNER_RESULT_KEY: [f"inner:{query} D{proxies.mean_round_delta:+.3f}"],
        # The outer loop's raw evidence, rendered as MODEL REASONING in its transcripts panel.
        "reasoning_trace": _inner_narrative(result, spec),
        **proxies.model_dump(),
        # An INFRA key, deliberately NOT an `OuterSampleProxies` field: those reach the scoring
        # formula's namespace, and a standard error inside the formula is one keystroke from the
        # `mean - λ·se` haircut the spec forbids. Precision travels beside the measurement; it
        # never grades it.
        ADOPTED_LEVEL_SE_KEY: mean_adopted_level_se(result),
        # The rest of what this seed knows about itself, as numbers rather than as the sentence
        # in `reasoning_trace`. Same infra slot and the same rule: they travel beside the
        # measurement and never grade it. `None` on a floored cell, whose keys are then absent.
        **(facts.model_dump() if facts is not None else {}),
        # The archive's reuse contract: a named node means "this outcome depends on config only
        # UP TO that node". An inner campaign consumes the ENTIRE outer config at once, so the
        # only honest stamp is the LAST node of the outer chain — anything earlier lets a
        # candidate editing a later node silently replay the origin's rows.
        "terminal_node": "l3_plan",
        "total_time": elapsed,
        "step_timings": {"l1_critique": elapsed},
        # No `step_tokens`: spend rides `_forward_inner_spend` at teardown instead. Returning it
        # HERE would bill only the cells that succeed, bill a continued cell's whole history
        # again, and re-bill the lot whenever the archive replays this row.
    }
    return {"data": data}


__all__ = ["InnerSpawnContext", "publish_inner_spawn_context", "run_inner_cycle"]
