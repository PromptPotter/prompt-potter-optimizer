"""Dashboard / status renderers — consume ``dashboard.json`` scalar tiers.

Tier layout mirrors ``infrastructure/persistence/session_emitter.py``
``_make_initial_state`` — execution / timing / pipeline / quality /
historical / config. Consumers pass the loaded dict unchanged; missing
keys render as ``-`` so partial dashboards from early-cycle runs still
display cleanly.
"""

from __future__ import annotations

from typing import Any

from promptpotter.presentation.views.formatting import fmt_pct


def _get(d: dict, key: str, default: Any = "-") -> Any:
    v = d.get(key, default)
    return default if v is None or v == "" else v


def _fmt_secs(v: Any) -> str:
    try:
        s = float(v)
    except (TypeError, ValueError):
        return "-"
    if s < 60:
        return f"{s:.1f}s"
    m, sec = divmod(s, 60)
    if m < 60:
        return f"{int(m)}m{int(sec):02d}s"
    h, m = divmod(int(m), 60)
    return f"{h}h{m:02d}m"


def render_dashboard(dashboard: dict) -> str:
    """Pretty-print the scalar dashboard tiers from ``dashboard.json``."""
    if not dashboard:
        return "No dashboard data."

    d = dashboard
    active = d.get("active_nodes") or []
    excluded = d.get("excluded_nodes") or []
    evals = d.get("round_evaluators") or {}

    lines = [
        "\nDASHBOARD",
        "=" * 70,
        "  Execution",
        f"    phase         : {_get(d, 'phase')}",
        f"    workflow      : {_get(d, 'workflow')}",
        f"    round         : {_get(d, 'round')} / {_get(d, 'max_rounds')}",
        f"    layer         : {_get(d, 'layer')}",
        f"    candidate     : {_get(d, 'candidate')}",
        f"    patience      : {_get(d, 'patience')}",
        f"    baseline      : {fmt_pct(d.get('baseline'))}",
        f"    best          : {fmt_pct(d.get('best'))}  (round {_get(d, 'best_round')})",
        f"    current       : {fmt_pct(d.get('current_acc'))}",
        f"    cycle_id      : {_get(d, 'cycle_id')}",
    ]
    if d.get("stop_reason"):
        lines.append(f"    stop_reason   : {d['stop_reason']}")
    if d.get("pause_point"):
        lines.append(f"    pause_point   : {d['pause_point']}")

    lines += [
        "  Timing",
        f"    elapsed       : {_fmt_secs(d.get('elapsed_s'))}",
        f"    round_elapsed : {_fmt_secs(d.get('round_elapsed_s'))}",
        f"    avg_query     : {_fmt_secs(d.get('avg_query_time_s'))}",
        f"    eta           : {_fmt_secs(d.get('eta_s'))}",
        "  Pipeline",
        f"    active        : {', '.join(active) if active else '-'}",
        f"    excluded      : {', '.join(excluded) if excluded else '-'}",
        f"    terminated_at : {_get(d, 'terminated_at')}",
        f"    cache_hit_rate: {fmt_pct(d.get('cache_hit_rate'))}",
        "  Quality",
        f"    hit_rate      : {fmt_pct(d.get('hit_rate'))}",
        f"    degraded      : {_get(d, 'degraded_count', 0)}",
        f"    errors        : {_get(d, 'error_count', 0)}",
        f"    improvement   : streak {_get(d, 'improvement_streak', 0)}",
        "  Historical",
        f"    rounds_done   : {_get(d, 'rounds_completed', 0)}",
        f"    queries_total : {_get(d, 'total_queries_scored', 0)}",
        f"    backend_calls : {_get(d, 'total_backend_calls', 0)}",
        "  Config",
        f"    model         : {_get(d, 'model')}",
        f"    n_variants    : {_get(d, 'n_variants')}",
        f"    sp_budget     : {_get(d, 'sp_budget_ttest')}",
    ]
    if evals:
        lines.append("  Evaluators (this round)")
        for k, v in evals.items():
            try:
                lines.append(f"    {k:<14s}: {float(v):.4f}")
            except (TypeError, ValueError):
                lines.append(f"    {k:<14s}: {v}")
    return "\n".join(lines)


def render_status(
    dashboard: dict,
    control: dict | None = None,
    result: dict | None = None,
) -> str:
    """Human form for ``show-status`` — dashboard + control + last result."""
    parts = [render_dashboard(dashboard)]

    if control:
        req = control.get("requested_state", "-")
        pause = control.get("pause_before_scoring", False)
        parts.append(
            "\nCONTROL\n" + "=" * 70 + f"\n  requested_state     : {req}"
            f"\n  pause_before_score  : {pause}"
        )

    if result:
        parts.append(
            "\nLAST RESULT\n" + "=" * 70 + "\n"
            f"  best_accuracy : {fmt_pct(result.get('best_accuracy'))}\n"
            f"  best_round    : {result.get('best_round', '-')}\n"
            f"  n_rounds      : {result.get('n_rounds', '-')}\n"
            f"  stop_reason   : {result.get('stop_reason', '-')}"
        )
    return "\n".join(parts)
