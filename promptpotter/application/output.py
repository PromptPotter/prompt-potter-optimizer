"""Operator-facing markdown writers — log.md / review.md / hard_samples.json.

Called from runner milestones (not RunCallbacks — those stay display-only).
Two log.md tiers: per-cycle (``cycles/{cycle_id}/log.md``) + campaign
digest (``campaigns/{campaign_id}/log.md``).

Owns disk-side view reconstruction (``from_disk_log``): a persisted ``index.json``
→ the same ``LogMdView`` shape the live ingress emits, so ``to_markdown`` has one
schema. (The round twin, ``from_disk_round``, had no callers and is deleted.)

This is an **orchestrator** (computes artifacts + writes disk), so it lives in
``application/`` next to the runner that calls it — not in ``presentation/``,
whose entry-point shells must stay read-only over orchestration. It renders
through the presentation view layer (``to_markdown`` + the typed view models),
which is the pure data → text surface shared with the live ingress."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.application.intelligence.exploration import build_observations
from promptpotter.application.intelligence.hard_sample_archive import (
    build_archive_hard_samples_artifact,
)
from promptpotter.application.intelligence.hard_sample_sorter import (
    build_hard_samples_artifact,
    build_hard_samples_artifact_from_observations,
)
from promptpotter.application.optimization.l1.stats import L1Stats, compute_l1_stats
from promptpotter.application.optimization.validators.l1_behavior import (
    CHECK_REGISTRY,
    CheckResult,
    ValidatorContext,
    extract_l1_variants,
    run_all_checks,
)
from promptpotter.application.optimization.validators.l2_behavior import run_all_l2_checks
from promptpotter.application.views.render import to_markdown
from promptpotter.application.views.view_models import (
    DigestStatusView,
    FinalWinnerView,
    ForkSummaryView,
    HardSamplesView,
    LogMdView,
    RoundDigestView,
)
from promptpotter.domain.escalation_signals import exploration_budget
from promptpotter.domain.phases import StopReason
from promptpotter.domain.rendering import format_l1_critique_for_prompt
from promptpotter.domain.results import (
    DegradationHealth,
    RoundResult,
    ScoredCandidate,
    candidate_label,
)
from promptpotter.infrastructure.projections.audit_trail import load_round_audits
from promptpotter.infrastructure.store.campaign_store.store import origin_accuracy_of
from promptpotter.infrastructure.store.io import read_json_tolerant, write_json, write_text
from promptpotter.infrastructure.store.layout import CycleLayout, campaign_cycles_dir
from promptpotter.infrastructure.store.read_model import iter_jsonl
from promptpotter.shared.errors import graceful

__all__ = ["render_review_md"]


def render_review_md(
    index: dict[str, Any],
    rounds: list[RoundResult],
    *,
    round_audits: list[dict[str, Any] | None] | None = None,
    context_object: list[str] | None = None,
    l1_patience: int,
) -> str:
    audits = list(round_audits or [None] * len(rounds))
    if len(audits) < len(rounds):
        audits.extend([None] * (len(rounds) - len(audits)))
    ctx_items = [c for c in (context_object or []) if isinstance(c, str) and c.strip()]

    behavior_per_round, l2_behavior_per_round = _compute_behavior_per_round(
        rounds, audits, ctx_items, l1_patience
    )
    final = index.get("final") or {}
    origin_composite_fitness = float(final.get("origin_composite_fitness") or 0.0)
    stats = compute_l1_stats(
        list(rounds),
        origin_composite_fitness=origin_composite_fitness,
        behavior_results=behavior_per_round,
        l2_behavior_results=l2_behavior_per_round,
    )

    repairs_per_round = [_schema_repair_count(a) for a in audits]
    calls_per_round = [_optimizer_call_count(a) for a in audits]
    halt = _halt_info(index, rounds)
    parts: list[str] = []
    parts += _render_header(index, final, stats, halt)
    parts += _render_stats_block(stats, repairs_per_round, calls_per_round, halt)
    parts += _render_behavior_summary(behavior_per_round)
    parts += ["## Rounds", ""]

    sweep_mode = (final.get("mode") or "").strip() == "sweep"
    last_idx = len(rounds) - 1
    for i, round_data in enumerate(rounds):
        is_peek = sweep_mode and i == last_idx and _is_generation_only(round_data)
        parts += _render_round(
            round_data,
            audits[i],
            behavior_per_round[i] if i < len(behavior_per_round) else [],
            is_peek=is_peek,
            schema_repair_retries=repairs_per_round[i],
        )

    return "\n".join(parts).rstrip() + "\n"


def _schema_repair_count(audit: dict[str, Any] | None) -> int:
    """Sum ``schema_repair_attempts`` across optimizer nodes; non-zero ⇒ a second round-trip was paid.
    Cycle-wide rate is the cleanest single-number quality signal for an L1 meta-prompt."""
    if not audit:
        return 0
    nodes = audit.get("nodes") or {}
    if not isinstance(nodes, dict):
        return 0
    return sum(
        int(block.get("schema_repair_attempts") or 0)
        for block in nodes.values()
        if isinstance(block, dict)
    )


def _optimizer_call_count(audit: dict[str, Any] | None) -> int:
    """Count optimizer LLM nodes in this round's audit (excludes ``l1_score``)."""
    if not audit:
        return 0
    nodes = audit.get("nodes") or {}
    if not isinstance(nodes, dict):
        return 0
    return sum(1 for k, v in nodes.items() if k != "l1_score" and isinstance(v, dict))


# --- behaviour-check evaluation -------------------------------------------


def _compute_behavior_per_round(
    rounds: list[RoundResult],
    audits: list[dict[str, Any] | None],
    context_object: list[str],
    l1_patience: int,
) -> tuple[list[list[CheckResult]], list[list[CheckResult]]]:
    """Per-round L1 + L2 behaviour-check results (same length as ``rounds``).
    L2 returns ``[]`` for rounds where L2 didn't fire — absent fire ≠ conformance failure."""
    l1_out: list[list[CheckResult]] = []
    l2_out: list[list[CheckResult]] = []
    prior_audits: list[dict[str, Any]] = []
    # Stall depth entering each round, reconstructed from the persisted ``improved``
    # flags (the round file doesn't carry the live l1_stall_count). Same recurrence as
    # ``EscalationFSM.observe_round``: reset to 0 on improvement, else +1. Read BEFORE
    # the update so each round's exploration_budget matches what its L1 generation saw.
    stall = 0
    for i, round_data in enumerate(rounds):
        round_num = round_data.round
        budget = exploration_budget(stall, l1_patience).value if round_num >= 1 else None
        audit = audits[i] if i < len(audits) else None
        if round_num >= 1:
            stall = 0 if round_data.improved else stall + 1
        if audit is None:
            l1_out.append([])
            l2_out.append([])
            continue
        opt_sp = round_data.opt_sp
        ctx = ValidatorContext(
            round_num=round_num,
            prior_rounds=list(prior_audits),
            opt_sp=opt_sp.model_dump() if opt_sp else {},
            context_object=context_object,
            exploration_budget=budget,
            peaked_axes=frozenset(round_data.axis_memory_peaked),
        )
        l1_out.append(run_all_checks(audit, ctx))
        l2_out.append(run_all_l2_checks(audit, ctx))
        prior_audits.append(audit)
    return l1_out, l2_out


# --- rendering helpers ----------------------------------------------------


def _halt_info(index: dict[str, Any], rounds: list[RoundResult]) -> dict[str, str] | None:
    """The cycle's terminal health story, or ``None`` when it ended cleanly.

    Reads the cycle ``stop_reason`` + the round ``health`` (both already on disk).
    An evidence-starvation abort — L2 reading the starved-node verdict and emitting
    ``terminate_proposal`` → ``StopReason.ABORT`` (``escalation_abort``) — is otherwise
    INVISIBLE in this surface: the conformance verdict reads "healthy" and ``l2_fires``
    reads 0 (a terminate produces no l2-sourced round). Surface it so a starvation halt
    can't be mistaken for a healthy short run.

    Gated on the cycle's TERMINAL state, not any critical round in history: a
    ``critical`` round that L2 then self-healed away (the cycle recovers and ends
    healthy) must NOT show a halt banner. So fire only when the cycle aborted
    (``escalation_abort``) OR the LAST graded round is itself ``critical`` (it halted
    there — backend-unreachable, origin-gate, or natural end on a critical round).

    Returns ``{tag, node, action, terminated}`` — ``tag`` the starvation/critical reason,
    ``node`` the dead node (``dominant_node``), ``action`` the operator-facing
    ``suggested_action``, ``terminated`` ``"yes"`` when L2 emitted ``terminate_proposal``
    (``stop_reason == escalation_abort``), else ``""``."""
    stop_reason = (index.get("stop_reason") or "").strip()
    terminated = "yes" if stop_reason == StopReason.ABORT else ""
    last_health: DegradationHealth | None = None
    last_critical: DegradationHealth | None = None
    for r in rounds:
        if r.health is not None:
            last_health = r.health  # ends as the last GRADED round (probes carry None)
            if r.health.grade == "critical":
                last_critical = r.health
    ended_critical = last_health is not None and last_health.grade == "critical"
    if not ended_critical and not terminated:
        return None
    # The terminate-triggering round is the last completed (critical) round; reuse it
    # to name the dead node (the ended-critical path uses the same round).
    if last_critical is not None:
        reasons = last_critical.reasons
        tag = (
            "evidence_starved"
            if "evidence_starved" in reasons
            else (reasons[0] if reasons else "critical")
        )
        return {
            "tag": tag,
            "node": last_critical.dominant_node or "",
            "action": (last_critical.suggested_action or "").strip(),
            "terminated": terminated,
        }
    return {"tag": "terminate_proposal", "node": "", "action": "", "terminated": terminated}


def _render_header(
    index: dict[str, Any], final: dict[str, Any], stats: L1Stats, halt: dict[str, str] | None
) -> list[str]:
    cycle_id = index.get("cycle_id") or "(unknown cycle)"
    mode = (final.get("mode") or "full").strip() or "full"
    parts: list[str] = [
        f"# Review — {cycle_id}",
        "",
        f"_mode: **{mode}** · round-1 conformance: **{stats.round_1_verdict}**_",
        "",
    ]
    if halt is not None:
        where = f" — node `{halt['node']}`" if halt["node"] else ""
        parts.append(f"> **HALTED — {halt['tag']}**{where}")
        if halt["action"]:
            parts.append(">")
            parts.append(f"> {halt['action']}")
        parts.append("")
    hashes = final.get("prompt_hashes") or {}
    if hashes:
        parts.append("**Prompt hashes**")
        parts.append("")
        for name in ("l1_generate", "l1_critique", "l2_context", "l3_plan"):
            short = (hashes.get(name) or "")[:8]
            if short:
                parts.append(f"- `{name}`: `{short}`")
        parts.append("")
    return parts


def _render_stats_block(
    stats: L1Stats,
    repairs_per_round: list[int],
    calls_per_round: list[int],
    halt: dict[str, str] | None,
) -> list[str]:
    def _rate(value: float | None, spec: str = ".2f") -> str:
        """An unmeasured rate renders as ``—``, never as a number the cycle never produced."""
        return "—" if value is None else format(value, spec)

    lines = [
        "## L1Stats",
        "",
        f"- **rounds_to_95**: {'—' if stats.rounds_to_95 is None else stats.rounds_to_95}",
        f"- yield_rate: {_rate(stats.yield_rate)}",
        f"- top_lift_mean: {_rate(stats.top_lift_mean, '+.4f')}",
        f"- behavior_pass_rate: {_rate(stats.behavior_pass_rate)}",
        f"- l2_behavior_pass_rate: {_rate(stats.l2_behavior_pass_rate)}",
        f"- stagnation_max: {stats.stagnation_max}",
        f"- l2_fires: {stats.l2_fires}",
    ]
    # A terminate is an L2 fire that produces no l2-sourced round, so `l2_fires`
    # alone reads 0 — name it explicitly so an L2 halt isn't invisible.
    if halt is not None and halt["terminated"]:
        node = f" ({halt['node']})" if halt["node"] else ""
        lines.append(f"- l2_terminated: {halt['tag']}{node}")
    repairs_total = sum(repairs_per_round)
    calls_total = sum(calls_per_round)
    if calls_total:
        rate_pct = 100.0 * repairs_total / calls_total
        lines.append(
            f"- schema_repair_retries: {repairs_total}/{calls_total} optimizer calls "
            f"({rate_pct:.0f}% paid a second round-trip)"
        )
    lines.append("")
    return lines


def _render_behavior_summary(
    behavior_per_round: list[list[CheckResult]],
) -> list[str]:
    if not behavior_per_round or not any(behavior_per_round):
        return []
    parts: list[str] = ["## Behaviour-check summary", ""]
    for check_id in CHECK_REGISTRY:
        fails = sum(
            1
            for round_res in behavior_per_round
            for c in round_res
            if c.check_id == check_id and not c.passed
        )
        runs = sum(
            1 for round_res in behavior_per_round for c in round_res if c.check_id == check_id
        )
        marker = "✗" if fails else "✓"
        parts.append(f"- {marker} `{check_id}` — {runs - fails}/{runs} rounds passed")
    parts.append("")
    return parts


def _render_round(
    round_data: RoundResult,
    audit: dict[str, Any] | None,
    checks: list[CheckResult],
    *,
    is_peek: bool,
    schema_repair_retries: int = 0,
) -> list[str]:
    opt_sp = round_data.opt_sp.model_dump() if round_data.opt_sp else {}
    lineage = opt_sp.get("lineage") or {}
    suffix = " (next-gen peek)" if is_peek else ""
    parts: list[str] = [
        f"### Round {round_data.round}{suffix}",
        "",
    ]
    if not is_peek:
        parts += [
            f"- accuracy: {round_data.accuracy:.1%}",
            f"- composite_fitness: `{round_data.composite_fitness:.4f}`",
            f"- improved: **{'yes' if round_data.improved else 'no'}**",
        ]
    if schema_repair_retries:
        parts.append(f"- schema_repair_retries: {schema_repair_retries}")
    parts += _render_l1_inputs(opt_sp, lineage)
    parts += _render_check_checklist(checks)
    parts += _render_variants_table(audit, round_data, scored=not is_peek)
    parts += _render_critique(round_data)
    return parts


def _render_l1_inputs(opt_sp: dict[str, Any], lineage: dict[str, Any]) -> list[str]:
    parts: list[str] = ["", "**L1 inputs**", ""]
    tc = (opt_sp.get("memory") or {}).get("task_context") or {}
    if isinstance(tc, dict) and tc:
        keys = ", ".join(sorted(k for k, v in tc.items() if v))
        parts.append(f"- task_context fields: {keys or '_(empty)_'}")
    else:
        parts.append("- task_context: _(empty)_")
    src = (lineage.get("source") or "").strip()
    if src:
        parts.append(f"- lineage source: `{src}`")
    changes = (lineage.get("changes_description") or "").strip()
    if changes:
        parts.append(f"- parent changes: {changes}")
    parts.append("")
    return parts


def _render_check_checklist(checks: list[CheckResult]) -> list[str]:
    if not checks:
        return ["**Behaviour checks:** _(no audit available)_", ""]
    parts: list[str] = ["**Behaviour checks**", ""]
    for c in checks:
        marker = "✓" if c.passed else "✗"
        parts.append(f"- {marker} `{c.check_id}` — {c.evidence}")
    parts.append("")
    return parts


def _render_variants_table(
    audit: dict[str, Any] | None,
    round_data: RoundResult,
    *,
    scored: bool,
) -> list[str]:
    """Per-variant row. The audit dict carries what L1 PROPOSED; ``round_data`` carries what
    each proposal MEASURED, joined on :func:`candidate_label` — the sole writer of both sides'
    key. Every score column used to print ``—`` while ``candidate_scores`` sat populated on
    the first parameter, so `review.md` recorded an unscored round for every round ever run.

    There is no Δ_parent column: the round file holds each candidate's MATCHED origin, which
    is the comparison the loop itself elects on, and no parent composite. A column with no
    source is what the em-dashes were.
    """
    variants = extract_l1_variants(audit)
    if not variants:
        return []
    parts: list[str] = ["**Variants**", ""]
    if scored:
        by_label = {c.label: c for c in round_data.candidate_scores}
        parts.append("| variant | composite_fitness | acc | Δ_origin | beat | evidence | changes |")
        parts.append("|---|---|---|---|---|---|---|")
        for i, v in enumerate(variants):
            changes = (v.get("changes_description") or "").replace("|", "\\|").strip()[:80]
            evidence = _fmt_evidence_cell(v.get("evidence_grounding"))
            label = candidate_label(round_data.round, i)
            parts.append(
                f"| `{label}` | {_score_cells(by_label.get(label))} | {evidence} | {changes} |"
            )
    else:
        parts.append("| cand_id | changes | derived_axes | evidence |")
        parts.append("|---|---|---|---|")
        for i, v in enumerate(variants):
            changes = (v.get("changes_description") or "").replace("|", "\\|").strip()[:80]
            axes = ", ".join(sorted((v.get("pipeline_params_override") or {}).keys()))
            evidence = _fmt_evidence_cell(v.get("evidence_grounding"))
            parts.append(f"| `C{i + 1}` | {changes} | {axes} | {evidence} |")
    parts.append("")
    return parts


def _score_cells(c: ScoredCandidate | None) -> str:
    """``composite_fitness | acc | Δ_origin | beat`` for one variant.

    ``—`` only where there genuinely is no number: a variant L1 proposed but the round never
    scored (parse-rejected, deduped), or a candidate with no MATCHED origin — eliminated
    before the coverage floor, where ``matched_origin_composite`` is deliberately ``None``
    rather than 0.0 (a 0.0 there reads as "beat the origin by its whole score").
    """
    if c is None:
        return "— | — | — | —"
    mo = c.matched_origin_composite
    if mo is None:
        return f"`{c.composite_fitness:.4f}` | {c.accuracy:.1%} | — | —"
    delta = c.composite_fitness - mo
    return f"`{c.composite_fitness:.4f}` | {c.accuracy:.1%} | {delta:+.4f} | {'✓' if delta > 0 else '·'}"


def _fmt_evidence_cell(raw: object) -> str:
    """Render evidence_grounding for the variants table — one cell, terse."""
    if not isinstance(raw, dict):
        return "—"
    field_name = str(raw.get("field") or "").strip()
    citation = str(raw.get("citation") or "").replace("|", "\\|").strip()
    if not field_name:
        return "—"
    if citation:
        return f"`{field_name}` — {citation[:60]}"
    return f"`{field_name}` _(no citation)_"


def _render_critique(round_data: RoundResult) -> list[str]:
    from promptpotter.domain.rendering import format_l1_critique_for_prompt

    critique = format_l1_critique_for_prompt(round_data.critique).strip()
    if not critique:
        return []
    quoted = critique.replace("\n", "\n> ")
    return ["**Critique**", "", f"> {quoted}", ""]


def _is_generation_only(round_data: RoundResult) -> bool:
    return round_data.status == "generation_only"


if TYPE_CHECKING:
    from promptpotter.application.bootstrap.session import Session
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.infrastructure.store.campaign_store.store import CampaignStore

__all__ = [
    "write_hard_samples_artifacts",
    "write_log_md",
    "write_review_md",
]


def _filter_artifact_to_live_candidates(
    artifact: dict[str, Any], live_cids: set[str]
) -> dict[str, Any]:
    """Restrict candidate_order + cells to ``live_cids`` (Y-axis hygiene).
    Rasch fit stays joint (archive observations still contribute to δ_s); only the displayed axis is filtered."""
    filtered_order = [cid for cid in artifact["candidate_order"] if cid in live_cids]
    filtered_cells = [c for c in artifact["cells"] if c["c"] in live_cids]
    rasch = dict(artifact["rasch"])
    rasch["theta"] = {cid: rasch["theta"][cid] for cid in filtered_order if cid in rasch["theta"]}
    rasch["theta_se"] = {
        cid: rasch["theta_se"][cid] for cid in filtered_order if cid in rasch["theta_se"]
    }
    rasch["n_obs_per_candidate"] = {
        cid: rasch["n_obs_per_candidate"][cid]
        for cid in filtered_order
        if cid in rasch["n_obs_per_candidate"]
    }
    out = dict(artifact)
    out["candidate_order"] = filtered_order
    out["cells"] = filtered_cells
    out["rasch"] = rasch
    out["n_candidates"] = len(filtered_order)
    out["n_observations"] = len(filtered_cells)
    return out


def write_hard_samples_artifacts(session: Session, cycle: Cycle) -> dict[str, Any] | None:
    """Build + persist the hard-sample heatmap artifacts at each data scope.

    Three files, named by data scope:

    - ``cycles/{cycle_id}/hard_samples.json`` — **cycle** scope: fit over
      ``cycle.rounds`` only.
    - ``campaigns/{campaign_id}/hard_samples.json`` — **campaign** scope:
      the cycle's rounds folded with the campaign's archive observations.
    - ``measurements/hard_samples_{backend}_{dataset}.json`` —
      **dataset** scope: the archive snapshot for this backend + dataset.

    Returns the artifact selected for inline log.md rendering: the
    campaign-scope one when ``optimization.seed_heatmap_from_archive`` is on,
    otherwise the cycle-scope one. ``None`` when no observations exist yet.
    """
    if not session.state.cycle_id or session.store is None:
        return None

    store = session.store.campaigns
    campaign_id = session.campaign_id
    cycle_id = session.state.cycle_id
    cycle_dir = store.cycle_dir(campaign_id, cycle_id)
    campaign_dir = store.campaign_root_dir(campaign_id)

    cycle_artifact = build_hard_samples_artifact(
        cycle.rounds,
        cycle_id=cycle_id,
        top_k_candidates=None,
        top_k_samples=None,
    )

    live_obs = build_observations(cycle.rounds)
    campaign_obs = list(cycle.archive_observations) + live_obs
    campaign_artifact = build_hard_samples_artifact_from_observations(
        campaign_obs,
        cycle_id=cycle_id,
        top_k_candidates=None,
        top_k_samples=None,
    )

    opt_cfg = cycle.config.optimization
    # Archive candidates contribute to the joint Rasch fit but stay off the
    # heatmap Y-axis — it's filtered to this cycle's own cand_NNN.
    live_cids = {cid for rr in cycle.rounds for cid in rr.all_candidate_results}
    if live_cids:
        campaign_artifact = _filter_artifact_to_live_candidates(campaign_artifact, live_cids)

    with graceful("cycle hard_samples.json write failed"):
        write_json(CycleLayout(cycle_dir).hard_samples, cycle_artifact)
    with graceful("campaign hard_samples.json write failed"):
        write_json(campaign_dir / "hard_samples.json", campaign_artifact)

    # Dataset-scope snapshot is per-(backend, dataset) — cross-dataset
    # pooling would corrupt Rasch + PoBB queue mechanism (sample_id collides).
    dataset_tag = session.dataset_name or "unknown"
    tenant_path = session.store.archive.dataset_snapshot_path(session.backend_id, dataset_tag)
    with graceful("dataset hard_samples snapshot write failed"):
        archive_artifact = build_archive_hard_samples_artifact(
            session.store,
            dataset_name=session.dataset_name,
            top_k_candidates=None,
            top_k_samples=None,
        )
        write_json(tenant_path, archive_artifact)

    return campaign_artifact if opt_cfg.seed_heatmap_from_archive else cycle_artifact


def _load_p_best_trajectory(
    streams_dir: Path | None, round_num: int
) -> tuple[dict[str, list[float]], dict[str, int]]:
    if streams_dir is None:
        return {}, {}
    trajectory: dict[str, list[float]] = {}
    last_seen: dict[str, int] = {}
    for rec in iter_jsonl(streams_dir / f"round_{round_num:04d}_p_best.jsonl"):
        qi = int(rec.get("sample_idx", -1))
        for cid, prob in (rec.get("p_best") or {}).items():
            trajectory.setdefault(str(cid), []).append(float(prob))
            last_seen[str(cid)] = qi
    return trajectory, last_seen


def _sample_queries(rounds: list[RoundResult]) -> dict[int, str]:
    """``{sample_id: query}`` harvested from the rounds already in hand — the rounds carry
    every sample's ``query``, so derive it rather than asking the caller."""
    out: dict[int, str] = {}
    for rr in rounds:
        for row in rr.results:
            sid, query = row.get("sample_id"), row.get("query")
            if isinstance(sid, int) and isinstance(query, str) and sid not in out:
                out[sid] = query
    return out


def from_disk_log(
    index: dict[str, Any],
    rounds: list[RoundResult],
    *,
    hard_samples_artifact: dict[str, Any] | None = None,
    streams_dir: Path | None = None,
    fork_indices: list[dict[str, Any]] | None = None,
) -> LogMdView:
    """``fork_indices`` is the list of sibling-cycle ``index.json`` blobs;
    rendered as the ``## Cycles`` section on the campaign digest. The
    per-cycle log.md passes ``None``.
    """
    final = index.get("final") or {}
    status = DigestStatusView(
        campaign_id=str(index.get("cycle_id") or ""),
        parent_session_id=index.get("parent_session_id"),
        status=str(index.get("status", "active")),
        stop_reason=str(final.get("stop_reason") or index.get("stop_reason") or "(running)"),
        origin_accuracy=origin_accuracy_of(index) or 0.0,
        best_accuracy=float(index.get("best_accuracy", 0.0)),
        best_round=index.get("best_round"),
        rounds_completed=int(index.get("n_rounds", 0)),
        started_at=final.get("started_at"),
        finished_at=final.get("finished_at"),
        gen_only_rounds=sum(1 for t in rounds if t.status == "generation_only"),
    )

    round_views: list[RoundDigestView] = []
    for t in rounds:
        traj, _ = _load_p_best_trajectory(streams_dir, t.round)
        lineage = t.opt_sp.lineage if t.opt_sp else None
        round_views.append(
            RoundDigestView(
                round=t.round,
                label=t.label.strip() or t.round_id,
                accuracy=t.accuracy,
                improved=t.improved,
                total=t.total,
                composite_fitness=t.composite_fitness,
                changes_description=(lineage.changes_description if lineage else "").strip(),
                l1_critique_text=format_l1_critique_for_prompt(t.critique),
                l1_yield=t.l1_yield,
                l1_n_no_op=t.l1_n_no_op,
                l1_n_duplicate=t.l1_n_duplicate,
                l1_n_repeat=t.l1_n_repeat,
                candidates_scored=t.candidates_scored,
                evaluators=dict(t.evaluators),
                p_best_trajectory=traj,
            )
        )

    final_view = (
        FinalWinnerView(
            winner_prompt_fields=dict(final.get("winner_prompt_fields") or {}),
            winner_pipeline_params=dict(final.get("winner_pipeline_params") or {}),
        )
        if final
        else None
    )
    hard = (
        HardSamplesView(
            artifact=dict(hard_samples_artifact),
            sample_query_lookup=_sample_queries(rounds),
        )
        if hard_samples_artifact
        else None
    )
    fork_views: tuple[ForkSummaryView, ...] = ()
    family_best: tuple[float, str] | None = None
    if fork_indices:
        live = [fi for fi in fork_indices if int(fi.get("n_rounds") or 0) > 0]
        fork_views = tuple(
            sorted(
                (_fork_summary_from_index(fi) for fi in live),
                key=lambda v: v.best_accuracy,
                reverse=True,
            )
        )
        candidates: list[tuple[float, str]] = [
            (float(index.get("best_accuracy", 0.0)), str(index.get("cycle_id") or ""))
        ]
        for fv in fork_views:
            candidates.append((fv.best_accuracy, fv.cycle_id))
        family_best = max(candidates, key=lambda c: c[0])

    return LogMdView(
        status=status,
        rounds=tuple(round_views),
        formula=final.get("scorer_round_formula"),
        origin_composite_fitness=final.get("origin_composite_fitness"),
        hard_samples=hard,
        final=final_view,
        forks=fork_views,
        family_best=family_best,
    )


def _fork_summary_from_index(fork_index: dict[str, Any]) -> ForkSummaryView:
    final = fork_index.get("final") or {}
    return ForkSummaryView(
        cycle_id=str(fork_index.get("cycle_id") or ""),
        mode=str(final.get("mode") or fork_index.get("sibling_kind") or ""),
        status=str(fork_index.get("status", "active")),
        best_accuracy=float(fork_index.get("best_accuracy", 0.0)),
        origin_accuracy=origin_accuracy_of(fork_index) or 0.0,
        n_rounds=int(fork_index.get("n_rounds", 0) or 0),
        stop_reason=str(final.get("stop_reason") or fork_index.get("stop_reason") or ""),
        finished_at=final.get("finished_at") or fork_index.get("finished_at"),
    )


def write_log_md(session: Session, *, hard_samples_artifact: dict[str, Any] | None = None) -> None:
    """Render the per-cycle log.md and refresh the campaign digest."""
    if not session.state.cycle_id or session.store is None:
        return
    with graceful("log.md render failed"):
        store = session.store.campaigns
        campaign_id = session.campaign_id
        cycle_id = session.state.cycle_id
        _render_cycle_log_md(store, campaign_id, cycle_id, hard_samples_artifact)
        _render_campaign_log_md(store, campaign_id)


def _render_cycle_log_md(
    store: CampaignStore,
    campaign_id: str,
    cycle_id: str,
    hard_samples_artifact: dict[str, Any] | None,
) -> None:
    """Per-cycle ``cycles/{cycle_id}/log.md`` — this cycle's rounds only."""
    index = store.load(campaign_id, cycle_id)
    if not index:
        return
    n_rounds = int(index.get("n_rounds", 0) or 0)
    rounds = store.load_rounds_range(campaign_id, cycle_id, 0, n_rounds - 1) if n_rounds else []
    layout = CycleLayout(store.cycle_dir(campaign_id, cycle_id))
    content = to_markdown(
        from_disk_log(
            index,
            rounds,
            hard_samples_artifact=hard_samples_artifact,
            streams_dir=layout.streams,
            fork_indices=None,
        )
    )
    write_text(layout.log_md, content)


def _render_campaign_log_md(store: CampaignStore, campaign_id: str) -> None:
    """Campaign digest ``campaigns/{campaign_id}/log.md`` — the folder-UI headline.

    Anchored on the root cycle, with every other cycle of the lineage
    folded into the ``## Cycles`` section.
    """
    campaign = store.load_campaign(campaign_id)
    if campaign is None:
        return
    root_id = campaign.root_cycle_id
    index = store.load(campaign_id, root_id)
    if not index:
        return
    n_rounds = int(index.get("n_rounds", 0) or 0)
    rounds = store.load_rounds_range(campaign_id, root_id, 0, n_rounds - 1) if n_rounds else []
    streams_dir = CycleLayout(store.cycle_dir(campaign_id, root_id)).streams
    fork_indices = _load_sibling_indices(store, campaign_id, exclude=root_id)
    content = to_markdown(
        from_disk_log(
            index,
            rounds,
            streams_dir=streams_dir,
            fork_indices=fork_indices,
        )
    )
    write_text(store.campaign_root_dir(campaign_id) / "log.md", content)


def _load_sibling_indices(
    store: CampaignStore, campaign_id: str, *, exclude: str
) -> list[dict[str, Any]] | None:
    """Every cycle ``index.json`` in the campaign except ``exclude``. ``None`` when empty."""
    cycles_dir = campaign_cycles_dir(store.campaign_root_dir(campaign_id))
    if not cycles_dir.is_dir():
        return None
    out: list[dict[str, Any]] = []
    for cycle_dir in sorted(cycles_dir.iterdir()):
        if not cycle_dir.is_dir() or cycle_dir.name == exclude:
            continue
        blob = read_json_tolerant(CycleLayout(cycle_dir).manifest)
        if not isinstance(blob, dict):
            continue
        blob["cycle_id"] = cycle_dir.name
        out.append(blob)
    return out or None


def write_review_md(session: Session, cycle: Cycle) -> None:
    if not session.state.cycle_id or session.store is None:
        return
    with graceful("review.md render failed"):
        store = session.store.campaigns
        campaign_id = session.campaign_id
        cycle_id = session.state.cycle_id
        index = store.load(campaign_id, cycle_id)
        if not index:
            return
        n_rounds = int(index.get("n_rounds", 0) or 0)
        rounds = store.load_rounds_range(campaign_id, cycle_id, 0, n_rounds - 1) if n_rounds else []
        cycle_dir = store.cycle_dir(campaign_id, cycle_id)
        round_audits = load_round_audits(cycle_dir, [r.round for r in rounds])
        td = cycle.opt_sp.memory.task_context
        context_object = [
            td.pipeline_purpose,
            td.optimization_goals,
            td.key_challenges,
        ]
        content = render_review_md(
            index,
            rounds,
            round_audits=round_audits,
            context_object=context_object,
            l1_patience=cycle.config.optimization.l1_patience,
        )
        write_text(CycleLayout(cycle_dir).review_md, content)
