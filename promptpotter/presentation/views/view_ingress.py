"""Live PhaseEvent ingress — ``PhaseEvent + ctx → typed View → wire dict``.

Each per-phase builder constructs a typed view directly and applies its
``ctx`` side effects (round-num tracking, origin rolling, p-value,
prompt-flat memo) in one pass.

Two entry points:

- ``from_phase_event(event, ctx)`` — used by ``RunCallbacks``; the typed
  view is the single source of truth, and the runner serialises it via
  ``view_to_wire_dict`` before placing it on the ledger payload.
- ``view_from_record(record_dict)`` — used by ledger subscribers reading
  the wire-format dict back. Identity-style projection that mirrors the
  builder shapes.

The shared score-entry helpers (``pick_round_winner``,
``score_entry_from_dict``) are also consumed by ``presentation/writers.py``
when reconstructing typed views from disk for ``log.md`` rendering.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from promptpotter.application.optimization.dispatch_hub import (
    format_l1_critique_for_prompt,
)
from promptpotter.domain.opt_search_point import (
    build_candidate_flat,
    flatten_sp_summary,
)
from promptpotter.domain.phases import PhaseEvent
from promptpotter.presentation.views.view_models import (
    AnyView,
    CandidatesGeneratedView,
    EscalationEnterView,
    EscalationExitView,
    InitEnterView,
    InitExitView,
    L2RefineEnterView,
    L2RefineExitView,
    PlanEnterView,
    PlanExitView,
    ProbeEnterView,
    ProbeExitView,
    RoundCompleteView,
    RoundStartView,
    ScoreEntry,
    SpDiffView,
    WarningEntry,
)
from promptpotter.shared.statistics import min_detectable_effect, proportion_test, wilson_ci

__all__ = [
    "from_phase_event",
    "pick_round_winner",
    "score_entry_from_dict",
    "view_from_record",
    "view_to_wire_dict",
]


def _truncate(s: str, max_len: int) -> str:
    return s if len(s) <= max_len else s[: max_len - 3] + "..."


# --- per-phase typed builders (live; with ctx side effects) ---------------


def _init_enter(d: dict, ctx: dict) -> InitEnterView:
    config = d["config"]
    dataset = d["dataset"]
    session = d.get("env")
    schema = session.pipeline_schema if session is not None else None
    opt = config.optimization
    sample = config.sp_budget_ttest

    ctx["max_rounds"] = opt.max_rounds or 0
    ctx["patience"] = opt.l1_patience
    ctx["original_sp_flat"] = flatten_sp_summary(
        schema.to_pipeline_params() if schema else None,
    )
    ctx["node_param_keys"] = (
        {s: sorted(k) for s, k in schema.node_param_keys().items()} if schema else None
    )
    ctx["round_num"] = 0
    ctx["l1_stall_count"] = 0
    ctx["origin_accuracy"] = 0.0

    # Resolve the per-round composite formula at INIT.enter so the live
    # dashboard can stamp it before origin scoring fires (matches _init_exit
    # priority: explicit campaign override > schema default > None).
    explicit = (
        session.scoring.scorer_round_formula
        if session is not None and getattr(session, "scoring", None) is not None
        else None
    )
    if explicit:
        full, short = explicit, None
    elif schema is None:
        full, short = None, None
    else:
        from promptpotter.application.scoring.evaluators import (
            default_per_round_formula,
            default_per_round_formula_short,
        )

        full = default_per_round_formula(schema)
        short = default_per_round_formula_short(schema)
    ctx["composite_fitness_formula"] = full
    ctx["composite_fitness_formula_short"] = short

    return InitEnterView(
        warnings=tuple(
            WarningEntry(title=w.title, detail=w.detail) for w in (d.get("warnings") or [])
        ),
        max_rounds=ctx["max_rounds"],
        patience=ctx["patience"],
        n_variants=opt.n_variants,
        sp_budget_ttest=sample,
        dataset_size=len(dataset),
        mde=min_detectable_effect(sample),
        model=config.optimizer_llm.model or "(default)",
        l2_enabled=opt.enable_l2,
        l3_enabled=opt.enable_l3,
        composite_fitness_formula=full,
        composite_fitness_formula_short=short,
    )


def _init_exit(d: dict, ctx: dict) -> InitExitView:
    cycle = d["state"]
    session = d["env"]
    ctx["origin_accuracy"] = cycle.tracking.current_accuracy
    ctx["origin_composite_fitness"] = cycle.tracking.current_composite_fitness
    schema = session.pipeline_schema
    explicit = session.scoring.scorer_round_formula
    if explicit:
        full, short = explicit, None
    elif schema is None:
        full, short = None, None
    else:
        from promptpotter.application.scoring.evaluators import (
            default_per_round_formula,
            default_per_round_formula_short,
        )

        full = default_per_round_formula(schema)
        short = default_per_round_formula_short(schema)
    ctx["composite_fitness_formula"] = full
    ctx["composite_fitness_formula_short"] = short

    overlays: dict[str, str] = {}
    for field_name, value in cycle.opt_sp.prompt_field_dict().items():
        if value:
            overlays[field_name] = str(value)
            ctx["original_sp_flat"][field_name] = str(value)

    return InitExitView(
        origin_acc=cycle.tracking.current_accuracy,
        cycle_id_short=(session.state.cycle_id or "?")[:12],
        samples=len(session.scoring.scoring_set),
        obs_on=session.state.obs is not None,
        resumed_from_round=session.state.resumed_from_round,
        task_context_keys=len(cycle.opt_sp.task_context),
        l2_round=cycle.escalation.l2_round,
        prompt_field_overlays=overlays,
        composite_fitness_formula=full,
        composite_fitness_formula_short=short,
        origin_composite_fitness=ctx["origin_composite_fitness"],
    )


def _l1_generate_enter(d: dict, ctx: dict) -> RoundStartView:
    preview = (d.get("prompt_preview") or "").replace("\n", " ").strip()
    preview = (
        "(empty starting prompt - param-only optimization)"
        if not preview
        else _truncate(preview, 50)
    )

    new_flat = flatten_sp_summary(d.get("pipeline_params"))
    for field_name, value in (d.get("parent_prompt_fields") or {}).items():
        if value:
            new_flat[field_name] = str(value)
    ctx["previous_sp_flat"] = dict(
        (
            ctx.get("original_sp_flat")
            if ctx.get("round_num", 0) == 0
            else ctx.get("current_sp_flat")
        )
        or {}
    )
    ctx["current_sp_flat"] = new_flat

    return RoundStartView(
        round=ctx.get("round_num", 0) + 1,
        max_rounds=ctx.get("max_rounds", 0),
        l1_stall_count=ctx.get("l1_stall_count", 0),
        patience=ctx.get("patience", 0),
        current_acc=d.get("current_accuracy", 0.0),
        prompt_preview=preview,
        n_variants=d.get("n_variants", 0),
        model=d.get("model") or "(default)",
        creativity=d.get("creativity", 0.7),
        has_l1_critique=bool(d.get("has_l1_critique")),
    )


def _l1_generate_exit(d: dict, ctx: dict) -> CandidatesGeneratedView:
    candidates_meta = d.get("candidates", [])
    parent = ctx.get("current_sp_flat") or {}
    columns: list[tuple[str, dict[str, str]]] = [
        ("Start", dict(ctx.get("original_sp_flat") or {})),
        ("Parent", dict(parent)),
    ]
    clone_labels: list[str] = []
    for c in candidates_meta:
        label = f"C{c['idx'] + 1}"
        flat = build_candidate_flat(parent, c)
        if flat == parent:
            clone_labels.append(label)
        columns.append((label, flat))

    l1_yield = float(d.get("l1_yield", 1.0))
    n_no_op = int(d.get("l1_n_no_op", 0))
    n_dup = int(d.get("l1_n_duplicate", 0))
    sp_diff = SpDiffView(
        columns=tuple(columns),
        node_param_keys=ctx.get("node_param_keys"),
        round_num=ctx.get("round_num", 0) + 1,
        clone_labels=tuple(clone_labels),
        l1_yield=l1_yield,
        l1_n_no_op=n_no_op,
        l1_n_duplicate=n_dup,
    )
    return CandidatesGeneratedView(
        n_candidates=d.get("n_candidates", 0),
        source="disk" if d.get("loaded_from_disk") else "llm",
        n_scoring_samples=d.get("n_scoring_samples", 0),
        l1_yield=l1_yield,
        l1_n_no_op=n_no_op,
        l1_n_duplicate=n_dup,
        clone_labels=tuple(clone_labels),
        sp_diff=sp_diff,
    )


def _l1_score_exit(d: dict, ctx: dict) -> RoundCompleteView:
    score_entries = [
        score_entry_from_dict(s, fallback_label=f"C{i + 1}")
        for i, s in enumerate(d.get("candidate_scores") or [])
    ]

    winner = pick_round_winner(score_entries)
    if winner is not None:
        winner_label, winner_hits, winner_total = winner.label, winner.hits, winner.total
    else:
        winner_label, winner_hits, winner_total = "?", 0, 0

    w_acc = float(d.get("winner_accuracy", 0.0))
    improved = bool(d.get("improved", False))
    origin_acc = ctx.get("origin_accuracy", 0.0)
    delta = w_acc - origin_acc if improved else 0.0
    p_value: float | None = None
    if improved and winner_total > 0:
        p_value = proportion_test(
            winner_hits, winner_total, round(origin_acc * winner_total), winner_total
        )
        ctx["origin_accuracy"] = w_acc

    return RoundCompleteView(
        round=int(ctx.get("round_num", 0)),
        origin_acc=origin_acc,
        scores=tuple(score_entries),
        winner_label=winner_label,
        winner_accuracy=w_acc,
        winner_composite_fitness=d.get("winner_composite_fitness"),
        winner_evaluators=dict(d.get("winner_evaluators") or {}),
        winner_hits=winner_hits,
        winner_total=winner_total,
        improved=improved,
        delta=delta,
        p_value=p_value,
        next_action=str(d.get("next_action", "?") or "?"),
        l1_critique_text=format_l1_critique_for_prompt(d.get("critique") or {}),
        composite_fitness_formula=ctx.get("composite_fitness_formula"),
        composite_fitness_formula_short=ctx.get("composite_fitness_formula_short"),
        origin_composite_fitness=ctx.get("origin_composite_fitness"),
    )


def _escalation_enter(d: dict, ctx: dict) -> EscalationEnterView:
    return EscalationEnterView(
        check_name=d.get("check_name", "?"),
        target=d.get("target", "?"),
        degraded_rate=d.get("degraded_rate", 0.0),
        warning_types=dict(d.get("warning_types") or {}),
    )


def _escalation_exit(d: dict, ctx: dict) -> EscalationExitView:
    return EscalationExitView(
        classifications=tuple(
            (c.get("warning_type", ""), c.get("status", ""))
            for c in (d.get("classifications") or [])
        )
    )


def _refine_enter(d: dict, ctx: dict) -> L2RefineEnterView:
    params = d.get("l1_overrides") or {}
    return L2RefineEnterView(
        l2_round=d.get("l2_round", "?"),
        l1_stall_count=d.get("l1_stall_count", "?"),
        current_acc=d.get("current_accuracy", 0.0),
        best_acc=d.get("best_accuracy", 0.0),
        l1_overrides={k: str(v) for k, v in params.items()},
        n_params=len(params),
    )


def _refine_exit(d: dict, ctx: dict) -> L2RefineExitView:
    return L2RefineExitView(
        param_changes_count=d.get("param_changes_count", 0),
        task_context_changed=bool(d.get("task_context_changed", False)),
        action=d.get("action", "continue"),
        changes_description=d.get("changes_description", ""),
        warned_samples=d.get("warned_samples", 0),
        top_warning=d.get("top_warning", ""),
        l2_prompt=d.get("l2_prompt", "") or "",
        l2_response_json=d.get("l2_response"),
    )


def _probe_enter(d: dict, ctx: dict) -> ProbeEnterView:
    queries = list(d.get("probe_queries") or [])
    return ProbeEnterView(
        n_probe_samples=d.get("n_probe_samples", len(queries)),
        probe_queries=tuple(queries),
    )


def _probe_exit(d: dict, ctx: dict) -> ProbeExitView:
    return ProbeExitView(
        n_probed=d.get("n_probed", 0),
        probe_hits=d.get("probe_hits", 0),
    )


def _plan_enter(d: dict, ctx: dict) -> PlanEnterView:
    return PlanEnterView(
        l3_round=d.get("l3_round", "?"),
        l2_stall_count=d.get("l2_stall_count", "?"),
        current_plan_preview=_truncate(d.get("current_plan_preview", "") or "", 55),
    )


def _plan_exit(d: dict, ctx: dict) -> PlanExitView:
    return PlanExitView(
        new_plan_preview=_truncate(d.get("new_plan_preview", "") or "", 55),
        changes_description=d.get("changes_description", ""),
    )


_BUILDERS: dict[str, Any] = {
    "init:enter": _init_enter,
    "init:exit": _init_exit,
    "l1_generate:enter": _l1_generate_enter,
    "l1_generate:exit": _l1_generate_exit,
    "l1_score:exit": _l1_score_exit,
    "refine_strategy:enter": _refine_enter,
    "refine_strategy:exit": _refine_exit,
    "modify_plan:enter": _plan_enter,
    "modify_plan:exit": _plan_exit,
    "escalation:enter": _escalation_enter,
    "escalation:exit": _escalation_exit,
    "probe_round:enter": _probe_enter,
    "probe_round:exit": _probe_exit,
}


# --- live entry points ----------------------------------------------------


def from_phase_event(event: PhaseEvent, ctx: dict) -> AnyView | None:
    """Build a typed view from a ``PhaseEvent``; ``None`` for unregistered phases."""
    if event.round is not None:
        ctx["round_num"] = event.round
    builder = _BUILDERS.get(f"{event.phase}:{event.event}")
    return builder(event.data, ctx) if builder is not None else None


def view_to_wire_dict(view: AnyView | None) -> dict[str, Any] | None:
    """Convert a typed view to its JSON-shaped wire dict (for the ledger)."""
    return asdict(view) if view is not None else None


# --- wire-dict → typed reconstruction (for ledger subscribers) ------------


def _sp_diff_from_dict(d: dict | None) -> SpDiffView:
    if not d:
        return SpDiffView((), None, None, (), 1.0, 0, 0)
    return SpDiffView(
        columns=tuple((c[0], dict(c[1])) for c in d.get("columns") or []),
        node_param_keys=d.get("node_param_keys"),
        round_num=d.get("round_num"),
        clone_labels=tuple(d.get("clone_labels") or []),
        l1_yield=float(d.get("l1_yield", 1.0)),
        l1_n_no_op=int(d.get("l1_n_no_op", 0)),
        l1_n_duplicate=int(d.get("l1_n_duplicate", 0)),
    )


def pick_round_winner(score_entries: list[ScoreEntry]) -> ScoreEntry | None:
    """Round winner = max non-aborted ``ScoreEntry`` ranked by
    ``(composite_fitness or accuracy, accuracy)``. Single source of truth for
    the tiebreak rule shared between the live event ingress (``_l1_score_exit``)
    and the disk-replay ingress (``from_disk_round``).
    """
    non_aborted = [s for s in score_entries if not s.escalation_aborted]
    if not non_aborted:
        return None
    return max(
        non_aborted,
        key=lambda s: (
            s.composite_fitness if s.composite_fitness is not None else s.accuracy,
            s.accuracy,
        ),
    )


def score_entry_from_dict(s: dict, *, fallback_label: str = "") -> ScoreEntry:
    """Project a candidate-score wire dict (``CandidateScore.to_dict()`` shape)
    into a ``ScoreEntry``. ``fallback_label`` covers in-memory call sites where
    the dict carries no ``"label"`` and the index-tag (e.g. ``C3``) is supplied
    by the caller; ``ci_lo``/``ci_hi`` are recomputed from hits/total when the
    dict pre-dates Wilson-CI persistence.
    """
    hits = int(s.get("hits", 0))
    total = int(s.get("total", 0))
    if "ci_lo" in s and "ci_hi" in s:
        ci_lo, ci_hi = float(s["ci_lo"]), float(s["ci_hi"])
    else:
        ci_lo, ci_hi = wilson_ci(hits, total)
    return ScoreEntry(
        label=s.get("label") or fallback_label,
        accuracy=float(s.get("accuracy", 0.0)),
        composite_fitness=s.get("composite_fitness"),
        hits=hits,
        total=total,
        ci_lo=ci_lo,
        ci_hi=ci_hi,
        escalation_aborted=bool(s.get("escalation_aborted", False)),
    )


def _init_enter_from_dict(v: dict) -> InitEnterView:
    return InitEnterView(
        warnings=tuple(
            WarningEntry(title=w.get("title", ""), detail=w.get("detail", ""))
            for w in v.get("warnings") or []
        ),
        max_rounds=v.get("max_rounds", 0),
        patience=v.get("patience", 0),
        n_variants=v.get("n_variants", 0),
        sp_budget_ttest=v.get("sp_budget_ttest", 0),
        dataset_size=v.get("dataset_size", 0),
        mde=v.get("mde", 0.0),
        model=v.get("model", ""),
        l2_enabled=v.get("l2_enabled", False),
        l3_enabled=v.get("l3_enabled", False),
        composite_fitness_formula=v.get("composite_fitness_formula"),
        composite_fitness_formula_short=v.get("composite_fitness_formula_short"),
    )


def _l1_generate_exit_from_dict(v: dict) -> CandidatesGeneratedView:
    return CandidatesGeneratedView(
        **{k: v[k] for k in v if k != "sp_diff" and k != "clone_labels"},
        clone_labels=tuple(v.get("clone_labels") or []),
        sp_diff=_sp_diff_from_dict(v.get("sp_diff")),
    )


def _l1_score_exit_from_dict(v: dict) -> RoundCompleteView:
    return RoundCompleteView(
        **{k: v[k] for k in v if k != "scores"},
        scores=tuple(score_entry_from_dict(s) for s in v.get("scores") or []),
    )


# Pure ``XxxView(**v)`` cases — registry of phase:event → dataclass.
_PURE_RECONSTRUCT: dict[str, type] = {
    "init:exit": InitExitView,
    "l1_generate:enter": RoundStartView,
    "escalation:enter": EscalationEnterView,
    "refine_strategy:enter": L2RefineEnterView,
    "refine_strategy:exit": L2RefineExitView,
    "probe_round:exit": ProbeExitView,
    "modify_plan:enter": PlanEnterView,
    "modify_plan:exit": PlanExitView,
}

# Cases that need wire-dict adaptation (tuples, nested views, etc.).
_CUSTOM_RECONSTRUCT: dict[str, Callable[[dict], AnyView]] = {
    "init:enter": _init_enter_from_dict,
    "l1_generate:exit": _l1_generate_exit_from_dict,
    "l1_score:exit": _l1_score_exit_from_dict,
    "escalation:exit": lambda v: EscalationExitView(
        classifications=tuple((c[0], c[1]) for c in v.get("classifications") or []),
    ),
    "probe_round:enter": lambda v: ProbeEnterView(
        n_probe_samples=v.get("n_probe_samples", 0),
        probe_queries=tuple(v.get("probe_queries") or []),
    ),
}


def view_from_record(record: dict[str, Any]) -> AnyView | None:
    """Reconstruct a typed view from a wire-format ledger payload.

    The wire dict is ``view_to_wire_dict(typed_view)``; this is its inverse.
    """
    v = record.get("view")
    if v is None:
        return None
    key = f"{record.get('phase', '')}:{record.get('event', '')}"
    if cls := _PURE_RECONSTRUCT.get(key):
        return cls(**v)
    if fn := _CUSTOM_RECONSTRUCT.get(key):
        return fn(v)
    return None
