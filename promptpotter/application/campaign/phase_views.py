"""Phase-event view builders — convert PhaseEvent payloads to serializable dicts."""

from __future__ import annotations

from typing import Any

from promptpotter.application.optimization.sp_diff_view import (
    build_candidate_flat,
    flatten_sp_summary,
)
from promptpotter.domain.phases import PhaseEvent
from promptpotter.shared.statistics import min_detectable_effect, proportion_test

__all__ = ["build_phase_view"]


def _truncate(s: str, max_len: int) -> str:
    if len(s) <= max_len:
        return s
    return s[: max_len - 3] + "..."


def _init_enter(d: dict, ctx: dict) -> dict:
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
    ctx["baseline_total"] = sample
    ctx["round_num"] = 0
    ctx["l1_stall_count"] = 0
    ctx["baseline_accuracy"] = 0.0

    warnings = [
        {"title": getattr(w, "title", ""), "detail": getattr(w, "detail", "")}
        for w in (d.get("warnings") or [])
    ]

    return {
        "warnings": warnings,
        "max_rounds": ctx["max_rounds"],
        "patience": ctx["patience"],
        "n_variants": opt.n_variants,
        "sp_budget_ttest": sample,
        "dataset_size": len(dataset),
        "mde": min_detectable_effect(sample),
        "model": config.optimizer_llm.model or "(default)",
        "l2_enabled": opt.enable_l2,
        "l3_enabled": opt.enable_l3,
        "original_sp_flat": dict(ctx["original_sp_flat"]),
        "node_param_keys": ctx["node_param_keys"],
    }


def _init_exit(d: dict, ctx: dict) -> dict:
    cycle = d["state"]
    session = d["env"]
    ctx["baseline_accuracy"] = cycle.current_accuracy
    ctx["baseline_composite"] = getattr(cycle, "current_composite", cycle.current_accuracy)
    schema = getattr(session, "pipeline_schema", None)
    explicit = getattr(session, "scorer_round_formula", None)
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
    ctx["composite_formula"] = full
    ctx["composite_formula_short"] = short

    prompt_fields = cycle.opt_sp.prompt_field_dict()
    overlays: dict[str, str] = {}
    for field_name, value in prompt_fields.items():
        if value:
            overlays[field_name] = str(value)
            ctx["original_sp_flat"][field_name] = str(value)

    cycle_id_short = (session.state.cycle_id or "?")[:12]
    samples = len(session.scoring.scoring_dataset)
    obs_on = session.state.obs is not None
    crit = cycle.opt_sp.l1_critique_text or ""
    bootstrap_critique = _truncate(crit.replace("\n", " ").strip(), 80)

    resumed = session.state.resumed_from_round
    return {
        "baseline_acc": cycle.current_accuracy,
        "cycle_id_short": cycle_id_short,
        "samples": samples,
        "obs_on": obs_on,
        "bootstrap_critique": bootstrap_critique,
        "resumed_from_round": resumed,
        "l1_critique_chars": len(cycle.opt_sp.l1_critique_text or ""),
        "task_context_keys": len(cycle.opt_sp.task_context),
        "l2_round": cycle.escalation.l2.round,
        "prompt_field_overlays": overlays,
        "composite_formula": ctx["composite_formula"],
        "composite_formula_short": ctx["composite_formula_short"],
        "baseline_composite": ctx["baseline_composite"],
    }


def _l1_generate_enter(d: dict, ctx: dict) -> dict:
    acc = d.get("current_accuracy", 0.0)
    preview = (d.get("prompt_preview") or "").replace("\n", " ").strip()
    if not preview:
        preview = "(empty starting prompt - param-only optimization)"
    else:
        preview = _truncate(preview, 50)
    n = d.get("n_variants", 0)
    model = d.get("model") or "(default)"
    creativity = d.get("creativity", 0.7)
    has_critique = bool(d.get("has_l1_critique"))

    new_flat = flatten_sp_summary(d.get("pipeline_params"))
    parent_prompt_fields = d.get("parent_prompt_fields") or {}
    for field_name, value in parent_prompt_fields.items():
        if value:
            new_flat[field_name] = str(value)
    if ctx.get("round_num", 0) == 0:
        ctx["previous_sp_flat"] = dict(ctx.get("original_sp_flat") or {})
    else:
        ctx["previous_sp_flat"] = dict(ctx.get("current_sp_flat") or {})
    ctx["current_sp_flat"] = new_flat

    return {
        "round": ctx.get("round_num", 0) + 1,
        "max_rounds": ctx.get("max_rounds", 0),
        "l1_stall_count": ctx.get("l1_stall_count", 0),
        "patience": ctx.get("patience", 0),
        "current_acc": acc,
        "prompt_preview": preview,
        "n_variants": n,
        "model": model,
        "creativity": creativity,
        "has_l1_critique": has_critique,
        "current_sp_flat": dict(new_flat),
    }


def _l1_generate_exit(d: dict, ctx: dict) -> dict:
    n = d.get("n_candidates", 0)
    source = "disk" if d.get("loaded_from_disk") else "llm"
    ctx["n_scoring_queries"] = d.get("n_scoring_queries", 0)
    ctx["candidates_meta"] = d.get("candidates", [])

    columns: list[dict] = [
        {"label": "Start", "flat": dict(ctx.get("original_sp_flat") or {})},
        {"label": "Parent", "flat": dict(ctx.get("current_sp_flat") or {})},
    ]
    parent = ctx.get("current_sp_flat") or {}
    clone_labels: list[str] = []
    for c in ctx["candidates_meta"]:
        label = f"C{c['idx'] + 1}"
        flat = build_candidate_flat(parent, c)
        if flat == parent:
            clone_labels.append(label)
        columns.append({"label": label, "flat": flat})

    l1_yield = float(d.get("l1_yield", 1.0))
    l1_n_no_op = int(d.get("l1_n_no_op", 0))
    l1_n_duplicate = int(d.get("l1_n_duplicate", 0))

    return {
        "n_candidates": n,
        "source": source,
        "n_scoring_queries": ctx["n_scoring_queries"],
        "l1_yield": l1_yield,
        "l1_n_no_op": l1_n_no_op,
        "l1_n_duplicate": l1_n_duplicate,
        "clone_labels": clone_labels,
        "sp_diff": {
            "columns": columns,
            "node_param_keys": ctx.get("node_param_keys"),
            "round_num": ctx.get("round_num", 0) + 1,
            "clone_labels": clone_labels,
            "l1_yield": l1_yield,
            "l1_n_no_op": l1_n_no_op,
            "l1_n_duplicate": l1_n_duplicate,
        },
    }


def _l1_score_enter(d: dict, ctx: dict) -> dict:
    n_cand = d.get("n_candidates", 0)
    n_q = d.get("n_queries", 0)
    n_calls = n_cand * n_q
    scoring_pp = d.get("current_pipeline_params")
    if scoring_pp is not None:
        ctx["current_pipeline_params"] = scoring_pp
    return {
        "n_candidates": n_cand,
        "n_queries": n_q,
        "n_calls": n_calls,
    }


def _l1_score_exit(d: dict, ctx: dict) -> dict:
    w_acc = d.get("winner_accuracy", 0.0)
    w_comp = d.get("winner_composite")
    improved = bool(d.get("improved", False))
    action = d.get("next_action", "?")
    raw_scores = list(d.get("candidate_scores", []))

    scores: list[dict] = []
    for i, s in enumerate(raw_scores):
        scores.append(
            {
                "label": f"C{i + 1}",
                "accuracy": float(s.get("accuracy", 0.0)),
                "composite": s.get("composite"),
                "hits": int(s.get("hits", 0)),
                "total": int(s.get("total", 0)),
                "escalation_aborted": bool(s.get("escalation_aborted", False)),
            }
        )
    non_aborted = [s for s in scores if not s["escalation_aborted"]]
    best = (
        max(
            non_aborted,
            key=lambda s: (s.get("composite") or s["accuracy"], s["accuracy"]),
        )
        if non_aborted
        else {}
    )
    winner_label = best.get("label", "?")
    winner_hits = int(best.get("hits", 0))
    winner_total = int(best.get("total", 0))

    baseline_acc = ctx.get("baseline_accuracy", 0.0)
    delta = w_acc - baseline_acc if improved else 0.0
    p_value: float | None = None
    if improved and winner_total > 0:
        bl_hits = round(baseline_acc * winner_total)
        p_value = proportion_test(winner_hits, winner_total, bl_hits, winner_total)
        ctx["baseline_accuracy"] = w_acc

    return {
        "baseline_acc": baseline_acc,
        "scores": scores,
        "winner_label": winner_label,
        "winner_accuracy": w_acc,
        "winner_composite": w_comp,
        "winner_evaluators": dict(d.get("winner_evaluators") or {}),
        "winner_hits": winner_hits,
        "winner_total": winner_total,
        "improved": improved,
        "delta": delta,
        "p_value": p_value,
        "next_action": action,
        "l1_critique_text": d.get("l1_critique_text", "") or "",
        "composite_formula": ctx.get("composite_formula"),
        "composite_formula_short": ctx.get("composite_formula_short"),
        "baseline_composite": ctx.get("baseline_composite"),
    }


def _escalation_enter(d: dict, ctx: dict) -> dict:
    return {
        "check_name": d.get("check_name", "?"),
        "target": d.get("target", "?"),
        "degraded_rate": d.get("degraded_rate", 0.0),
        "warning_types": dict(d.get("warning_types") or {}),
    }


def _escalation_exit(d: dict, ctx: dict) -> dict:
    return {
        "classifications": [
            {
                "warning_type": c.get("warning_type", ""),
                "status": c.get("status", ""),
            }
            for c in (d.get("classifications") or [])
        ]
    }


def _refine_enter(d: dict, ctx: dict) -> dict:
    params = d.get("current_params") or {}
    serialized = {k: str(v) for k, v in params.items()}
    return {
        "l2_round": d.get("l2_round", "?"),
        "l1_stall_count": d.get("l1_stall_count", "?"),
        "current_acc": d.get("current_accuracy", 0.0),
        "best_acc": d.get("best_accuracy", 0.0),
        "current_params": serialized,
        "n_params": len(params),
    }


def _refine_exit(d: dict, ctx: dict) -> dict:
    return {
        "param_changes_count": d.get("param_changes_count", 0),
        "task_context_changed": bool(d.get("task_context_changed", False)),
        "action": d.get("action", "continue"),
        "changes_description": d.get("changes_description", ""),
        "warned_queries": d.get("warned_queries", 0),
        "top_warning": d.get("top_warning", ""),
        "l2_prompt": d.get("l2_prompt", "") or "",
        "l2_response_json": d.get("l2_response"),
    }


def _probe_enter(d: dict, ctx: dict) -> dict:
    queries = list(d.get("probe_queries") or [])
    return {
        "n_probe_queries": d.get("n_probe_queries", len(queries)),
        "probe_queries": queries,
    }


def _probe_exit(d: dict, ctx: dict) -> dict:
    return {
        "n_probed": d.get("n_probed", 0),
        "probe_hits": d.get("probe_hits", 0),
    }


def _plan_enter(d: dict, ctx: dict) -> dict:
    plan = d.get("current_plan_preview", "") or ""
    return {
        "l3_round": d.get("l3_round", "?"),
        "l2_stall_count": d.get("l2_stall_count", "?"),
        "current_plan_preview": _truncate(plan, 55),
    }


def _plan_exit(d: dict, ctx: dict) -> dict:
    new_plan = d.get("new_plan_preview", "") or ""
    return {
        "new_plan_preview": _truncate(new_plan, 55),
        "changes_description": d.get("changes_description", ""),
    }


_BUILDERS: dict[str, Any] = {
    "init:enter": _init_enter,
    "init:exit": _init_exit,
    "l1_generate:enter": _l1_generate_enter,
    "l1_generate:exit": _l1_generate_exit,
    "l1_score:enter": _l1_score_enter,
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


def build_phase_view(event: PhaseEvent, ctx: dict) -> dict | None:
    """Dispatch a PhaseEvent to its builder; track round_num side-effect.

    Returns the serializable view dict, or ``None`` if no builder is
    registered for this (phase, event) pair (the emitter then skips
    the JSONL append).
    """
    if event.round is not None:
        ctx["round_num"] = event.round
    key = f"{event.phase}:{event.event}"
    builder = _BUILDERS.get(key)
    if builder is None:
        return None
    return builder(event.data, ctx)
