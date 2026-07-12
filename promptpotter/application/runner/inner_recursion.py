"""L4 inner-cycle runner — the recursion arm of the ``promptpotter`` connector.

The ``promptpotter`` connector declares ``execution="in_process"``; its
``in_process_run`` delegates here. One outer "sample" = one inner PromptPotter
campaign on a cheap proxy benchmark, scored by **how much the inner loop
improved** — a composed fitness (``_compute_proxies``) over endpoint deltas,
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
import contextlib
import contextvars
import logging
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.application.config import LivesConfig
from promptpotter.domain.escalation_signals import NurseOwner
from promptpotter.domain.outer_verdict import OUTER_PROXY_KEYS, OuterSampleProxies
from promptpotter.domain.phases import StopOutcome, stop_reason_outcome
from promptpotter.domain.results import L1_PARSE_FAILURE_TOOLING
from promptpotter.infrastructure.store.io import read_json_optional

if TYPE_CHECKING:
    from promptpotter.application.bootstrap.session import Session
    from promptpotter.domain.results import CycleResult, CycleSpend, RoundResult
    from promptpotter.shared.identity import IdentityContext

logger = logging.getLogger(__name__)


class InnerCycleUnscoreableError(RuntimeError):
    """The inner cycle produced no evidence about the meta-prompt under test.

    THE exclusion signal — a crash, a cycle that never reached an L1 round, and a cycle whose
    every round lost its candidates are one fact, not three, so they raise one exception with
    one vocabulary (see :func:`_no_evidence_reason`). ``measure_sample``'s catch-all turns it
    into one EXCLUDED row and the candidate is scored on its surviving samples. Never a zero —
    a zero is a verdict, and this is the absence of one."""


# The terminal-ranker key the outer `promptpotter-self` pipeline reads as its
# prediction (a non-empty list keeps the origin round-0 health gate from halting
# on all-NO_RESULT) + the composed-fitness proxy scalars the outer scoring formula
# reads. `datasets/promptpotter-self/pipeline.json::nodes.l1_critique.optimizer
# .observation_mappings` declares these as observation keys, so they reach
# `pipeline_data` and the formula namespace (`scoring/formula/compiler.py`).
INNER_RESULT_KEY = "final_ranking"

# Per-round inner spend allowance, used to size an inner cycle's `spend_budget_usd` from its
# round budget. Measured on the L4 inner benchmark (justlogic, 24-sample bank, 2 inner
# variants, gpt-oss-120b optimizer + gpt-oss-20b target): the origin round costs ~$0.006 and
# each L1 round ~$0.006 (optimizer + backend). Doubled, so a pricier model or a wider bank
# still finishes its declared rounds rather than tripping the cost stop mid-budget.
INNER_SPEND_PER_ROUND_USD = 0.012

# Token twin of the spend allowance above. `token_budget` counts backend + optimizer tokens and,
# on a cheap/free backend, is the ceiling that trips first. It MUST scale off the round budget for
# the same reason the spend cap does — a flat constant the round budget can outgrow silently
# reconverts a ROUND budget into a TOKEN budget, halting a deep inner run mid-rounds. The flat
# `CampaignConfig` default is 210k ≈ a 5-round run (~42k/round); sized to 52k/round here so a
# 7-round inner run gets ~2x that (the `429eea` starvation halted every inner campaign at ~210k
# with rounds still in hand), with the same headroom rationale.
INNER_TOKENS_PER_ROUND = 52_000

# Per-round wall-clock allowance for ONE outer sample, sized like the spend cap above. Every
# individual await inside an inner campaign is already bounded (optimizer 180s x2, backend 120s,
# MAX_429_ATTEMPTS, rounds <= HARD_CAP) — but nothing bounded their SUM, so sustained 429
# throttling could stretch one sample across tens of minutes inside every one of those bounds,
# silently truncating how many outer rounds completed. Measured over the 155 inner cycles on
# disk that ran to a SUCCESS outcome: per-round p50 81s, p90 116s, max 245s. At 300s none of
# them would have tripped, so the deadline catches only a genuinely stuck sample.
OUTER_SAMPLE_WALL_S_PER_ROUND = 300.0

# How deep the recursion may nest. L4 (an outer campaign scoring inner campaigns) is depth 1;
# the module is written to re-enter, so L5+ nests by construction and nothing stopped it. Depth
# is carried on a ContextVar rather than the spawn context because `create_task` copies the
# context into the inner campaign, so each level reads exactly its parent's value.
MAX_INNER_DEPTH = 2
_INNER_DEPTH: contextvars.ContextVar[int] = contextvars.ContextVar(
    "promptpotter_inner_depth", default=0
)


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
    so the isolation injected a noise term larger than the lift it was measuring."""

    inner_sandbox_root: Path
    dataset_config_dir: Path
    identity: IdentityContext
    shared_root: Path


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
        )
    )


def _verify_outer_observation_contract(session: Session, dataset_dir: Path) -> None:
    """An outer dataset must DECLARE every key its inner samples emit — checked once, at the
    seam that arms the recursion, against the schema the campaign actually loaded.

    A dataset that owns an ``inner_tasks.json`` IS an outer dataset (the file is what
    :func:`_resolve_inner_task` reads), so no name test is needed to recognise one. An
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


@dataclass(frozen=True)
class _InnerTaskSpec:
    """One outer query resolved against ``inner_tasks.json`` → an inner campaign."""

    inner_dataset: str
    seed: int
    n_samples: int
    n_rounds: int
    target: float
    n_variants: int | None  # inner_n_variants — None keeps the inner dataset's own value
    # ``inner_lives`` — the improvement-banked round budget for the inner cycle. ``n_rounds``
    # is the CEILING a compounding inner campaign is allowed to reach; ``lives`` is what makes
    # a stalling one stop early (a fully-stalling run does exactly ``start`` L1 rounds). Without
    # it, every inner campaign — improving or dead — pays the full round budget, which is the
    # dominant cost of raising that budget at all. Owned by the outer task spec for the same
    # reason ``n_rounds`` and ``n_variants`` are: it is an L4 decision about how much evidence
    # to buy, not the inner dataset's standalone default. None ⇒ ``max_rounds`` alone governs.
    lives: LivesConfig | None = None
    # Per-cell target-model override (the environment axis of the panel). None keeps
    # the inner dataset's own pinned model. The operator sets these — never the loop —
    # so an (target-model, dataset) cell is a fixed, in-band resource, not a fuzzy pick.
    inner_model: str | None = None
    inner_provider: str | None = None
    # Determinism clamp for the inner OPTIMIZER's sampling temperature. Each inner
    # campaign is a fitness MEASUREMENT of one meta-prompt; at the file default
    # (l1_generate creativity ~0.4) identical meta-prompts generate different
    # candidates → run-to-run swing that swamps the outer proxy. Pinning this (0.0)
    # makes the inner loop a near-deterministic function of its meta-prompt. None
    # keeps the optimizer's file temperature (feature off). Applied inner-task-only.
    inner_optimizer_temperature: float | None = None


_REQUIRED_BENCH_KEYS = ("n_samples_per_inner_round", "max_inner_rounds", "target_score")


def _resolve_inner_task(ctx: InnerSpawnContext, query: str) -> _InnerTaskSpec:
    """Map an outer query (``"justlogic-d67/seed-0"``) to its inner-campaign spec.

    Reads the spawning dataset's ``inner_tasks.json`` — top-level
    ``inner_benchmark`` + ``inner_benchmark_config`` (the inner dataset + sample
    count + round cap + target), overlaid by the matching ``tasks[]`` entry
    (per-task seed / round cap / target, and — for a multi-cell panel — per-task
    ``inner_dataset`` / ``inner_model`` / ``inner_provider``). A task that omits
    those inherits the single top-level benchmark + the dataset's own model,
    so a single-cell panel needs no per-task overrides.

    **The config is the source of truth; there is no default ladder here.** ``target_score`` is
    the denominator of ``headroom_lift``, a proxy the outer L4 formula scores on, and the round
    cap and sample count set what the inner cycle is even allowed to discover. Defaulting them in
    code silently rescales every meta-prompt candidate's fitness against a benchmark nobody
    declared — and the defaults this used to carry (``justlogic``/10/3/0.8) had already drifted
    from the shipped config (24/7/0.6) on three of four values, with nothing to reveal it."""
    path = ctx.dataset_config_dir / "inner_tasks.json"
    cfg = read_json_optional(path)
    if not isinstance(cfg, dict):
        raise InnerCycleUnscoreableError(
            f"{path} is missing or is not an object — the inner benchmark, its sample count, "
            "round cap and target score are all declared there. There is no default to run."
        )
    bench = str(cfg.get("inner_benchmark") or "")
    bench_cfg = cfg.get("inner_benchmark_config") or {}
    missing = [k for k in _REQUIRED_BENCH_KEYS if bench_cfg.get(k) is None]
    if not bench or missing:
        raise InnerCycleUnscoreableError(
            f"{path} must declare 'inner_benchmark' and inner_benchmark_config"
            f"{list(_REQUIRED_BENCH_KEYS)}; missing: "
            f"{([] if bench else ['inner_benchmark']) + missing}."
        )
    n_samples = int(bench_cfg["n_samples_per_inner_round"])
    n_rounds = int(bench_cfg["max_inner_rounds"])
    target = float(bench_cfg["target_score"])
    raw_variants = bench_cfg.get("inner_n_variants")
    n_variants = int(raw_variants) if raw_variants is not None else None
    raw_lives = bench_cfg.get("inner_lives")
    lives = LivesConfig.model_validate(raw_lives) if raw_lives is not None else None
    raw_opt_temp = bench_cfg.get("inner_optimizer_temperature")
    inner_optimizer_temperature = float(raw_opt_temp) if raw_opt_temp is not None else None
    seed = 0
    inner_model: str | None = None
    inner_provider: str | None = None
    for task in cfg.get("tasks", []):
        if isinstance(task, dict) and task.get("id") == query:
            seed = int(task.get("inner_dataset_seed", 0))
            n_rounds = int(task.get("n_inner_rounds", n_rounds))
            target = float(task.get("target_score", target))
            bench = str(task.get("inner_dataset") or bench)
            im = task.get("inner_model")
            inner_model = str(im) if im else None
            ip = task.get("inner_provider")
            inner_provider = str(ip) if ip else None
            break
    return _InnerTaskSpec(
        inner_dataset=bench,
        seed=seed,
        n_samples=n_samples,
        n_rounds=n_rounds,
        target=target,
        n_variants=n_variants,
        lives=lives,
        inner_model=inner_model,
        inner_provider=inner_provider,
        inner_optimizer_temperature=inner_optimizer_temperature,
    )


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _mean(xs: list[float]) -> float:
    """Mean of a NON-EMPTY sequence. The empty case is a caller bug, not a zero: every use
    here feeds a proxy the outer formula reads as a positive signal, so ``sum([])/1 -> 0.0``
    would report an unexercised meta-prompt as perfectly clean. Callers exclude first
    (:func:`_no_evidence_reason`); this refuses to invent the verdict for them."""
    if not xs:
        raise InnerCycleUnscoreableError(
            "a proxy was averaged over no rounds — the cycle should have been excluded"
        )
    return sum(xs) / len(xs)


def _is_evidential(rnd: RoundResult) -> bool:
    """False when the round says nothing about the meta-prompt under test.

    A ``L1_PARSE_FAILURE_TOOLING`` round lost its candidates to an empty optimizer
    response. That is missing data, the same class as the crashed inner cycle this module
    already excludes as an outer sample — *"a TOOLING failure is NOT evidence the outer
    meta-prompt mutation was bad."* Scoring it dirty makes provider flakiness look like a
    bad mutation, which is precisely the noise that swamps the outer signal."""
    return rnd.l1_parse_failure != L1_PARSE_FAILURE_TOOLING


def _self_heal_rate(rnd: RoundResult) -> float:
    """Share of the round's candidates that tripped the inner self-healing machinery for a
    META-PROMPT-owned reason ∈ [0,1]: a malformed L1 variant (any ``ValidationFailure`` —
    e.g. a hallucinated node key) or an operator-terminal ``RuntimeFailure`` (a config-
    deterministic break). A transient-transport ``RuntimeFailure`` is ``owner=L1`` (provider
    noise, not the meta-prompt's fault — see ``signal_effect._abort_is_config_break``) and is
    excluded, the same reason a TOOLING round is not scored dirty.

    The general L4 principle: whatever the inner loop had to self-heal is evidence about the
    meta-prompt under test, so it rides the ``cleanliness`` penalty to the outer optimizer.
    The weight is provisional (a per-candidate share folded 1:1); tuning it is deferred."""
    cands = rnd.candidate_scores
    if not cands:
        return 0.0
    healed = sum(
        1
        for c in cands
        if c.validation_failures
        or any(rf.owner is NurseOwner.OPERATOR for rf in c.runtime_failures)
    )
    return healed / len(cands)


def _round_problem_rate(rnd: RoundResult) -> float:
    """Per-round dirtiness ∈ [0,1] for an EVIDENTIAL round (see ``_is_evidential``), three
    disjoint candidate-controlled signals: inner samples that degraded or came back
    unscoreable (``health``), the meta-fault self-heal load (``_self_heal_rate`` — malformed
    variants + config breaks the inner loop had to heal), or a round the outer meta-prompt
    made unparseable outright. A meta-prompt that makes the inner loop emit unscoreable
    predictions, need healing, or yield no candidates at all scores dirtier.

    A parse failure is charged to the ROUND, not to a candidate. It cannot be read off
    ``candidate_scores``: ``l1_generate`` returns zero candidates in exactly that case, so
    any per-candidate scan is structurally always empty and the worst possible round —
    a meta-prompt that makes its own children unreadable — would score perfectly clean.
    """
    if rnd.l1_parse_failure:
        return 1.0
    health = rnd.health
    struct = 0.0
    if health and health.samples:
        struct = health.degraded_rate + health.no_result_count / health.samples
    struct += _self_heal_rate(rnd)
    return _clamp(struct, 0.0, 1.0)


def _no_evidence_reason(result: CycleResult) -> str | None:
    """Why this inner cycle says nothing about the meta-prompt under test — ``None`` if it does.

    THE exclusion decision, asked once. The question is **"did this run produce evidence?"**,
    never "did it fail?". Those differ: a cycle can end SUCCESS/HALTED/PAUSED having never run
    an L1 round (target hit or perfect at origin, spend/token budget, origin gate, an operator
    Ctrl+C), and every aggregate below then reads an *unexercised* meta-prompt as flawless —
    ``_mean([]) -> 0.0``, so ``cleanliness = diversity_health = 1.0``. That is a fabricated
    measurement, the same class of harm as scoring a crash as a bad mutation.

    ``result.rounds`` holds L1 rounds only (``Cycle.absorb_round`` is their sole sink; resume
    peels round 0 off), and ``round_discovered_levels`` carries one level per such round."""
    if stop_reason_outcome(result.stop_reason) is StopOutcome.FAILED:
        return f"it failed as tooling (stop_reason={result.stop_reason})"
    if not result.rounds:
        return f"it ran no L1 rounds (stop_reason={result.stop_reason})"
    if not any(_is_evidential(r) for r in result.rounds):
        return (
            f"every one of its {len(result.rounds)} L1 round(s) lost its candidates to an "
            "empty optimizer response"
        )
    if result.origin_level is None:
        return "its origin was never scored, so there is no floor to difference its rounds against"
    if not result.round_discovered_levels:
        return "it discovered no levels to difference against its origin"
    # Unmeasured spend is not cheap spend. `delta_per_dollar` divides by cost, and the outer
    # formula reads it as `0.7 + 0.3*clamp(dpd/6)` — so a cost of 0.0 pins efficiency to its
    # MAXIMUM. Under-reporting spend made a run score fitter, which is the incentive exactly
    # backwards. `unpriced_tokens > 0` is the same harm partially applied: cost_usd is then a
    # floor, not a total, and the ratio is inflated by however much went unpriced.
    spend = result.spend
    if spend is None or spend.cost_usd <= 0.0:
        return "it recorded no spend, so its cost-efficiency cannot be measured"
    if spend.unpriced_tokens > 0:
        return (
            f"{spend.unpriced_tokens} of its tokens have no USD rate on file, so its recorded "
            f"cost (${spend.cost_usd:.4f}) understates real spend"
        )
    return None


def _round_mode_collapse_rate(rnd: RoundResult) -> float:
    """Per-round mode-collapse ∈ [0,1): share of generated variants the invariant detector
    nuked as no-op / duplicate. A meta-prompt that induces the inner L1 to regurgitate the
    parent (or itself) is not exploring — the outer loop should steer away from it."""
    collapsed = rnd.l1_n_no_op + rnd.l1_n_duplicate
    generated = collapsed + rnd.candidates_scored
    return collapsed / generated if generated else 0.0


def _compute_proxies(result: CycleResult, target: float, elapsed: float) -> OuterSampleProxies:
    """Composed outer fitness — a vector of subset-invariant, bounded raw signals from a
    finished inner cycle, combined by ``campaign.json::scoring`` (the backend never hides
    the composite; every term stays a re-weightable observation).

    Endpoint deltas (kept — early speed + best depth): ``first_round_delta`` = round-1
    discovered lift over origin; ``after_N_rounds_delta`` = best discovered lift over origin;
    ``rounds_to_N`` = rounds to first reach *target*. These difference the single-scale θ-LCB
    trajectory (``origin_level`` / ``round_discovered_levels``, built upstream in
    ``discovered_level_trajectory``) — no mixed-space subtraction, no crowned-frontier blindness.

    Composed terms (each carries a candidate gradient — the retired proxies died for lacking
    one, so a flat term earns nothing and gets cut in tuning):
    - ``headroom_lift`` — best depth normalized by the task's own headroom ``(target−origin)``,
      so a config compares across inner tasks of different origin strength.
    - ``cleanliness`` / ``diversity_health`` — bounded quality: 1 − mean per-round problem /
      mode-collapse rate; the scoring formula uses them as a modulator that discounts a
      warning-riddled or collapsing campaign without diluting the lift core.
    - ``rounds_improved_frac`` — share of L1 rounds that beat their predecessor.
    - ``delta_per_dollar`` / ``delta_per_candidate`` / ``delta_per_second`` — efficiency: best
      depth over spend / candidates / wall-time. Candidate-specific numerator, so (unlike the
      retired per-seed cost multiplier) they carry a gradient — a verbose meta-prompt burns
      more for the same lift and scores lower.

    Deltas may be negative on a regressing meta-prompt (levels are not floored at origin), so
    the efficiency ratios can be negative too; the formula recentres / clamps.

    Raises :class:`InnerCycleUnscoreableError` when the cycle carries no evidence. Asking that
    first is what makes "absent" un-representable here; :class:`OuterSampleProxies` is what keeps
    it so — bounded fields carry their bound, and no field has a default, because the absence of
    a measurement is not a value a meta-prompt can be scored on."""
    if (reason := _no_evidence_reason(result)) is not None:
        raise InnerCycleUnscoreableError(reason)

    assert result.origin_level is not None  # guaranteed by _no_evidence_reason
    origin = result.origin_level
    levels = result.round_discovered_levels
    first = levels[0] - origin
    after_n = max(levels) - origin
    if origin >= target:
        rounds_to_n = 0
    else:
        rounds_to_n = next(
            (i + 1 for i, lvl in enumerate(levels) if lvl >= target),
            len(levels) + 1,
        )

    headroom = target - origin
    headroom_lift = (
        _clamp(after_n / headroom, -1.0, 1.0) if headroom > 1e-9 else float(after_n >= 0) * 2 - 1
    )

    # Individual rounds that carry no evidence are dropped, not scored — an all-tooling cycle
    # already left via `_no_evidence_reason`, so `evidential` is non-empty here.
    evidential = [r for r in result.rounds if _is_evidential(r)]
    cleanliness = 1.0 - _mean([_round_problem_rate(r) for r in evidential])
    diversity_health = 1.0 - _mean([_round_mode_collapse_rate(r) for r in evidential])
    rounds_improved_frac = _mean([1.0 if r.improved else 0.0 for r in evidential])

    # No `max(cost, eps)` floor: `_no_evidence_reason` already refused a cycle whose spend is
    # zero or under-priced, so the divisor is a real, fully-priced cost.
    assert result.spend is not None  # guaranteed by _no_evidence_reason
    cost = result.spend.cost_usd
    n_cand = sum(r.candidates_scored for r in result.rounds)
    delta_per_dollar = after_n / cost
    # n_cand == 0 only when no candidate was ever scored — and then the trajectory never rose
    # above origin, so `after_n` is 0.0 and this is 0/0. Say 0.0 outright instead of letting a
    # `max(n_cand, 1)` divisor pretend one candidate existed.
    delta_per_candidate = after_n / n_cand if n_cand else 0.0
    delta_per_second = after_n / max(elapsed, 1e-6)

    return OuterSampleProxies(
        first_round_delta=first,
        after_N_rounds_delta=after_n,
        rounds_to_N=rounds_to_n,
        headroom_lift=headroom_lift,
        cleanliness=cleanliness,
        diversity_health=diversity_health,
        rounds_improved_frac=rounds_improved_frac,
        delta_per_dollar=delta_per_dollar,
        delta_per_candidate=delta_per_candidate,
        delta_per_second=delta_per_second,
    )


def _clip(text: str, cap: int) -> str:
    """Whitespace-normalize + head-clip at a word boundary with a visible marker."""
    text = " ".join(text.split())
    if len(text) <= cap:
        return text
    return text[: cap - 1].rsplit(" ", 1)[0] + "…"


def _inner_narrative(result: CycleResult, spec: _InnerTaskSpec) -> str:
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
    # Narrated only for a cycle that carried evidence, so both are present (`_compute_proxies`
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
            top = max(
                scored,
                key=lambda c: (c.accuracy - c.matched_origin_accuracy, c.composite_fitness),
            )
            theta = (
                f", th {top.theta:.2f}+/-{top.theta_se:.2f}"
                if top.theta is not None and top.theta_se is not None
                else ""
            )
            parts.append(
                f"tried {top.label} (acc {top.accuracy:.3f} vs matched-origin "
                f"{top.matched_origin_accuracy:.3f}{theta}): "
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
    spec: _InnerTaskSpec,
    meta_prompt_overrides: dict[str, dict[str, Any]],
    cycle_dir_box: dict[str, Path],
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
    from promptpotter.application.datasets import read_campaign_config_file
    from promptpotter.application.jobs.mint import prepare_fresh_cycle
    from promptpotter.application.optimization.dispatch.llm_call import (
        set_optimizer_config_overrides,
        set_optimizer_prompt_overrides,
    )
    from promptpotter.application.optimization.task_context import (
        checkin_call_context,
        load_or_build_task_context,
    )
    from promptpotter.application.run_observers import build_run_observers
    from promptpotter.application.runner import RunMode, run_optimization
    from promptpotter.infrastructure.store import build_stores

    # One level deeper, recorded in THIS task's context copy only — so a campaign nested inside
    # this one reads its parent's depth, and the outer cycle's own depth is untouched.
    _INNER_DEPTH.set(_INNER_DEPTH.get() + 1)
    # Apply the outer L1's meta-prompt mutations to the inner optimizer prompts —
    # set in THIS task's context copy, so it can't reach the outer's optimizer.
    set_optimizer_prompt_overrides(meta_prompt_overrides or None)
    # Clamp the inner optimizer's sampling temperature (determinism for measurement)
    # in THIS task only — so the inner campaign is a near-deterministic function of
    # its meta-prompt and the outer optimizer's own temperature is untouched. None
    # (unset in inner_tasks.json) leaves the optimizer's file temperature in place.
    if spec.inner_optimizer_temperature is not None:
        set_optimizer_config_overrides({"temperature": spec.inner_optimizer_temperature})

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
    # inner measurement must be the same size — anything less is wasted samples).
    #
    # The inner spend cap is sized off ``spec.n_rounds`` (+1 for the origin round), NOT pinned
    # to a constant. A constant cap that the round budget can outgrow silently converts the
    # ROUND budget into a COST budget — and because a verbose meta-prompt burns more per round,
    # it binds EARLIER for exactly the candidates ``delta_per_dollar`` exists to penalise, so
    # the confound gets scored as the signal. Sizing off ``n_rounds`` keeps the cap identical
    # across every candidate in an outer round. A None (unlimited) inner budget is still BOUNDED
    # here — an inner cycle must never run open-ended.
    inner_spend_cap = max(
        campaign_config.optimization.spend_budget_usd or 0.0,
        INNER_SPEND_PER_ROUND_USD * (spec.n_rounds + 1),
    )
    # Same round-budget sizing for the token ceiling — see INNER_TOKENS_PER_ROUND. Without this the
    # inner cycle inherits the flat 5-round default and token-starves a 7-round run mid-rounds.
    inner_token_cap = max(
        campaign_config.optimization.token_budget or 0,
        INNER_TOKENS_PER_ROUND * (spec.n_rounds + 1),
    )
    opt_update: dict[str, Any] = {
        "max_rounds": spec.n_rounds,
        "spend_budget_usd": inner_spend_cap,
        "token_budget": inner_token_cap,
    }
    if spec.n_variants is not None:
        # inner_n_variants (inner_tasks.json) — the outer task spec owns the inner
        # search width, same as it owns the round cap; the inner dataset's own
        # n_variants is a standalone-campaign default, not an L4 decision.
        opt_update["n_variants"] = spec.n_variants
    if spec.lives is not None:
        # inner_lives (inner_tasks.json) — `max_rounds` above is the ceiling a COMPOUNDING
        # inner campaign may reach; lives is what stops a STALLING one early. Same channel,
        # same ownership rationale as `n_variants`.
        opt_update["lives"] = spec.lives
    cfg_update: dict[str, Any] = {
        "sp_budget_ttest": len(train_data),
        "optimization": campaign_config.optimization.model_copy(update=opt_update),
    }
    # CRN (common random numbers): pin this cell's inner LLM seed on the dataset's
    # prompt-bearing node — `spec.seed` is the per-cell data-draw seed, already fixed
    # per cell and identical across every outer arm (origin + every variant) hitting
    # that cell. Every inner LLM call for this cell then shares one seed regardless of
    # which outer meta-prompt spawned it, so the inner's own run-to-run noise is common
    # and cancels in the (variant − origin) paired outer diff — for zero extra spend.
    # Rides the same sanctioned pipeline_overrides channel as the model override
    # below (merge, don't clobber, any dataset-level overrides).
    #
    # The node is ASKED, never assumed: a hardcoded `llm_only` wrote both overrides
    # under a nonexistent key on any inner dataset that named its node otherwise —
    # no error, no seed, and a paired diff quietly missing its variance cancellation.
    llm_node = session.llm_node_name()
    po: dict[str, Any] = {k: dict(v) for k, v in (campaign_config.pipeline_overrides or {}).items()}
    node = dict(po.get(llm_node, {}))
    node["seed"] = spec.seed
    if spec.inner_model:
        # The cell's target-model override (the panel's environment axis) rides the
        # same channel onto the inner dataset's single-call node — every in-band panel
        # cell is a single-call reasoning dataset.
        node["model"] = spec.inner_model
        if spec.inner_provider:
            node["provider"] = spec.inner_provider
    po[llm_node] = node
    cfg_update["pipeline_overrides"] = po
    campaign_config = campaign_config.model_copy(update=cfg_update)

    prepare_fresh_cycle(session, campaign_config, train_data)
    # Publish the freshly-minted inner cycle dir so the outer task's heartbeat
    # detail_fn can tail this run's dashboard.json (best {best}% / round X/Y).
    if session.campaign_id and session.state.cycle_id:
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

    An inner cycle that produced **no evidence** about the meta-prompt — it crashed, or
    never reached an L1 round, or every round lost its candidates — raises
    ``InnerCycleUnscoreableError`` from ``_compute_proxies`` (see ``_no_evidence_reason``,
    the one exclusion decision), so ``measure_sample`` excludes it as one error row
    (missing data, not a real proxy) instead of scoring a false floor that mimics a bad
    mutation. A completed inner run that merely failed to improve returns normally with
    poor proxies (measured, so a bad mutation is penalised). One excluded sample cannot
    kill the outer cycle."""
    ctx = _INNER_SPAWN.get()
    if ctx is None:
        raise RuntimeError(
            "promptpotter connector: no inner-spawn context published — "
            "run_optimization must call publish_inner_spawn_context first."
        )
    depth = _INNER_DEPTH.get()
    if depth >= MAX_INNER_DEPTH:
        raise RuntimeError(
            f"promptpotter connector: inner recursion is already {depth} level(s) deep "
            f"(MAX_INNER_DEPTH={MAX_INNER_DEPTH}); refusing to spawn another inner campaign. "
            "An inner dataset whose own backend_type is 'promptpotter' recurses without bound — "
            "check the inner_benchmark named in inner_tasks.json."
        )
    spec = _resolve_inner_task(ctx, query)
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
        # Lead with the running winner's LIFT over origin (delta) — the meaningful
        # number for a live run — and carry absolute best as context. Origin reads the
        # SAME cumulative basis as `best` (round-0 cumulative_accuracy), mirroring the
        # webapp's `headlineStats` so the two surfaces can't disagree.
        origin = next(
            (r.get("cumulative_accuracy") for r in dash.get("rounds") or [] if r.get("round") == 0),
            None,
        )
        if isinstance(best, int | float) and isinstance(origin, int | float):
            lift = f"Δ{best - origin:+.0%} (best {best:.0%})"
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
    inner_task = asyncio.create_task(_run_inner_campaign(ctx, spec, overrides, cycle_dir_box))
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
    # No exclusion decision here: `_compute_proxies` asks `_no_evidence_reason` and raises
    # `InnerCycleUnscoreableError`, which `measure_sample`'s catch-all turns into this sample's
    # EXCLUDED row. The row already names the sample, so the message carries only the reason.
    proxies = _compute_proxies(result, spec.target, elapsed)

    data: dict[str, Any] = {
        # Terminal-ranker head = the inner-result token (`inner:{query}` — the
        # connector's `_extract_experiment` sets `ground_truth` to the same
        # prefix; keep the two in sync) plus a compact outcome suffix so the
        # outer diagnostics/transcripts show the movement, not an identity
        # string. Safe: the outer formula reads only the proxy scalars — no
        # consumer matches predicted against ground_truth (outer hit is
        # `fitness >= 1.0`), and the round-0 health gate only needs a
        # non-empty, non-NO_RESULT prediction.
        INNER_RESULT_KEY: [
            f"inner:{query} D{proxies.after_N_rounds_delta:+.3f}/r{proxies.rounds_to_N}"
        ],
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
