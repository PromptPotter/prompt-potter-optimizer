"""L4 inner-cycle runner — the recursion arm of the ``promptpotter`` connector.

The ``promptpotter`` connector declares ``execution="in_process"``; its
``in_process_run`` delegates here. One outer "sample" = one inner PromptPotter
campaign on a cheap proxy benchmark, scored by **how much the inner loop
improved** (the three proxy metrics ``first_round_delta`` /
``after_N_rounds_delta`` / ``rounds_to_N``). Decided in
``docs/specs/l4-outer-loop.md`` § 2.

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

The spawning cycle publishes its context (:func:`publish_inner_spawn_context`,
called from ``runner/entry.py::run_optimization`` for every cycle) so the
connector — which only receives ``(query, payload)`` — can find where to sandbox
and which inner benchmark to run. The outer L1's meta-prompt mutations ride
``payload["meta_prompt_overrides"]`` and are applied to the inner cycle's
``_optimizer/`` prompts via the per-run override ContextVar
(``dispatch/llm_call/prompts.py``), set inside the inner task so it can't leak to
the outer.

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
import contextvars
import logging
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.domain.phases import StopOutcome, stop_reason_outcome
from promptpotter.infrastructure.store.io import read_json_optional

if TYPE_CHECKING:
    from promptpotter.application.bootstrap.session import Session
    from promptpotter.domain.results import CycleResult, CycleSpend
    from promptpotter.shared.identity import IdentityContext

logger = logging.getLogger(__name__)

# The terminal-ranker key the outer `promptpotter-self` pipeline reads as its
# prediction (a non-empty list keeps the origin round-0 health gate from halting
# on all-NO_RESULT) + the three proxy scalars the outer scoring formula reads.
# `datasets/promptpotter-self/pipeline.json::nodes.l1_critique.optimizer
# .observation_mappings` declares these as observation keys, so they reach
# `pipeline_data` and the formula namespace (`scoring/formula/compiler.py`).
INNER_RESULT_KEY = "final_ranking"
PROXY_KEYS = ("first_round_delta", "after_N_rounds_delta", "rounds_to_N")


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
    sandbox stores under the same tenant."""

    inner_sandbox_root: Path
    dataset_config_dir: Path
    identity: IdentityContext


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
    # recursion depth (Windows MAX_PATH). projects_root is ``<workspace>/projects``.
    inner_root = session.store.projects_root.parent / ".inner" / cycle_id
    _INNER_SPAWN.set(
        InnerSpawnContext(
            inner_sandbox_root=inner_root,
            dataset_config_dir=Path(dataset_dir),
            identity=session.store.identity,
        )
    )


@dataclass(frozen=True)
class _InnerTaskSpec:
    """One outer query resolved against ``inner_tasks.json`` → an inner campaign."""

    inner_dataset: str
    seed: int
    n_samples: int
    n_rounds: int
    target: float
    n_variants: int | None  # inner_n_variants — None keeps the inner dataset's own value


def _resolve_inner_task(ctx: InnerSpawnContext, query: str) -> _InnerTaskSpec:
    """Map an outer query (``"justlogic-d67/seed-0"``) to its inner-campaign spec.

    Reads the spawning dataset's ``inner_tasks.json`` — top-level
    ``inner_benchmark`` + ``inner_benchmark_config`` (the inner dataset + sample
    count + round cap + target), overlaid by the matching ``tasks[]`` entry
    (per-task seed / round cap / target)."""
    cfg = read_json_optional(ctx.dataset_config_dir / "inner_tasks.json") or {}
    bench = str(cfg.get("inner_benchmark") or "justlogic")
    bench_cfg = cfg.get("inner_benchmark_config") or {}
    n_samples = int(bench_cfg.get("n_samples_per_inner_round", 10))
    n_rounds = int(bench_cfg.get("max_inner_rounds", 3))
    target = float(bench_cfg.get("target_score", 0.8))
    raw_variants = bench_cfg.get("inner_n_variants")
    n_variants = int(raw_variants) if raw_variants is not None else None
    seed = 0
    for task in cfg.get("tasks", []):
        if isinstance(task, dict) and task.get("id") == query:
            seed = int(task.get("inner_dataset_seed", 0))
            n_rounds = int(task.get("n_inner_rounds", n_rounds))
            target = float(task.get("target_score", target))
            break
    return _InnerTaskSpec(
        inner_dataset=bench,
        seed=seed,
        n_samples=n_samples,
        n_rounds=n_rounds,
        target=target,
        n_variants=n_variants,
    )


def _compute_proxies(result: CycleResult, target: float) -> dict[str, Any]:
    """The three proxy metrics from a finished inner cycle.

    ``first_round_delta`` = round-1 discovered lift over origin; ``after_N_rounds_delta`` =
    best discovered lift over origin (across all rounds); ``rounds_to_N`` = rounds to first
    reach *target* (0 if the origin already meets it, ``n_rounds + 1`` as the "didn't make
    it" sentinel just past the budget). Deltas may be negative on a regressing meta-prompt.

    The heavy lifting is upstream in ``discovered_level_trajectory`` (single-scale, θ-LCB
    over discovered candidates): here we just difference its ``origin_level`` /
    ``round_discovered_levels`` — no mixed-space subtraction, no crowned-frontier blindness.
    Levels are cumulative, so the last level is the best; ``max`` is defensive."""
    origin = result.origin_level
    levels = result.round_discovered_levels
    first = (levels[0] - origin) if levels else 0.0
    after_n = (max(levels) - origin) if levels else 0.0
    if origin >= target:
        rounds_to_n = 0
    else:
        rounds_to_n = next(
            (i + 1 for i, lvl in enumerate(levels) if lvl >= target),
            len(levels) + 1,
        )
    return {
        "first_round_delta": round(first, 6),
        "after_N_rounds_delta": round(after_n, 6),
        "rounds_to_N": rounds_to_n,
    }


async def _run_inner_campaign(
    ctx: InnerSpawnContext,
    spec: _InnerTaskSpec,
    meta_prompt_overrides: dict[str, dict[str, Any]],
) -> CycleResult:
    """Mint + run one sandboxed inner campaign; return its ``CycleResult``.

    The result carries ``.spend`` (the inner run's total, captured from its live
    dashboard state), so the caller rolls the inner cost up without touching the
    sandbox's ``dashboard.json``.

    Runs in a FRESH task (the caller spawns it) so the per-task ContextVars are
    isolated from the outer cycle. Sets the per-run optimizer-prompt override
    ContextVar here (inner task only) so the outer L1's meta-prompt mutations
    shape the inner ``_optimizer/`` prompts without leaking to the outer."""
    # Lazy imports: heavy application machinery, and `run_optimization` would be a
    # package-internal import cycle (`entry.py` imports `publish_inner_spawn_context`
    # from here). Deferring to call time keeps this module import-light.
    from promptpotter.application.bootstrap.wiring import init_services
    from promptpotter.application.config import load_campaign_config
    from promptpotter.application.datasets import read_campaign_config_file
    from promptpotter.application.jobs.mint import prepare_fresh_cycle
    from promptpotter.application.optimization.dispatch.llm_call import (
        set_optimizer_prompt_overrides,
    )
    from promptpotter.application.optimization.task_context import load_or_build_task_context
    from promptpotter.application.run_observers import build_run_observers
    from promptpotter.application.runner import RunMode, run_optimization
    from promptpotter.infrastructure.store import build_stores

    # Apply the outer L1's meta-prompt mutations to the inner optimizer prompts —
    # set in THIS task's context copy, so it can't reach the outer's optimizer.
    set_optimizer_prompt_overrides(meta_prompt_overrides or None)

    # Sandbox: the inner tenant tree roots at the spawning cycle's flat, shallow
    # `<workspace>/.inner/<cycle_id>` home (re-entrant + Windows MAX_PATH-safe; see
    # InnerSpawnContext). The store reads benchmarks from the repo `datasets/`
    # (build_stores default), so only campaign STATE is sandboxed, not the
    # read-only inner dataset.
    store = build_stores(ctx.identity, projects_root=ctx.inner_sandbox_root)

    session = await init_services(
        dataset_name=spec.inner_dataset, identity=ctx.identity, store=store
    )
    all_samples = session.samples or []
    if not all_samples:
        raise ValueError(f"inner dataset {spec.inner_dataset!r} loaded zero samples")
    n = min(spec.n_samples, len(all_samples))
    train_data = random.Random(spec.seed).sample(all_samples, n)

    file_config: dict[str, Any] = {}
    if session.dataset_config_dir is not None:
        cfg_path = session.dataset_config_dir / "campaign.json"
        if cfg_path.exists():
            file_config = read_campaign_config_file(cfg_path)
    profile = session.store.backends.load_connector_profile(session.backend_id) or {}
    campaign_config = load_campaign_config({**profile, **file_config})
    # Cap inner rounds at the task's budget — the proxy metrics are defined over
    # exactly this many rounds, and it bounds the (geometric) recursion cost.
    # Score every candidate on the WHOLE inner bank (``sp_budget_ttest = len(train_data)``),
    # not a thinner per-round subset: the outer proxy reads each candidate's θ-LCB, whose
    # width is set by how many samples that candidate was scored on, so a bank drawn but not
    # fully scored just widens the LCB and starves the outer signal (the inner draw and the
    # inner measurement must be the same size — anything less is wasted samples). The inner
    # spend cap is lifted to fit a full-bank × N-round run so this doesn't merely trip the
    # ``spend_budget`` stop early; it stays well inside the outer campaign's own $ cap. A None
    # (unlimited) inner budget is BOUNDED here — an inner cycle must never run open-ended.
    inner_spend_cap = max(campaign_config.optimization.spend_budget_usd or 0.05, 0.05)
    opt_update: dict[str, Any] = {"max_rounds": spec.n_rounds, "spend_budget_usd": inner_spend_cap}
    if spec.n_variants is not None:
        # inner_n_variants (inner_tasks.json) — the outer task spec owns the inner
        # search width, same as it owns the round cap; the inner dataset's own
        # n_variants is a standalone-campaign default, not an L4 decision.
        opt_update["n_variants"] = spec.n_variants
    campaign_config = campaign_config.model_copy(
        update={
            "sp_budget_ttest": len(train_data),
            "optimization": campaign_config.optimization.model_copy(update=opt_update),
        }
    )

    prepare_fresh_cycle(session, campaign_config, train_data)
    task_context = await load_or_build_task_context(session.dataset_config_dir)
    observers = build_run_observers(
        session=session,
        campaign_config=campaign_config,
        dataset=train_data,
        display=None,
        resumed_from_round=None,
        origin_accuracy=0.0,
    )
    result = await run_optimization(
        train_data,
        campaign_config,
        session=session,
        observers=observers,
        experiment_id=session.experiment_id,
        task_context=task_context,
        mode=RunMode(),
        spend_budget_usd=campaign_config.optimization.spend_budget_usd,
    )
    return result


async def run_inner_cycle(query: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Run one inner campaign for an outer query; return the scorer-shaped result.

    The ``promptpotter`` connector's ``in_process_run`` arm. Resolves the inner
    task from ``inner_tasks.json``, runs the campaign in a fresh ``asyncio.Task``
    (ContextVar isolation), and projects the three proxy metrics onto the
    ``{"data": {…}}`` shape ``measure_sample`` parses from an HTTP body — so the
    outer scorer reads an inner result identically to a remote one.

    A TOOLING failure (``StopOutcome.FAILED`` — inner timeout/crash/diverge/render
    error, however it surfaced) RAISES, so ``measure_sample`` excludes it as one
    error row (missing data, not a real proxy) instead of scoring a false floor
    that mimics a bad mutation. A completed inner run that merely failed to improve
    returns normally with poor proxies (measured, so a bad mutation is penalised).
    One excluded sample cannot kill the outer cycle."""
    ctx = _INNER_SPAWN.get()
    if ctx is None:
        raise RuntimeError(
            "promptpotter connector: no inner-spawn context published — "
            "run_optimization must call publish_inner_spawn_context first."
        )
    spec = _resolve_inner_task(ctx, query)
    overrides = payload.get("meta_prompt_overrides") or {}

    start = time.monotonic()
    # Fresh task = its own ContextVar copies (ledger / round / abort / prompt
    # overrides). create_task copies the current context at creation; the
    # inner run re-binds its copies, leaving the outer's untouched.
    #
    # A TOOLING failure (the inner optimizer timed out, crashed, diverged, or hit
    # a render error) is NOT evidence the outer meta-prompt mutation was bad — it
    # is missing data. Scoring it as a real proxy (the old -1.0 floor) is
    # indistinguishable from a genuinely-regressing mutation and silently corrupts
    # the outer signal. So we RAISE on a FAILED outcome, whether it surfaced as a
    # returned ``stop_reason`` (e.g. OPTIMIZER_TIMEOUT — the runner returns it, it
    # does not raise) or as an exception. ``measure_sample``'s own catch-all turns
    # that raise into one EXCLUDED error row (out of hits/accuracy/rescore/θ), so
    # the candidate is scored on its surviving samples and one broken inner cycle
    # still cannot kill the outer cycle. A completed inner run that merely failed
    # to improve is a SUCCESS outcome (MAX_ROUNDS) with poor proxies — measured,
    # not excluded — so a bad mutation is still penalised.
    result = await asyncio.create_task(_run_inner_campaign(ctx, spec, overrides))
    if stop_reason_outcome(result.stop_reason) is StopOutcome.FAILED:
        raise RuntimeError(
            f"inner cycle for {query} failed as tooling (stop_reason="
            f"{result.stop_reason}); excluding this outer sample, not scoring it"
        )
    inner_spend: CycleSpend | None = result.spend
    proxies = _compute_proxies(result, spec.target)
    elapsed = time.monotonic() - start

    data: dict[str, Any] = {
        # Terminal-ranker head = the inner-result token. The connector's
        # `_extract_experiment` sets each sample's `ground_truth` to this SAME
        # `inner:{query}` token, so a sample is a HIT iff its inner cycle
        # produced a result (there is no label to match in L4 — fitness is the
        # proxy composite). Keep the two formats in sync.
        INNER_RESULT_KEY: [f"inner:{query}"],
        **proxies,
        "terminated_at": "l1_critique",
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
    "PROXY_KEYS",
    "InnerSpawnContext",
    "publish_inner_spawn_context",
    "run_inner_cycle",
]
