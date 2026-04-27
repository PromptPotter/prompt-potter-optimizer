"""Dashboard / status renderers — consume the slim ``dashboard.json``.

``dashboard.json`` is the machine-readable live-state scalar file. For
narrative round views (per-round leaderboards, critiques, node I/O),
read ``trials/trial_NNNN.json`` and ``candidates/round_NNNN.json``
directly. This renderer exists for ``show-status`` and shows only what
``dashboard.json`` carries.
"""

from __future__ import annotations

from typing import Any

from promptpotter.presentation.views.formatting import fmt_pct


def _get(d: dict, key: str, default: Any = "-") -> Any:
    v = d.get(key, default)
    return default if v is None or v == "" else v


def render_dashboard(dashboard: dict) -> str:
    """Pretty-print the slim ``dashboard.json`` state."""
    if not dashboard:
        return "No dashboard data."

    d = dashboard
    lines = [
        "\nDASHBOARD",
        "=" * 70,
        f"  phase         : {_get(d, 'phase')}",
        f"  round         : {_get(d, 'round')}",
        f"  layer         : {_get(d, 'layer')}",
        f"  candidate     : {_get(d, 'candidate')}",
        f"  query         : {_get(d, 'query')}",
        f"  patience      : {_get(d, 'patience')}",
        f"  baseline      : {fmt_pct(d.get('baseline'))}",
        f"  best          : {fmt_pct(d.get('best'))}",
        f"  current       : {fmt_pct(d.get('current_acc'))}",
        f"  cycle_id      : {_get(d, 'cycle_id')}",
    ]
    lines += [
        f"  degraded      : {_get(d, 'degraded_count', 0)}",
        f"  errors        : {_get(d, 'error_count', 0)}",
        f"  queries_total : {_get(d, 'total_queries_scored', 0)}",
        f"  backend_calls : {_get(d, 'total_backend_calls', 0)}",
        f"  n_variants    : {_get(d, 'n_variants')}",
        f"  sp_budget     : {_get(d, 'sp_budget_ttest')}",
    ]
    if d.get("query_in_flight"):
        payload = d.get("current_query_payload") or ""
        lines += [
            "  in flight     :",
            f"    since       : {_get(d, 'query_started_at')}",
            f"    payload     : {payload}",
        ]

    hitl = d.get("hitl") or {}
    lines += [
        "",
        "HITL",
        "=" * 70,
        f"  requested_state      : {_get(hitl, 'requested_state')}",
        f"  pause_point          : {_get(hitl, 'pause_point')}",
        f"  stop_reason          : {_get(hitl, 'stop_reason')}",
    ]
    return "\n".join(lines)


def render_status(
    dashboard: dict,
    control: dict | None = None,
    result: dict | None = None,
) -> str:
    """Human form for ``show-status`` — dashboard + last result.

    HITL signals are nested inside ``dashboard["hitl"]`` so the separate
    ``control`` arg is accepted but ignored (kept for caller shape). For
    a narrative view of a finished round (summary, critique, leaderboard,
    optimizer node I/O, per-query detail), read
    ``trials/trial_NNNN.json`` and ``candidates/round_NNNN.json``.
    """
    del control
    parts = [render_dashboard(dashboard)]

    if result:
        parts.append(
            "\nLAST RESULT\n" + "=" * 70 + "\n"
            f"  best_accuracy : {fmt_pct(result.get('best_accuracy'))}\n"
            f"  best_round    : {result.get('best_round', '-')}\n"
            f"  n_rounds      : {result.get('n_rounds', '-')}\n"
            f"  stop_reason   : {result.get('stop_reason', '-')}"
        )
    return "\n".join(parts)
