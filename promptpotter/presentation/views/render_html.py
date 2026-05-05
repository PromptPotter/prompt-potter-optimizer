"""HTML render target — typed View → minimal HTML for notebook / future webapp.

Stub coverage: ``RoundCompleteView`` (winner verdict + scoreboard table) and
``FinalWinnerView`` (final prompt + pipeline params). Other views fall
through to an empty string; the notebook keeps using ANSI text for live
narrative until each one is needed.

Pure function ``to_html(view) → str``: no I/O, no IPython imports here.
Callers (``notebook_run.py``) wrap the return value in ``IPython.display.HTML``.
"""

from __future__ import annotations

import html
import json
from collections.abc import Callable
from typing import Any

from promptpotter.presentation.views.view_models import (
    AnyView,
    FinalWinnerView,
    RoundCompleteView,
    ScoreEntry,
)

__all__ = ["to_html"]


def _esc(s: Any) -> str:
    return html.escape(str(s))


def _pct(x: float) -> str:
    return f"{x:.1%}"


def _row(s: ScoreEntry, *, is_winner: bool) -> str:
    if s.escalation_aborted:
        winner_mark = '<span style="color:#a87900">aborted</span>'
    elif is_winner:
        winner_mark = '<span style="color:#0a7d0a;font-weight:bold">★</span>'
    else:
        winner_mark = ""
    composite_fitness = "—" if s.composite_fitness is None else f"{s.composite_fitness:.4f}"
    return (
        f"<tr><td>{_esc(s.label)}</td>"
        f"<td style='text-align:right'>{_pct(s.accuracy)}</td>"
        f"<td style='text-align:right'>[{_pct(s.ci_lo)}-{_pct(s.ci_hi)}]</td>"
        f"<td style='text-align:right'>{composite_fitness}</td>"
        f"<td style='text-align:right'>{s.hits}/{s.total}</td>"
        f"<td>{winner_mark}</td></tr>"
    )


def _render_round_complete(v: RoundCompleteView) -> str:
    color = "#0a7d0a" if v.improved else "#a87900"
    label = "✓ IMPROVED" if v.improved else "⚠ NO IMPROVEMENT"
    delta_str = (
        f" &nbsp;(was {_pct(v.baseline_acc)}, <span style='color:{color}'>+{_pct(v.delta)}</span>)"
        if v.improved
        else ""
    )
    p_str = f" &nbsp;p={v.p_value:.3f}" if v.improved and v.p_value is not None else ""
    rows = "\n".join(_row(s, is_winner=(s.label == v.winner_label)) for s in v.scores)
    return (
        "<div style='font-family:monospace'>"
        f"<div style='font-weight:bold;color:{color}'>"
        f"Round {v.round} &middot; {label} &mdash; "
        f"{_pct(v.winner_accuracy)}{delta_str}{p_str}</div>"
        "<table style='border-collapse:collapse;font-family:monospace;font-size:90%'>"
        "<thead><tr style='border-bottom:1px solid #888'>"
        "<th>Cand</th><th>Accuracy</th><th>95% CI</th>"
        "<th>Composite</th><th>Hits</th><th></th></tr></thead>"
        f"<tbody>{rows}</tbody></table></div>"
    )


def _render_final(final: FinalWinnerView) -> str:
    prompt_json = json.dumps(final.winner_prompt_fields, indent=2, ensure_ascii=False, default=str)
    pp_json = json.dumps(final.winner_pipeline_params, indent=2, ensure_ascii=False, default=str)
    return (
        "<div style='font-family:monospace'>"
        "<h3 style='margin:8px 0 4px'>Final Winner</h3>"
        "<details><summary>Prompt fields</summary>"
        f"<pre>{_esc(prompt_json)}</pre></details>"
        "<details><summary>Pipeline params</summary>"
        f"<pre>{_esc(pp_json)}</pre></details></div>"
    )


_RENDERERS: dict[type, Callable[..., str]] = {
    RoundCompleteView: _render_round_complete,
    FinalWinnerView: _render_final,
}


def to_html(view: AnyView) -> str:
    """Dispatch a typed view to its HTML renderer; ``""`` for unhandled types."""
    fn = _RENDERERS.get(type(view))
    return fn(view) if fn else ""
