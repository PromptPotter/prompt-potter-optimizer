"""L4 inner-cycle runner — the recursion arm of the ``promptpotter`` connector.

The ``promptpotter`` connector declares ``execution="in_process"``; its
``in_process_run`` delegates here. One outer "sample" = one inner PromptPotter
campaign on a cheap proxy benchmark, scored by **how much the inner loop
improved** — a composed fitness (``compute_outer_proxies``) over endpoint deltas,
normalized headroom, bounded quality (cleanliness / diversity), and efficiency
ratios (lift per $/candidate/second). Decided in
``docs/specs/l4-outer-loop.md`` § 2 + § 4.

Two isolations make the recursion safe **and re-entrant** (so L5+ nests by
construction — never a depth-1 assumption):

- **Own ``asyncio.Task`` per inner cycle.** The per-task ContextVars the runner
  binds (``_CYCLE_LEDGER`` / ``_CURRENT_ROUND`` in ``infrastructure/llm/models``;
  ``_ABORT_CHECK`` in ``rate_limit``) isolate per task, not per call — a naïve
  nested ``await run_optimization`` in the outer's own task would clobber the
  outer's ledger binding / round stamp / abort predicate. We spawn a fresh task,
  which copies the context, so each level gets its own copies.
- **Sandboxed stores in a flat, shallow per-cycle home** —
  ``<workspace>/.inner/<spawn_cycle_id>`` (sibling of ``projects/``, NOT physically
  nested under the deep outer cycle dir). The inner campaign's ``cycles/`` tree,
  ledger, active-pointer, and dashboards live there, so they never touch the outer
  campaign's listing / active pointer / SSE stream. The home is named by (owned by)
  the spawning cycle, but kept flat because physical nesting
  (``…/.runtime/inner/…/.runtime/inner/…``) blows past Windows' 260-char
  ``MAX_PATH`` at depth 1 and is hopeless by L5. A flat registry stays shallow at
  EVERY depth, so the re-entrancy invariant holds without the path-length trap.

What makes the inner cycle a *measurement* rather than a campaign is declared in ONE place —
:func:`~promptpotter.shared.instrument.enter_instrument_mode`, called inside the inner task.
It binds recursion depth, the evidence epoch (the archive stays shared as a content-addressed
CACHE, but is hidden as cross-run MEMORY, so the instrument does not depend on how often it has
been used) and the optimizer decoding clamp, together. Read that module before changing any of
them.

The spawning cycle publishes its context (:func:`publish_inner_spawn_context`,
called from ``runner/entry.py::run_optimization`` for every cycle) so the
connector — which only receives ``(query, payload)`` — can find where to sandbox
and which inner benchmark to run. The outer L1's meta-prompt mutations ride
``payload["meta_prompt_overrides"]`` and are applied to the inner cycle's
``_optimizer/`` prompts via the per-run override ContextVar
(``dispatch/llm_call/prompts.py``), set inside the inner task so it can't leak to
the outer. Those are the SPECIMEN under test, not part of the instrument — the same
channel carries a normal outer cycle's own meta-prompt set.

The process-global rate limiter is shared: inner spend competes with the outer
for TPM/RPM (flagged, not blocked). Inner LLM cost (optimizer + backend) is
tracked in the sandbox ledger AND rolled up onto the OUTER dashboard: each inner
cycle's total spend rides its :class:`CycleResult.spend` (read from the inner
run's live dashboard state at finalize — never the debounced ``dashboard.json``,
which would race the read) and is returned as this outer sample's ``step_tokens``,
so it fans onto the outer ledger through the existing backend-cost channel
(``sample_measurement``) — the inner cost IS the outer sample's backend cost, so
"spend is the headline" holds at the outer level.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import logging
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.application.runner.inner.tasks import (
    InnerTaskSpec,
    inner_instrument_config,
    resolve_inner_task,
)
from promptpotter.domain.l4.proxies import (
    OUTER_PROXY_KEYS,
    InnerCycleUnscoreableError,
    compute_outer_proxies,
    floor_reason,
)
from promptpotter.infrastructure.store.io import read_json_optional
from promptpotter.shared.instrument import MAX_INSTRUMENT_DEPTH, instrument_depth

if TYPE_CHECKING:
    from promptpotter.application.bootstrap.session import Session
    from promptpotter.domain.results import CycleResult, CycleSpend
    from promptpotter.shared.identity import IdentityContext

logger = logging.getLogger(__name__)


# The terminal-ranker key the outer `promptpotter-self` pipeline reads as its
# prediction (a non-empty list keeps the origin round-0 health gate from halting
# on all-NO_RESULT) + the composed-fitness proxy scalars the outer scoring formula
# reads. `datasets/promptpotter-self/pipeline.json::nodes.l1_critique.optimizer
# .observation_mappings` declares these as observation keys, so they reach
# `pipeline_data` and the formula namespace (`scoring/formula/compiler.py`).
INNER_RESULT_KEY = "final_ranking"

# An inner cycle's budget is its ROUND budget — `max_rounds` (the ceiling a compounding run may
# reach) and `lives` (what stops a stalling one early). Both are DETERMINISTIC, and the outer
# proxies are defined over exactly them.
#
# It deliberately carries no spend or token cap. Those trip on MEASURED token counts, which jitter
# run to run (reasoning tokens are not reproducible) — so the same meta-prompt halted at a
# different round on different runs, and the resulting truncated trajectory is indistinguishable
# from "this meta-prompt found nothing". That made provider mood a fitness signal, and it was a
# second budget doing the same kind of work as the round budget, only nondeterministically.
# Measured on the inner cycles then on disk: 3 of 36 tripped `token_budget`, two of them
# truncating at rounds 4-5 of a 7-round budget, and every one was scored as a verdict.
#
# What still bounds an inner cycle: the round budget above; every individual await inside it
# (optimizer 180s x2, backend 120s, MAX_429_ATTEMPTS, rounds <= HARD_CAP); the wall-clock deadline
# below, which is the genuine runaway guard and EXCLUDES rather than scoring; and — for the
# operator's actual cost ceiling — the OUTER campaign's own spend budget, which every inner
# dollar rolls up onto. The cap belongs at the level where the operator set one, not inside the
# instrument.

# Per-round wall-clock allowance for ONE outer sample. Every
# individual await inside an inner campaign is already bounded (optimizer 180s x2, backend 120s,
# MAX_429_ATTEMPTS, rounds <= HARD_CAP) — but nothing bounded their SUM, so sustained 429
# throttling could stretch one sample across tens of minutes inside every one of those bounds,
# silently truncating how many outer rounds completed. Measured over the 155 inner cycles on
# disk that ran to a SUCCESS outcome: per-round p50 81s, p90 116s, max 245s (none would have
# tripped at 300s). Set to 600 so longer / slower campaigns (raised max_inner_rounds + lives, or a
# 429 storm) keep that headroom — the wall only ever EXCLUDES a genuinely stuck sample, never a
# live one; the operator's real cost ceiling is the OUTER spend budget every inner dollar rolls up.
OUTER_SAMPLE_WALL_S_PER_ROUND = 600.0


@dataclass(frozen=True)
class InnerSpawnContext:
    """What an inner cycle needs from the cycle that spawned it — published per
    cycle so the connector (which only gets ``(query, payload)``) can recurse.

    ``inner_sandbox_root`` is the SHALLOW, FLAT home for this cycle's inner
    campaigns: ``<workspace>/.inner/<spawn_cycle_id>``. It is named by (owned by)
    the spawning cycle but NOT physically nested under its deep campaign dir —
    physical nesting (``…/.runtime/inner/…/.runtime/inner/…``) blows past Windows'
    260-char ``MAX_PATH`` at depth 1, and would be hopeless at L5+. A flat registry
    stays shallow at EVERY recursion depth (an L5 cycle gets its own
    ``<workspace>/.inner/<l5_id>``), so the re-entrancy invariant holds without the
    path-length trap. Still out of the ``projects/`` tree, so inner campaigns never
    show in the outer campaign listing. ``dataset_config_dir`` is the spawning
    campaign's config dir, read for ``inner_tasks.json``; ``identity`` roots the
    sandbox stores under the same tenant.

    ``shared_root`` is the REAL workspace root, carried through so the inner store keeps
    its ``archive`` + ``optimizer_calls`` tenant-global while its campaign state stays
    sandboxed. Sandboxing those caches too meant every outer cycle re-scored every inner
    origin from scratch — and because an inner origin is stochastic, it redrew a different
    accuracy each time (observed: the same content hash on the same 24 samples scoring
    0.375 in seven sandboxes and 0.417 in two). The outer fitness subtracts that origin,
    so the isolation injected a noise term larger than the lift it was measuring.

    ``spawn_cycle_id`` is the OUTER cycle that owns this sandbox — carried explicitly
    rather than re-parsed off ``inner_sandbox_root.name``, so the provenance an inner
    campaign stamps names its parent by fact, not by string surgery on a path."""

    inner_sandbox_root: Path
    dataset_config_dir: Path
    identity: IdentityContext
    shared_root: Path
    spawn_cycle_id: str


_INNER_SPAWN: contextvars.ContextVar[InnerSpawnContext | None] = contextvars.ContextVar(
    "promptpotter_inner_spawn", default=None
)


def publish_inner_spawn_context(session: Session) -> None:
    """Publish *session*'s cycle as the spawn context for any inner recursion.

    Called once per cycle at the runner seam (``run_optimization``) for EVERY
    cycle — the runner can't know in advance whether a child will use the
    ``promptpotter`` connector, and publishing unconditionally is what keeps the
    seam connector-agnostic + re-entrant (each level publishes its own). A cycle
    with no ``cycle_id`` / ``dataset_config_dir`` yet is a no-op."""
    cycle_id = session.state.cycle_id
    dataset_dir = session.dataset_config_dir
    if not cycle_id or dataset_dir is None or not session.campaign_id:
        return
    # Flat, shallow sandbox home: the workspace's ``.inner/<cycle_id>`` (sibling of
    # ``projects/``), NOT the deep outer cycle dir — keeps the path short at any
    # recursion depth (Windows MAX_PATH). Anchored on ``shared_root`` (the REAL
    # workspace root, invariant across depth), not on this store's ``projects_root``:
    # inside a sandbox the latter already IS ``.inner/<parent>``, so an L5 would nest
    # at ``.inner/.inner/<id>`` and reintroduce the path-length trap the flat layout exists
    # to avoid. Identical for a top-level cycle, where the two roots coincide.
    shared_root = session.store.shared_root
    inner_root = shared_root.parent / ".inner" / cycle_id
    _verify_outer_observation_contract(session, Path(dataset_dir))
    _INNER_SPAWN.set(
        InnerSpawnContext(
            inner_sandbox_root=inner_root,
            dataset_config_dir=Path(dataset_dir),
            identity=session.store.identity,
            shared_root=shared_root,
            spawn_cycle_id=cycle_id,
        )
    )


def _spawn_provenance(ctx: InnerSpawnContext, round_num: int | None, query: str) -> dict[str, Any]:
    """Which outer work-item is asking for this measurement — stamped on the inner cycle.

    Without it an inner campaign is anonymous: its ``campaign_id`` is random and its
    ``cycle_id`` is a hash of its OWN origin, so nothing on disk says which outer round
    or candidate produced it, and the sidebar can only number runs by launch order.

    A work-item is (candidate × ``task``), not a candidate: the panel runs EVERY task
    per candidate, so one candidate's spawns are as many as ``inner_tasks.json`` has
    cells (seven for ``promptpotter-self``). ``task`` is the outer QUERY — the panel
    cell's id, e.g. ``justlogic-d23/seed-0`` — and it is the only thing telling those
    siblings apart; the candidate fields are identical across all of them.

    Read in the OUTER task (see the caller). ``candidate`` is ``None`` for the origin
    pass — C0 doesn't go through candidate scoring — which is a real answer, not a
    missing one, so the label still resolves. A ``round`` of ``None`` means the spawn
    came from outside any round (the fenced ``noise-floor`` diagnostic).
    """
    from promptpotter.domain.results import candidate_label
    from promptpotter.shared.instrument import measured_candidate

    cand = measured_candidate()
    return {
        "outer_cycle_id": ctx.spawn_cycle_id,
        "round": round_num,
        "candidate_idx": cand.idx if cand else None,
        "candidate_id": cand.candidate_id if cand else None,
        "candidate_label": (
            cand.label if cand else (candidate_label(0, 0) if round_num == 0 else None)
        ),
        "task": query,
    }


def _verify_outer_observation_contract(session: Session, dataset_dir: Path) -> None:
    """An outer dataset must DECLARE every key its inner samples emit — checked once, at the
    seam that arms the recursion, against the schema the campaign actually loaded.

    A dataset that owns an ``inner_tasks.json`` IS an outer dataset (the file is what
    :func:`resolve_inner_task` reads), so no name test is needed to recognise one. An
    emitted-but-undeclared key is dropped on the floor by ``sample_measurement`` and never
    reaches ``pipeline_data`` — so the scoring formula either dies on a name it cannot see
    (loud, but a run in) or, worse, the observation quietly never lands in the archive and
    the what-if panel scores a term nobody measured. Fail at arm time instead."""
    schema = session.pipeline_schema
    if schema is None or not (dataset_dir / "inner_tasks.json").is_file():
        return
    declared = {key for node in schema.nodes for key in node.output_keys}
    missing = [k for k in (INNER_RESULT_KEY, *OUTER_PROXY_KEYS) if k not in declared]
    if missing:
        raise ValueError(
            f"{dataset_dir.name} runs inner campaigns but its pipeline.json declares no "
            f"observation_mappings for {missing} — every key an inner sample emits must be "
            "declared, or it never reaches pipeline_data and the outer formula scores a "
            "measurement that was silently dropped."
        )


def _clip(text: str, cap: int) -> str:
    """Whitespace-normalize + head-clip at a word boundary with a visible marker."""
    text = " ".join(text.split())
    if len(text) <= cap:
        return text
    return text[: cap - 1].rsplit(" ", 1)[0] + "…"


def _inner_narrative(result: CycleResult, spec: InnerTaskSpec) -> str:
    """Human-grade digest of one inner campaign — the outer loop's MODEL REASONING.

    Rides the existing ``reasoning_trace`` infra key (``sample_measurement._INFRA_KEYS``)
    so it archives, replays, and renders in the outer ``sample_transcripts`` panel for
    both outer tiers — without it the outer transcripts degenerate to identity tokens
    and the outer critique has literally nothing to quote (run b786e9). Authored to
    ≤1150 chars — under the panel's ``TRANSCRIPT_REASONING_CAP`` (1200) at the writer,
    so the render never clips it. Per round: the discovered level vs origin, the steer
    the round acted on (the PRIOR round's ``priority_fix`` — that's the causal pairing),
    and the strongest candidate's edit + matched-origin delta; plus one verbatim
    failure highlight for the campaign. Exactly the evidence an outer critique needs
    to say WHY a meta-prompt mutation helped or hurt."""
    # A floored cycle has no trajectory to narrate — say why it was floored instead.
    if (floor := floor_reason(result)) is not None:
        return (
            f"INNER {spec.inner_dataset} seed-{spec.seed}: {floor} — scored at the floor "
            f"(meta-prompt-owned); stop={result.stop_reason}."
        )
    # Narrated only for a cycle that carried evidence, so both are present (`compute_outer_proxies`
    # raised otherwise). No `or 0.0`: an origin that was never scored has no level to narrate.
    assert result.origin_level is not None
    origin = result.origin_level
    levels = result.round_discovered_levels
    best = max(levels)
    lines = [
        f"INNER {spec.inner_dataset} seed-{spec.seed}: origin {origin:.3f}"
        f" -> best-discovered {best:.3f} (D{best - origin:+.3f})"
        f" over {result.n_l1_rounds} rounds; stop={result.stop_reason}."
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
            # Rank by lift over the MATCHED origin where there is one, and never invent the
            # comparison where there is not: an eliminated arm carries no matched origin, so
            # `accuracy - 0.0` would have handed it its whole accuracy as lift and floated it
            # to the top of exactly the sentence the outer optimizer learns from.
            top = max(
                scored,
                key=lambda c: (
                    c.accuracy - c.matched_origin_accuracy
                    if c.matched_origin_accuracy is not None
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
                f" vs matched-origin {top.matched_origin_accuracy:.3f}"
                if top.matched_origin_accuracy is not None
                else " (eliminated before the origin could be matched to its samples)"
            )
            parts.append(
                f"tried {top.label} (acc {top.accuracy:.3f}{versus}{theta}): "
                f"{_clip(top.changes_description, 100)}"
            )
        else:
            parts.append("no scored candidates")
        anomalies = [
            f"{tag} x{n}"
            for tag, n in (("no-op", rnd.l1_n_no_op), ("dup", rnd.l1_n_duplicate))
            if n
        ]
        if anomalies:
            parts.append(", ".join(anomalies))
        lines.append(f"R{r} " + " | ".join(parts))
    # Enforce the authored budget: on a deep inner run, drop the EARLIEST round
    # lines first (the trajectory's tail is the informative end) rather than
    # letting the panel's head-keep clip silently cut the latest rounds.
    n_head = 2 if highlight else 1
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


async def _run_inner_campaign(
    ctx: InnerSpawnContext,
    spec: InnerTaskSpec,
    meta_prompt_overrides: dict[str, dict[str, Any]],
    cycle_dir_box: dict[str, Path],
    spawned_by: dict[str, Any],
) -> CycleResult:
    """Mint + run one sandboxed inner campaign; return its ``CycleResult``.

    The result carries ``.spend`` (the inner run's total, captured from its live
    dashboard state), so the caller rolls the inner cost up without touching the
    sandbox's ``dashboard.json``.

    Runs in a FRESH task (the caller spawns it) so the per-task ContextVars are
    isolated from the outer cycle. Sets the per-run optimizer-prompt override
    ContextVar here (inner task only) so the outer L1's meta-prompt mutations
    shape the inner ``_optimizer/`` prompts without leaking to the outer.

    ``cycle_dir_box`` is a mutable holder the caller reads from its outer-task
    heartbeat: once the inner cycle is minted its dir is published here, so the
    heartbeat's ``detail_fn`` can tail the inner ``dashboard.json`` for a live
    ``"inner rX/Y · best Z%"`` line while this runs (the outer chat/dashboard
    would otherwise go silent for the whole multi-minute inner campaign)."""
    # Lazy imports: heavy application machinery, and `run_optimization` would be a
    # package-internal import cycle (`entry.py` imports `publish_inner_spawn_context`
    # from here). Deferring to call time keeps this module import-light.
    from promptpotter.application.bootstrap.wiring import init_services
    from promptpotter.application.config import load_campaign_config
    from promptpotter.application.datasets.authored import read_campaign_config_file
    from promptpotter.application.jobs.mint import prepare_fresh_cycle
    from promptpotter.application.optimization.dispatch.llm_call.prompts import (
        set_optimizer_prompt_overrides,
    )
    from promptpotter.application.optimization.task_context import (
        checkin_call_context,
        load_or_build_task_context,
    )
    from promptpotter.application.run_observers import build_run_observers
    from promptpotter.application.runner.entry import RunMode, run_optimization
    from promptpotter.infrastructure.store.archive_views import capture_evidence_epoch
    from promptpotter.infrastructure.store.stores import build_stores
    from promptpotter.shared.instrument import enter_instrument_mode

    # Apply the outer L1's meta-prompt mutations to the inner optimizer prompts — set in THIS
    # task's context copy, so they can't reach the outer's optimizer. This is the SPECIMEN under
    # test, not part of the instrument: the same channel also carries a normal outer cycle's own
    # meta-prompt SET (`optimizer_set`, bound at the runner seam), so it is not mode-gated.
    set_optimizer_prompt_overrides(meta_prompt_overrides or None)

    # Sandbox: the inner tenant tree roots at the spawning cycle's flat, shallow
    # `<workspace>/.inner/<cycle_id>` home (re-entrant + Windows MAX_PATH-safe; see
    # InnerSpawnContext). The store reads benchmarks from the repo `datasets/`
    # (build_stores default) and keeps the content-addressed caches (`archive` +
    # `optimizer_calls`) on the REAL tenant tree via `shared_root`, so only campaign
    # STATE is sandboxed — not the read-only inner dataset, and not the measurement
    # cache. A cache hit here is the same measurement by content hash; re-scoring it
    # would re-pay for it AND redraw its stochastic value under the outer's fitness.
    store = build_stores(
        ctx.identity,
        projects_root=ctx.inner_sandbox_root,
        shared_root=ctx.shared_root,
    )

    # THE declaration: this cycle is a measurement instrument, not a campaign. One call binds
    # every hermetic property together (recursion depth, the evidence epoch, the optimizer
    # decoding clamp) in THIS task's context copy, so none of it reaches the outer cycle and
    # none of it can be forgotten piecemeal by a future code path. `inner_optimizer_temperature`
    # unset (inner_tasks.json) leaves the optimizer's file decoding alone; the seed is the
    # cell's, matching the target model's (`pipeline_overrides` below), so every candidate for a
    # cell shares one random stream (CRN). See `shared/instrument.py` for why each one is load-
    # bearing — in particular why the archive stays shared as a CACHE while being hidden as
    # MEMORY.
    clamp = (
        None
        if spec.inner_optimizer_temperature is None
        else {"temperature": spec.inner_optimizer_temperature, "seed": spec.seed}
    )
    enter_instrument_mode(
        evidence_epoch=capture_evidence_epoch(store),
        optimizer_clamp=clamp,
    )

    # enable_tracing=False: inner campaigns are ephemeral fitness measurements —
    # their per-(sample x candidate x round) cloud traces have no operator value,
    # burn Langfuse quota, and (the root of the L4 OOM) piled payload-bearing span
    # objects in the logger's _trace_metadata until the process was OOM-killed. The
    # local FileSink still records inner traces to disk for the self-potter-hop.
    session = await init_services(
        dataset_name=spec.inner_dataset,
        identity=ctx.identity,
        store=store,
        enable_tracing=False,
    )
    all_samples = session.samples
    if not all_samples:
        raise ValueError(f"inner dataset {spec.inner_dataset!r} loaded zero samples")
    n = min(max(spec.n_samples, spec.n_samples_origin or 0), len(all_samples))
    train_data = random.Random(spec.seed).sample(all_samples, n)

    file_config: dict[str, Any] = {}
    if session.dataset_config_dir is not None:
        cfg_path = session.dataset_config_dir / "campaign.json"
        if cfg_path.exists():
            file_config = read_campaign_config_file(cfg_path)
    profile = session.store.backends.load_connector_profile(session.backend_id) or {}
    campaign_config = inner_instrument_config(
        spec,
        load_campaign_config({**profile, **file_config}),
        llm_node=session.llm_node_name(),
        n_scored=len(train_data),
    )

    prepare_fresh_cycle(session, campaign_config, train_data)
    if session.campaign_id and session.state.cycle_id:
        # Stamp WHO asked for this measurement onto the cycle index. Written here rather
        # than threaded through `prepare_fresh_cycle` → `auto_mint_session`: those are the
        # generic mint seam every campaign shares, and this is an L4-only fact, so it stays
        # in the L4 module. The cycle index is a raw dict (no model, no `extra="forbid"`),
        # so the key costs nothing and no prior inner cycle needs re-stamping — an older
        # one simply has no `spawned_by` and falls back to its origin hash.
        session.store.campaigns.update(
            session.campaign_id, session.state.cycle_id, {"spawned_by": spawned_by}
        )
        # Publish the freshly-minted inner cycle dir so the outer task's heartbeat
        # detail_fn can tail this run's dashboard.json (best {best}% / round X/Y).
        cycle_dir_box["dir"] = session.store.campaigns.cycle_dir(
            session.campaign_id, session.state.cycle_id
        )
    task_context = await load_or_build_task_context(
        session.dataset_config_dir,
        campaign_id=session.campaign_id,
        context=checkin_call_context(
            session.store, session.campaign_id, session.state.cycle_id or ""
        ),
    )
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
            task_context=task_context,
            mode=RunMode(),
            spend_budget_usd=campaign_config.optimization.spend_budget_usd,
        )
    finally:
        # Release THIS inner campaign's per-campaign resources before the next
        # sequential inner campaign starts. One process runs dozens of inner
        # campaigns back-to-back (6 origin seeds + per-round candidates, deeper at
        # L5+), so anything holding an OS handle or a large payload that leans on
        # GC piles up until the process is OOM-killed (no traceback):
        #   - backend_client: a fresh httpx pool per inner Session (wiring.py) —
        #     close it, since GC is not prompt for sockets.
        #   - langfuse: inner cloud tracing is disabled (enable_tracing=False
        #     above), so no LangfuseSink / _trace_metadata spans accumulate; reset()
        #     is a cheap belt-and-braces release of any stray span refs.
        # The optimizer LLM clients are process-shared (get_llm_client is
        # @functools.cache) AND the Langfuse SDK is a process-wide singleton
        # (keyed by public key) — so NEITHER is shut down here; that would break
        # every later campaign. Cleanup must never mask the run's own outcome, so
        # failures are suppressed.
        with contextlib.suppress(Exception):
            await session.backend_client.aclose()
        if session.langfuse is not None:
            with contextlib.suppress(Exception):
                session.langfuse.reset()
    return result


async def run_inner_cycle(query: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Run one inner campaign for an outer query; return the scorer-shaped result.

    The ``promptpotter`` connector's ``in_process_run`` arm. Resolves the inner
    task from ``inner_tasks.json``, runs the campaign in a fresh ``asyncio.Task``
    (ContextVar isolation), and projects the three proxy metrics onto the
    ``{"data": {…}}`` shape ``measure_sample`` parses from an HTTP body — so the
    outer scorer reads an inner result identically to a remote one.

    An inner cycle that produced **no evidence** about the meta-prompt for a NO-FAULT
    reason — it crashed as tooling, or never reached an L1 round — raises
    ``InnerCycleUnscoreableError`` from ``compute_outer_proxies`` (see ``no_evidence_reason``,
    the one exclusion decision), so ``measure_sample`` excludes it as one error row
    (missing data, not a real proxy). A meta-prompt-OWNED evidence kill — every L1 round
    lost to an empty optimizer response — scores the FLOOR instead (``floor_reason``), and
    a completed inner run that merely failed to improve returns normally with poor proxies
    (measured, so a bad mutation is penalised). One excluded sample cannot kill the outer
    cycle."""
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
            "without bound — check the inner_benchmark named in inner_tasks.json."
        )
    spec = resolve_inner_task(ctx, query)
    overrides = payload.get("meta_prompt_overrides") or {}

    start = time.monotonic()
    # Lazy imports (match this file's lazy-import discipline; sidesteps the
    # llm_call package import cycle). ``run_inner_cycle`` runs in the OUTER task,
    # so the outer ledger is reachable via the ``_CYCLE_LEDGER`` ContextVar — the
    # same binding ``sample_measurement.emit_token_usage`` uses to roll inner
    # spend onto the outer ledger. The heartbeat below appends progress to it so
    # the outer L4 chat + dashboard stay live (never "Run went silent") through
    # the whole multi-minute inner campaign, which emits only to its OWN sandbox
    # ledger.
    from promptpotter.application.optimization.dispatch.llm_call.heartbeat import heartbeat
    from promptpotter.infrastructure.llm.models import _CURRENT_ROUND, _CYCLE_LEDGER

    outer_ledger = _CYCLE_LEDGER.get()
    cycle_dir_box: dict[str, Path] = {}
    # Capture the outer work-item HERE, in the outer task, and hand it to the inner
    # campaign explicitly. It must not be read from inside the inner task: that task
    # gets a COPY of this context, and the inner cycle's own round loop immediately
    # rebinds `_CURRENT_ROUND` to its round — so a read over there would attribute the
    # inner campaign to itself.
    spawned_by = _spawn_provenance(ctx, _CURRENT_ROUND.get(), query)

    def _inner_detail() -> str | None:
        """The outer heartbeat tick's live sub-status — read best-effort off the
        inner cycle's ``dashboard.json`` (``round`` / ``best`` / ``run_limits
        .max_rounds``). ``"inner campaign starting…"`` until the inner cycle is
        minted and its dir published."""
        cycle_dir = cycle_dir_box.get("dir")
        if cycle_dir is None:
            return "inner campaign starting…"
        dash = read_json_optional(cycle_dir / "dashboard.json")
        if not dash:
            return "inner campaign starting…"
        rnd = dash.get("round")
        best = dash.get("best")
        max_rounds = (dash.get("run_limits") or {}).get("max_rounds")
        # Lead with the running winner's LIFT over origin — the SERVED
        # ``headline_delta`` (LiveDashboardState), the same number the webapp
        # headline reads, so the two surfaces cannot disagree.
        delta = dash.get("headline_delta")
        if isinstance(delta, int | float) and isinstance(best, int | float):
            lift = f"Δ{delta:+.0%} (best {best:.0%})"
        elif isinstance(best, int | float):
            lift = f"best {best:.0%}"
        else:
            lift = "best —"
        return f"inner r{rnd if rnd is not None else '?'}/{max_rounds or '?'} · {lift}"

    # Fresh task = its own ContextVar copies (ledger / round / abort / prompt
    # overrides). create_task copies the current context at creation; the
    # inner run re-binds its copies, leaving the outer's untouched.
    #
    # A FAILED outcome surfaces as a returned ``stop_reason`` (e.g. OPTIMIZER_TIMEOUT — the
    # runner returns it, it does not raise), so it is classified below with every other
    # no-evidence shape rather than caught here. A completed inner run that merely failed to
    # improve is a SUCCESS outcome (MAX_ROUNDS) with poor proxies — measured, not excluded —
    # so a bad mutation is still penalised.
    inner_task = asyncio.create_task(
        _run_inner_campaign(ctx, spec, overrides, cycle_dir_box, spawned_by)
    )
    heartbeat_task: asyncio.Task[None] | None = None
    if outer_ledger is not None:
        heartbeat_task = asyncio.create_task(
            heartbeat(
                outer_ledger,
                call_id=f"inner:{query}",
                node="l1_critique",
                round_num=_CURRENT_ROUND.get(),
                start_monotonic=start,
                detail_fn=_inner_detail,
            )
        )
    # The ONE bound on this sample's total wall clock. Awaiting `inner_task` DIRECTLY makes it
    # this coroutine's `_fut_waiter`, so the timeout's cancellation propagates into the inner
    # campaign and it really stops. Do not wrap the await in `asyncio.shield` or `asyncio.wait`:
    # both detach the campaign from the cancellation, orphaning it to keep calling the optimizer
    # and billing tokens against a sample nobody will read.
    deadline_s = OUTER_SAMPLE_WALL_S_PER_ROUND * (spec.n_rounds + 1)
    try:
        try:
            async with asyncio.timeout(deadline_s):
                result = await inner_task
        except TimeoutError:
            raise InnerCycleUnscoreableError(
                f"it ran past its {deadline_s:.0f}s wall-clock deadline "
                f"({spec.n_rounds} rounds + origin, at {OUTER_SAMPLE_WALL_S_PER_ROUND:.0f}s each) "
                "and was cancelled"
            ) from None
    finally:
        # Cancel the heartbeat whether the inner run returned or raised — an
        # in-flight task would otherwise keep appending against a finished sample.
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task
    inner_spend: CycleSpend | None = result.spend
    elapsed = time.monotonic() - start
    # No exclusion decision here: `compute_outer_proxies` asks `no_evidence_reason` and raises
    # `InnerCycleUnscoreableError`, which `measure_sample`'s catch-all turns into this sample's
    # EXCLUDED row. The row already names the sample, so the message carries only the reason.
    proxies = compute_outer_proxies(result)

    data: dict[str, Any] = {
        # Terminal-ranker head = the inner-result token (`inner:{query}` — the
        # connector's `_extract_experiment` sets `ground_truth` to the same
        # prefix; keep the two in sync) plus a compact outcome suffix so the
        # outer diagnostics/transcripts show the movement, not an identity
        # string. Safe: the outer formula reads only the proxy scalars — no
        # consumer matches predicted against ground_truth (outer hit is
        # `fitness >= 1.0`), and the round-0 health gate only needs a
        # non-empty, non-NO_RESULT prediction.
        INNER_RESULT_KEY: [f"inner:{query} D{proxies.after_N_rounds_delta:+.3f}"],
        # The outer loop's raw evidence: a <=1150c narrative of what the inner
        # search tried, what steered it, and what moved — rendered as MODEL
        # REASONING in the outer sample_transcripts panel.
        "reasoning_trace": _inner_narrative(result, spec),
        **proxies.model_dump(),
        # terminated_at is the archive's reuse contract: a named node means "the
        # sample's outcome depends on config only UP TO that node", and prefix-
        # matched rows replay when they terminated inside the trusted prefix
        # (MeasurementArchive.load_reusable_results). An inner campaign consumes
        # the ENTIRE meta-config at once, so the only honest stamp is the LAST
        # node of the outer chain — anything earlier lets a candidate editing a
        # later node (l2_context/l3_plan) silently replay the origin's rows.
        # step_timings/step_tokens stay keyed by l1_critique (the ranker node
        # carrying the proxy observation_mappings + spend attribution).
        "terminated_at": "l3_plan",
        "total_time": elapsed,
        "step_timings": {"l1_critique": elapsed},
    }
    # Roll the inner campaign's total spend up onto the OUTER dashboard via the
    # existing backend-cost channel: the inner cost IS this outer sample's backend
    # cost. Keyed by the terminal node so it fans onto one TokenUsageRecord.
    if inner_spend and (inner_spend.input_tokens or inner_spend.cost_usd):
        data["step_tokens"] = {
            "l1_critique": {
                "input": inner_spend.input_tokens,
                "output": inner_spend.output_tokens,
                "cost_usd": inner_spend.cost_usd,
                "model": f"inner:{spec.inner_dataset}",
            }
        }
    return {"data": data}


__all__ = [
    "INNER_RESULT_KEY",
    "InnerSpawnContext",
    "publish_inner_spawn_context",
    "run_inner_cycle",
]
