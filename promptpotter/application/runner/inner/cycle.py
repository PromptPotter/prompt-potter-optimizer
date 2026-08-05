"""L4 inner-cycle runner — the recursion arm of the ``promptpotter`` connector.

One outer "sample" = one inner campaign on a cheap proxy benchmark, scored by how much
the inner loop improved (``domain/l4/proxies.py`` is that law; nothing restates it).
Design: ``docs/specs/l4-outer-loop.md`` § 2 + § 4.

Two isolations make the recursion re-entrant, so L5+ nests by construction and no code
here may assume depth 1:

- **Its own ``asyncio.Task``.** The runner's ContextVars (ledger, round stamp, abort
  predicate) isolate per task, not per call — a nested ``await run_optimization`` in the
  outer's task would clobber all three. Spawning copies the context instead, and the abort
  predicate is the one the inner run READS back (`_bind_run_controls` composes rather than
  overwrites), so a pause on the owner stops the instrument.
- **A FLAT sandbox home**, ``<workspace>/.inner/<key>`` keyed on the owning
  (tenant, campaign, cycle), a sibling of
  ``projects/`` rather than a child of the outer cycle dir. Physical nesting blows past
  Windows' ``MAX_PATH`` at depth 1 and is hopeless by L5; flat stays shallow at every
  depth. The inner tree never touches the outer's listing, pointer or SSE stream.

Instrument mode — what makes this a measurement rather than a campaign — is declared in
``shared/instrument.py`` alone; read it before changing any of what it binds. The outer's
optimizer prompt overrides are the SPECIMEN under test, not part of the instrument, and are
applied inside the inner task so they cannot leak outward. Inner spend rolls up as the
outer sample's backend cost, read from live state at finalize rather than the debounced
``dashboard.json``, which would race.
"""

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
    OUTER_PROXY_KEYS,
    InnerCycleUnscoreableError,
    compute_outer_proxies,
    floor_reason,
    held_levels,
    mean_round_delta_se,
)
from promptpotter.domain.phases import RunPhase
from promptpotter.domain.pipeline_schema import stable_hash
from promptpotter.infrastructure.store.io import read_json_optional, write_json
from promptpotter.infrastructure.store.layout import (
    CycleLayout,
    inner_sandbox_dir,
    sandbox_owner_path,
)
from promptpotter.shared.instrument import (
    MAX_INSTRUMENT_DEPTH,
    MeasurementRole,
    instrument_depth,
    measured_candidate,
)

if TYPE_CHECKING:
    from promptpotter.application.config import CampaignConfig
    from promptpotter.application.initialization.session import Session
    from promptpotter.domain.results import CycleResult, CycleSpend
    from promptpotter.domain.sample import Sample
    from promptpotter.shared.identity import IdentityContext

logger = logging.getLogger(__name__)


# The terminal-ranker key the outer `promptpotter-self` pipeline reads as its
# prediction (a non-empty list keeps the origin round-0 health gate from halting
# on all-NO_RESULT) + the composed-fitness proxy scalars the outer scoring formula
# reads. `datasets/promptpotter-self/pipeline.yaml::nodes.l1_critique.optimizer
# .observation_mappings` declares these as observation keys, so they reach
# `pipeline_data` and the formula namespace (`scoring/formula/compiler.py`).
INNER_RESULT_KEY = "final_ranking"

# An inner cycle's budget is its ROUND budget — `max_rounds` (the ceiling a compounding run may
# reach) and `lives` (what stops a stalling one early). Both are DETERMINISTIC, and the outer
# proxies are defined over exactly them.
#
# It deliberately carries no spend or token cap. Those trip on MEASURED token counts, which jitter
# run to run (reasoning tokens are not reproducible) — so the same optimizer prompt halted at a
# different round on different runs, and the resulting truncated trajectory is indistinguishable
# from "this optimizer prompt found nothing". That made provider mood a fitness signal, and it was a
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
    campaigns: ``<workspace>/.inner/<key>``, where the key identifies the OWNING
    ``(tenant, campaign, cycle)`` — see ``store/layout.py::inner_sandbox_key``. It is
    owned by the spawning cycle but NOT physically nested under its deep campaign dir —
    physical nesting (``…/.runtime/inner/…/.runtime/inner/…``) blows past Windows'
    260-char ``MAX_PATH`` at depth 1, and would be hopeless at L5+. A flat registry
    stays shallow at EVERY recursion depth (an L5 cycle gets its own key), so the
    re-entrancy invariant holds without the path-length trap. Still out of the
    ``projects/`` tree, so inner campaigns never show in the outer campaign listing.
    ``dataset_config_dir`` is the spawning campaign's config dir, read for
    ``inner_tasks.yaml``; ``identity`` roots the sandbox stores under the same tenant.

    ``shared_root`` is the REAL workspace root, carried through so the inner store keeps
    its ``archive`` + ``optimizer_calls`` tenant-global while its campaign state stays
    sandboxed. Sandboxing those caches too meant every outer cycle re-scored every inner
    origin from scratch — and because an inner origin is stochastic, it redrew a different
    accuracy each time (observed: the same content hash on the same 24 samples scoring
    0.375 in seven sandboxes and 0.417 in two). The outer fitness subtracts that origin,
    so the isolation injected a noise term larger than the lift it was measuring.

    ``spawn_campaign_id`` / ``spawn_cycle_id`` are the OUTER campaign + cycle that OWN this
    sandbox — carried explicitly rather than re-parsed off ``inner_sandbox_root.name``, so
    the provenance an inner campaign stamps names its parent by fact, not by string surgery
    on a path. Since the key became a hash, re-parsing is not merely fragile but impossible,
    and these two are also what the sandbox records in its ``owner.json``.

    ``asking_cycle_id`` is a DIFFERENT fact and the two were one field: which cycle is asking
    for this measurement right now. They diverge the moment a resume forks — the fork asks,
    while the sandbox stays owned by the cycle it was cut from, deliberately, because that is
    what lets a repaired cell CONTINUE the campaign the parent banked instead of restarting
    it from zero. Stamping the owner as the asker filed every measurement a fork paid for
    under the cycle it superseded, where the lineage could only match it back by
    ``candidate_label`` — a counter each course mints its own copy of — so a fork's ``C2.1``
    landed on the parent's ``C2.1`` and the live work appeared on the retired branch."""

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
    # Flat, shallow sandbox home keyed on the FULL owner identity — see
    # ``store/layout.py::inner_sandbox_key`` for why all three parts are needed and why the
    # key is a hash. Anchored on ``shared_root`` (the REAL workspace root, invariant across
    # depth), never this store's ``projects_root``, which inside a sandbox already IS the
    # sandbox.
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
    """Re-point the provenance at the cycle now running, leaving the sandbox where it is.

    Called once the cycle id is FINAL — a resume can mint a repair fork and retarget the
    pointer well after :func:`publish_inner_spawn_context` ran, and the spawn context is
    published at the top of the runner seam because a child may recurse before any of that
    resolves. Only the asker moves; the sandbox owner must not, or the fork's cells would
    look for their banked campaigns in a sandbox that has none and re-run every one.
    """
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
    """Which outer work-item is asking for this measurement — stamped on the inner cycle.

    Without it an inner campaign is anonymous: its ``campaign_id`` is random and its
    ``cycle_id`` is a hash of its OWN origin, so nothing on disk says which outer round
    or candidate produced it, and the sidebar can only number runs by launch order.

    A work-item is (candidate × ``task``), not a candidate: the panel runs EVERY task
    per candidate, so one candidate's spawns are as many as ``inner_tasks.yaml`` has
    cells (seven for ``promptpotter-self``). ``task`` is the outer QUERY — the panel
    cell's id, e.g. ``justlogic-d234/seed-0`` — and it is the only thing telling those
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
        # The ASKER, not the sandbox owner — a repair fork asks while the parent still owns
        # the sandbox, and this is the join the lineage hangs an inner run off.
        "outer_cycle_id": ctx.asking_cycle_id,
        # The campaign half. A cycle_id is content-addressed on its origin, so it is SHARED
        # by every campaign minted from that origin — stamping it alone recorded half an
        # identity, the same defect the sandbox key itself had.
        "outer_campaign_id": ctx.spawn_campaign_id,
        "round": round_num,
        "candidate_idx": cand.idx if cand else None,
        "candidate_id": cand.candidate_id if cand else None,
        "candidate_label": (
            cand.label if cand else (candidate_label(0, 0) if round_num == 0 else None)
        ),
        # WHY this cell ran, not just for whom. A backfill's inner campaign is spawned
        # outside the round's shared order to fill a paired comparison; without this the
        # record cannot be told apart from the candidate's own panel cell, and reading one
        # as the other is what made a repaired round unreproducible.
        "role": cand.role.value if cand else None,
        "task": query,
    }


def _verify_outer_panel_contract(
    session: Session, campaign_config: CampaignConfig, dataset_dir: Path
) -> None:
    """An outer dataset must DECLARE every key its inner samples emit, and must budget for
    exactly the panel it declares — both checked once, at the seam that arms the recursion,
    against the schema and config the campaign actually loaded.

    A dataset that owns the connector's declared ``experiment_file`` IS an outer dataset
    (that file is what :func:`resolve_inner_task` reads), so no name test is needed to
    recognise one — and asking the connector, rather than spelling the name a second time,
    is what keeps this probe from silently disagreeing with the loader. An
    emitted-but-undeclared key is dropped on the floor by ``sample_measurement`` and never
    reaches ``pipeline_data`` — so the scoring formula either dies on a name it cannot see
    (loud, but a run in) or, worse, the observation quietly never lands in the archive and
    the what-if panel scores a term nobody measured. Fail at arm time instead."""
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
    # The panel and the round budget are ONE declaration in two files, and nothing joined
    # them: `inner_tasks.yaml` lists the cells, `campaign.yaml::sp_budget_ttest` says how
    # many a round draws, and their equality was asserted only in a comment. A budget BELOW
    # the panel narrows it silently — and with `per_round_resubset` on, different rounds
    # then draw different cells, so candidates are compared across rounds on bases that
    # never matched. `_check_sp_budget_vs_dataset` warns in the other direction only (budget
    # above the data), so this direction had no reader at all.
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
    """Whitespace-normalize + head-clip at a word boundary with a visible marker."""
    text = " ".join(text.split())
    if len(text) <= cap:
        return text
    return text[: cap - 1].rsplit(" ", 1)[0] + "…"


def _lift_shape(result: CycleResult) -> str:
    """Which rounds LIFTED — the cell's search shape, one line.

    A healthy inner search has a shape, not just a total: most cells lift in round 1 (the
    easiest round on the board — most headroom, cleanest evidence), about half again in round
    2, the ones that missed round 2 land in round 3, and lifts thin out as the cell saturates.
    A cell that lifts once and flatlines, or never lifts at all, is a DIFFERENT failure from
    one that climbs steadily to the same total — and the scored scalar cannot tell them apart.

    Read from ``RoundResult.improved``, which is a within-round paired verdict against the
    matched origin on the same samples. That is what makes this line worth its characters: it
    never touches the per-cycle θ anchor, and both sides of its pair see the same re-drawn
    subset, so it carries neither of the noise terms the scalar does. A panel of 6 cells over 4
    rounds is ~24 of these verdicts against 6 scalars — the shape is legible at a panel size
    where a 0.077-logit contrast is not.

    The denominator is the ROUND BUDGET, the same one the scored scalar divides by
    (``held_levels``). It read the rounds that RAN, which put two different denominators one line
    apart in a narrative the outer generator reads whole: a cell that stopped at 2 of 4 showed
    "1/2" beside a scalar averaged over 4, so the same search looked half as productive or twice
    as productive depending on which line was believed. A short cell has unlifted rounds, and
    saying so is the point — ``lives`` stops a STALLING cell, so the rounds it never ran are
    exactly the ones it was not going to lift in.
    """
    l1 = [rr for rr in result.rounds if rr.round > 0]
    marks = " ".join(f"r{rr.round}{'+' if rr.improved else '.'}" for rr in l1)
    if not marks:
        return "lifts: none — no L1 round closed."
    n = sum(1 for rr in l1 if rr.improved)
    budget = max(result.round_budget, len(l1))
    return f"lifts: {marks} ({n}/{budget}; target: early and often, thinning late)"


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
    to say WHY an optimizer prompt mutation helped or hurt."""
    # A floored cycle has no trajectory to narrate — say why it was floored instead.
    if (floor := floor_reason(result)) is not None:
        return (
            f"INNER {spec.inner_dataset} seed-{spec.seed}: {floor} — scored at the floor "
            f"(optimizer-prompt-owned); stop={result.stop_reason}."
        )
    # Narrated only for a cycle that carried evidence, so both are present (`compute_outer_proxies`
    # raised otherwise). No `or 0.0`: an origin that was never scored has no level to narrate.
    assert result.origin_level is not None
    origin = result.origin_level
    levels = result.round_adopted_levels
    # Lead with `mean_round_delta`, the term the outer formula scores. Showing the generator a
    # headline it is not graded on teaches the wrong lesson, so this line moves whenever the
    # measurand does — it has now led with the mean, then the endpoint, then the mean again.
    # The endpoint rides along, and the peak beside it, because "ended well below peak" is
    # exactly the collapse worth reading. `held_levels`, not `levels`: the law averages over the
    # round BUDGET, and a headline dividing by the rounds that ran would disagree with the score.
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
            # Rank by lift over the MATCHED origin where there is one, and never invent the
            # comparison where there is not: an arm that did not cover the origin's panel
            # carries no matched origin, so `accuracy - 0.0` would have handed it its whole
            # accuracy as lift and floated it to the top of exactly the sentence the outer
            # optimizer learns from. `-inf` keeps the story on an arm that ran the panel — the
            # ones with the shortest prefixes are the ones a prefix rate flatters most.
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
            # `repeat` rides the inner narrative because it is the anomaly the OUTER generator
            # most needs: it says the inner loop stopped forming new hypotheses, which is a
            # optimizer prompt defect, not a task difficulty.
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
    # Enforce the authored budget: on a deep inner run, drop the EARLIEST round
    # lines first (the trajectory's tail is the informative end) rather than
    # letting the panel's head-keep clip silently cut the latest rounds.
    # Headline + lift shape are always kept; the highlight when there is one.
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
    """The campaign a ``(cell, optimizer-prompt-overrides, purpose)`` triple OWNS here.

    Content, never asker. An inner campaign's behaviour is decided entirely by the cell it
    runs and the optimizer prompts it runs under, so those two ARE its identity — and
    keying on them is what lets a re-measured cell find the rounds a previous attempt
    banked instead of starting over at round 0.

    **Purpose is the third component, and omitting it conflated two different
    measurements.** A candidate's own panel cell is measured in the round's shared order;
    a PoBB ``BACKFILL`` catches a PRIOR up on a sample out of that order, for someone
    else's paired comparison. Same cell, same overrides, different experiment — so they
    must own different directories, or the two collapse and whichever ran second either
    lands on top of the other or (with random ids) forks the data into duplicates.

    The discriminator is deliberately BINARY, not the four-way role: ``REPAIR`` and
    ``PARENT`` measure an individual's own evidence and so share ``PANEL``'s identity —
    repair exists precisely to CONTINUE the panel campaign, and giving it its own id would
    make it start a fresh run every time, which is the bug it was written to fix.

    The obvious key, ``cycle_id``, does not work: it is a benchmark-cell hash and therefore
    **collides across candidates** — ``cycle_19ab182342b7`` is shared by C0/seed-3,
    C1.1/seed-3 and two C1.2/seed-3 campaigns. The overrides are what tell them apart.
    The outer half of the identity is not lost either; the sandbox directory already IS
    ``(tenant, outer campaign, outer cycle)`` (``inner_sandbox_key``), so sandbox path +
    content key is a complete identity with each half owned in exactly one place.

    ``[:6]`` matches ``mint_campaign_id``'s ``token_hex(3)`` width so continued campaigns
    cost no more path length than minted ones (Windows ``MAX_PATH``, ``store/layout.py``).
    """
    purpose = "backfill" if role is MeasurementRole.BACKFILL else "own"
    digest = stable_hash([spec.model_dump(mode="json"), overrides, purpose])[:6]
    return f"{spec.inner_dataset}__{digest}"


def _banked_inner_rounds(ctx: InnerSpawnContext, campaign_id: str) -> int:
    """Round records already on disk for *campaign_id* in this sandbox; 0 if it is new.

    Read in the OUTER task, before the inner campaign is spawned, because the wall-clock
    deadline is the outer task's to set and a continued cell must be budgeted over the
    rounds that REMAIN. A cell resuming with 3 of 5 rounds banked that inherits the full
    budget is not bounded by it in any useful sense.

    Deliberately a directory read rather than a store call: the campaign id is
    content-addressed and owns exactly one root cycle, so the glob (the same shape
    ``reaper.py::reap_cycle_by_id`` uses) answers without building a Session — which does
    not exist yet out here.
    """
    indexes = ctx.inner_sandbox_root.glob(f"*/campaigns/{campaign_id}/cycles/*/index.json")
    return max(
        (len((read_json_optional(p) or {}).get("rounds") or []) for p in indexes),
        default=0,
    )


def _open_inner_campaign(
    session: Session,
    campaign_config: CampaignConfig,
    train_data: list[Sample],
    *,
    campaign_id: str,
) -> int:
    """Bind *session* to this cell's campaign, minting only if it does not exist yet.

    Returns how many round records are already banked (0 for a fresh mint) — the caller
    budgets its wall clock over the rounds that REMAIN, not the whole cycle again.

    Continuing rather than re-minting is the whole point: an abandoned inner campaign
    still holds every round it finished, and the previous behaviour (always
    ``prepare_fresh_cycle``, always a fresh random ``campaign_id``) orphaned them where no
    reader would ever find them and re-ran the cell from round 0 against the same wall
    clock it had just blown.

    Eligibility is asked of :func:`derive_run_phase` — the single run-phase derivation —
    and the rule is **continue unless something is live on it**. ``stop_reason_outcome`` is
    deliberately not the question here: that governs *scoring* (only a SUCCESS cycle is a
    measurement, ``domain/l4/proxies.py``) and stays there. For *resumption* every terminal
    class continues, including ``PRODUCER_VANISHED`` — which is precisely the reaped state
    this exists to recover. A cycle that finished successfully re-enters with
    ``clean_rounds >= max_rounds``, runs zero rounds, and returns its fully replayed
    trajectory: no spend, full proxies.

    Binding is all this does. The terminal latch a continued cycle still carries is
    cleared one layer down, in ``init_cycle`` — the seam BOTH levels pass through —
    so an outer resume past a stop is un-latched by the same rule, not a second copy of it.
    """
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
    """Mint + run one sandboxed inner campaign; return its ``CycleResult``.

    The result carries ``.spend`` (the inner run's total, captured from its live
    dashboard state), so the caller rolls the inner cost up without touching the
    sandbox's ``dashboard.json``.

    Runs in a FRESH task (the caller spawns it) so the per-task ContextVars are
    isolated from the outer cycle. Sets the per-run optimizer-prompt override
    ContextVar here (inner task only) so the outer L1's optimizer prompt mutations
    shape the inner ``assets/optimizer/`` prompts without leaking to the outer.

    ``cycle_dir_box`` is a mutable holder the caller reads from its outer-task
    heartbeat: once the inner cycle is minted its dir is published here, so the
    heartbeat's ``detail_fn`` can tail the inner ``dashboard.json`` for a live
    ``"inner rX/Y · best Z%"`` line while this runs (the outer chat/dashboard
    would otherwise go silent for the whole multi-minute inner campaign)."""
    # Lazy imports: heavy application machinery, and `run_optimization` would be a
    # package-internal import cycle (`entry.py` imports `publish_inner_spawn_context`
    # from here). Deferring to call time keeps this module import-light.
    from promptpotter.application.config import load_campaign_config
    from promptpotter.application.datasets.authored import (
        dataset_campaign_path,
        read_campaign_config_file,
    )
    from promptpotter.application.initialization.wiring import init_services
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

    # Apply the outer L1's optimizer prompt mutations to the inner optimizer prompts — set in THIS
    # task's context copy, so they can't reach the outer's optimizer. This is the SPECIMEN under
    # test, not part of the instrument: the same channel also carries a normal outer cycle's own
    # optimizer prompt SET (`optimizer_set`, bound at the runner seam), so it is not mode-gated.
    set_optimizer_prompt_overrides(optimizer_prompt_overrides or None)

    # Sandbox: the inner tenant tree roots at the spawning cycle's flat, shallow
    # `<workspace>/.inner/<key>` home (re-entrant + Windows MAX_PATH-safe; see
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
    # Who owns this sandbox, written where a human and the orphan reaper can both read it.
    # The directory name is a hash (MAX_PATH — see `store/layout.py::inner_sandbox_key`), so
    # this file is the only place the three owner names survive. Written here rather than at
    # `publish_inner_spawn_context`, which fires for EVERY cycle and would mint an empty
    # sandbox dir for every non-L4 run.
    write_json(
        sandbox_owner_path(ctx.inner_sandbox_root),
        {
            "tenant_id": str(ctx.identity.tenant_id),
            "campaign_id": ctx.spawn_campaign_id,
            "cycle_id": ctx.spawn_cycle_id,
        },
    )

    # THE declaration: this cycle is a measurement instrument, not a campaign. One call binds
    # every hermetic property together (recursion depth, the evidence epoch, the optimizer
    # decoding clamp) in THIS task's context copy, so none of it reaches the outer cycle and
    # none of it can be forgotten piecemeal by a future code path. `inner_optimizer_temperature`
    # unset (inner_tasks.yaml) leaves the optimizer's file decoding alone; the seed is the
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
        stores=store,
        enable_tracing=False,
    )
    all_samples = session.samples
    if not all_samples:
        raise ValueError(f"inner dataset {spec.inner_dataset!r} loaded zero samples")
    n = min(max(spec.n_samples, spec.n_samples_origin or 0), len(all_samples))
    # The draw is spelled once, in the screen that CHOOSES seeds — a runner that drew
    # differently would run a bank nobody screened, and nothing would report the divergence.
    train_data = draw_bank(all_samples, n, spec.seed)
    # The constant-answer floor of the bank about to run. Free (no measurement — it is the
    # majority-class share of the ground truths) and computed HERE because this is the first
    # moment the drawn rows exist. `seed-screen` defines the disqualifier — a bank whose floor
    # exceeds its origin pays more for giving up than for reasoning — but it is a diagnostic the
    # operator runs by hand, so nothing recomputed it for the seats actually seated, and a
    # re-cut could re-admit a collapse-rewarding bank in silence. Reported, never enforced: one
    # origin pass is ~0.08 SE on 40 rows, and rejecting a seat on it is the single-pass error the
    # screen itself stopped making.
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

    # Continue this cell's own campaign when one already exists — a retry must not orphan
    # the rounds the previous attempt banked.
    _open_inner_campaign(
        session,
        campaign_config,
        train_data,
        campaign_id=inner_campaign_id(spec, optimizer_prompt_overrides, spawn_role),
    )
    if session.campaign_id and session.state.cycle_id:
        # Stamp WHO asked for this measurement onto the cycle index. Written here rather
        # than threaded through `prepare_fresh_cycle` → `auto_mint_session`: those are the
        # generic mint seam every campaign shares, and this is an L4-only fact, so it stays
        # in the L4 module. The cycle index is a raw dict (no model, no `extra="forbid"`),
        # so the key costs nothing and no prior inner cycle needs re-stamping — an older
        # one simply has no `spawned_by` and falls back to its origin hash.
        session.store.campaigns.update(session.hop, {"spawned_by": spawned_by})
        # Publish the freshly-minted inner cycle dir so the outer task's heartbeat
        # detail_fn can tail this run's dashboard.json (best {best}% / round X/Y).
        cycle_dir_box["dir"] = session.store.campaigns.cycle_dir(session.hop)
    task_context = await load_or_build_task_context(
        session.store,
        session.dataset_name,
        campaign_id=session.campaign_id,
        context=checkin_call_context(session.store, session.hop),
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
    # A bank that pays MORE for answering one label than for reasoning cannot measure an
    # optimizer prompt — the whole gradient of that cell points at collapse. `seed-screen`
    # defines this disqualifier, but it is a hand-run diagnostic, so nothing ever recomputed it
    # for the seats that actually ran and a re-cut could re-admit such a bank in silence.
    # REPORTED, never enforced: one origin pass is ~0.08 SE on 40 rows, so rejecting a seat on a
    # single read is precisely the single-pass error the screen itself stopped making.
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
    """Run one inner campaign for an outer query; return the scorer-shaped result.

    The ``promptpotter`` connector's ``in_process_run`` arm. Resolves the inner
    task from ``inner_tasks.yaml``, runs the campaign in a fresh ``asyncio.Task``
    (ContextVar isolation), and projects the three proxy metrics onto the
    ``{"data": {…}}`` shape ``measure_sample`` parses from an HTTP body — so the
    outer scorer reads an inner result identically to a remote one.

    An inner cycle that produced **no evidence** about the optimizer prompt for a NO-FAULT
    reason — it crashed as tooling, or never reached an L1 round — raises
    ``InnerCycleUnscoreableError`` from ``compute_outer_proxies`` (see ``no_evidence_reason``,
    the one exclusion decision), so ``measure_sample`` excludes it as one error row
    (missing data, not a real proxy). An optimizer prompt-OWNED evidence kill — every L1 round
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
            "without bound — check the inner_benchmark named in inner_tasks.yaml."
        )
    spec = resolve_inner_task(ctx, query)
    overrides = payload.get("optimizer_prompt_overrides") or {}

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
    from promptpotter.infrastructure.llm.telemetry import _CURRENT_ROUND, _CYCLE_LEDGER

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
        dash = read_json_optional(CycleLayout(cycle_dir).dashboard)
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
    # Captured in the OUTER task, like `spawned_by` above and for the same reason: the
    # inner task gets a COPY of this context and its own candidate loop rebinds the stamp,
    # so a read over there would return the inner run's candidate, not the asker.
    spawn_role = cand.role if (cand := measured_candidate()) else MeasurementRole.PANEL
    inner_task = asyncio.create_task(
        _run_inner_campaign(ctx, spec, overrides, cycle_dir_box, spawned_by, spawn_role)
    )
    # The ONE bound on this sample's total wall clock. Awaiting `inner_task` DIRECTLY makes it
    # this coroutine's `_fut_waiter`, so the timeout's cancellation propagates into the inner
    # campaign and it really stops. Do not wrap the await in `asyncio.shield` or `asyncio.wait`:
    # both detach the campaign from the cancellation, orphaning it to keep calling the optimizer
    # and billing tokens against a sample nobody will read.
    # Budget the rounds that REMAIN, not the whole cycle again. A continued cell re-enters
    # its own campaign holding everything the last attempt banked, so charging it the full
    # budget for the tail leaves the wall bounding almost nothing — the failure the
    # continuation would otherwise import from the abandonment it fixes. `max(1, …)` keeps
    # one round's grace for a fully-banked cycle to replay its priors and finalize.
    banked = _banked_inner_rounds(ctx, inner_campaign_id(spec, overrides, spawn_role))
    deadline_s = OUTER_SAMPLE_WALL_S_PER_ROUND * max(1, (spec.n_rounds + 1) - banked)
    # Constructed here rather than inline in the `async with` so the heartbeat's suspend hook
    # below can close over it. `asyncio.timeout` fixes its absolute `when` at CALL time, so
    # `when()` is already readable; only `reschedule` requires the context to have been entered.
    deadline = asyncio.timeout(deadline_s)

    def _give_back_suspended_time(overshoot: float) -> None:
        """Push the deadline out by wall time the MACHINE spent asleep.

        The deadline bounds how long this cell may SPEND, and a suspended machine
        spends nothing. Without this, an overnight sleep hands the next tick a
        budget that expired while nothing ran: one cell here was cancelled at wake
        having completed a single inner round in ~2s of real work, and the
        abandoned campaign was then reaped as `producer_vanished` — a hole in the
        panel manufactured entirely by the laptop lid.
        """
        when = deadline.when()
        if when is None:  # pragma: no cover — only for `timeout(None)`, never used here
            return
        try:
            deadline.reschedule(when + overshoot)
        except RuntimeError:
            # The `async with` already exited and this tick landed before the
            # `finally` cancelled us. Nothing left to extend.
            return
        logger.warning(
            "inner cell %s: machine suspended ~%.0fs mid-campaign; extending the "
            "%.0fs wall-clock deadline by that much rather than charging it as work",
            query,
            overshoot,
            deadline_s,
        )

    heartbeat_task = asyncio.create_task(
        # NOT an optimizer node name. What is running here is a whole inner campaign
        # — many rounds, every optimizer node inside it — so naming it after one of
        # them charges that node with the entire campaign's wall clock: a healthy
        # 8-round run read as `l1_critique still waiting · 1444s`, and the obvious
        # conclusion was that critique had hung (it averages 30s). The
        # `step_timings`/`step_tokens` keying below is a different question with a
        # real answer (spend attribution to the ranker node) — do not re-align this
        # display label to it.
        #
        # Created UNCONDITIONALLY, even with no outer ledger to append to (`heartbeat`
        # tolerates `None`): this task now also carries the deadline's suspend guard,
        # and gating it on a telemetry sink would silently disarm that guard wherever
        # the sink happens to be absent.
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
            # The deadline does not take the callee's word for it. ``asyncio.timeout`` raises
            # TimeoutError only if a CancelledError comes back up, so anything in the inner
            # chain answering a cancellation with a normal return makes the guard vanish
            # silently and an over-deadline campaign scores as a real measurement. Ask the
            # clock rather than trust that no such site appears.
            if deadline.expired():
                result = None
        except TimeoutError:
            result = None
    finally:
        # A campaign that outlived its deadline without answering the cancellation is still
        # running, still calling the optimizer, and still billing tokens against a sample
        # nobody will read. Insist.
        if result is None and not inner_task.done():
            inner_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await inner_task
        # Cancel the heartbeat whether the inner run returned or raised — an
        # in-flight task would otherwise keep appending against a finished sample.
        heartbeat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat_task
    if result is None:
        # Name the directory the abandoned campaign is IN. The reason reaches the operator
        # through the outer ERROR row, but that row names only the cell — so the on-disk
        # campaign it abandoned, holding real banked rounds, was unfindable from either
        # surface. Reading `.goldmine/latest.log` beside the tree is how the two get joined.
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
        INNER_RESULT_KEY: [f"inner:{query} D{proxies.mean_round_delta:+.3f}"],
        # The outer loop's raw evidence: a <=1150c narrative of what the inner
        # search tried, what steered it, and what moved — rendered as MODEL
        # REASONING in the outer sample_transcripts panel.
        "reasoning_trace": _inner_narrative(result, spec),
        **proxies.model_dump(),
        # The cell's own precision on the scalar above, so the panel can tell estimation noise
        # from between-cell heterogeneity instead of inferring both from one spread of six
        # numbers. An INFRA key, deliberately not an `OuterSampleProxies` field: those are
        # derived into `OUTER_PROXY_KEYS` and reach the scoring formula's namespace, and a
        # standard error inside the formula is one keystroke from the `mean - λ·se` haircut the
        # spec forbids. Precision travels beside the measurement; it never grades it.
        "mean_round_delta_se": mean_round_delta_se(result),
        # terminated_at is the archive's reuse contract: a named node means "the
        # sample's outcome depends on config only UP TO that node", and prefix-
        # matched rows replay when they terminated inside the trusted prefix
        # (MeasurementArchive.load_reusable_results). An inner campaign consumes
        # the ENTIRE outer config at once, so the only honest stamp is the LAST
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
