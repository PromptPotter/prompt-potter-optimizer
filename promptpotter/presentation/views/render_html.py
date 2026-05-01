"""HTML render target — typed View → minimal HTML for notebook / future webapp.

Stub coverage: ``RoundCompleteView`` (winner verdict + scoreboard table),
``LogMdView`` (status + per-round digest), ``FinalWinnerView`` (final
prompt + pipeline params). Other views fall through to an empty string;
the notebook keeps using ANSI text for live narrative until each one is
needed.

Pure function ``to_html(view) → str``: no I/O, no IPython imports here.
Callers (``notebook_run.py``) wrap the return value in ``IPython.display.HTML``.
"""

from __future__ import annotations

import html
import json
from typing import Any

from promptpotter.presentation.views.view_models import (
    AnyView,
    FinalWinnerView,
    LogMdView,
    RoundCompleteView,
    RoundDigestView,
    ScoreEntry,
)

__all__ = ["to_html"]


def _esc(s: Any) -> str:
    return html.escape(str(s))


def _pct(x: float) -> str:
    return f"{x:.1%}"


def _row(s: ScoreEntry, *, is_winner: bool) -> str:
    delta_class = ""
    winner_mark = ""
    if s.escalation_aborted:
        winner_mark = '<span style="color:#a87900">aborted</span>'
    elif is_winner:
        winner_mark = '<span style="color:#0a7d0a;font-weight:bold">★</span>'
        delta_class = "winner"
    composite = "—" if s.composite is None else f"{s.composite:.4f}"
    return (
        f'<tr class="{delta_class}">'
        f"<td>{_esc(s.label)}</td>"
        f"<td style='text-align:right'>{_pct(s.accuracy)}</td>"
        f"<td style='text-align:right'>[{_pct(s.ci_lo)}-{_pct(s.ci_hi)}]</td>"
        f"<td style='text-align:right'>{composite}</td>"
        f"<td style='text-align:right'>{s.hits}/{s.total}</td>"
        f"<td>{winner_mark}</td>"
        "</tr>"
    )


def _render_round_complete(v: RoundCompleteView) -> str:
    verdict_color = "#0a7d0a" if v.improved else "#a87900"
    verdict_label = "✓ IMPROVED" if v.improved else "⚠ NO IMPROVEMENT"
    delta_str = ""
    if v.improved:
        delta_str = (
            f" &nbsp;(was {_pct(v.baseline_acc)}, "
            f'<span style="color:{verdict_color}">+{_pct(v.delta)}</span>)'
        )
    p_str = ""
    if v.improved and v.p_value is not None:
        p_str = f" &nbsp;p={v.p_value:.3f}"

    rows = "\n".join(_row(s, is_winner=(s.label == v.winner_label)) for s in v.scores)
    table = (
        '<table style="border-collapse:collapse;font-family:monospace;font-size:90%">'
        '<thead><tr style="border-bottom:1px solid #888">'
        "<th>Cand</th><th>Accuracy</th><th>95% CI</th>"
        "<th>Composite</th><th>Hits</th><th></th>"
        "</tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )
    return (
        '<div style="font-family:monospace">'
        f'<div style="font-weight:bold;color:{verdict_color}">'
        f"Round {v.round} &middot; {verdict_label} &mdash; "
        f"{_pct(v.winner_accuracy)}{delta_str}{p_str}</div>"
        f"{table}"
        "</div>"
    )


def _render_round_digest(rd: RoundDigestView) -> str:
    badge_color = "#0a7d0a" if rd.improved else "#a87900"
    parts: list[str] = [
        '<div style="font-family:monospace;margin:8px 0">',
        f'<div style="font-weight:bold">Round {rd.round} &middot; '
        f"{_esc(rd.label)} &middot; {_pct(rd.accuracy)} "
        f'<span style="color:{badge_color}">'
        f"{'improved' if rd.improved else 'no change'}</span></div>",
        "<ul style='margin:2px 0 0 16px;padding:0'>",
        f"<li>hits: {rd.hits}/{rd.total}</li>",
        f"<li>composite: {rd.composite:.4f}</li>",
    ]
    if rd.changes_description:
        parts.append(f"<li>changes: {_esc(rd.changes_description)}</li>")
    if rd.l2_directive:
        parts.append(f"<li>L2 directive: {_esc(rd.l2_directive)}</li>")
    parts.append("</ul>")
    if rd.l1_critique_text:
        parts.append(
            '<blockquote style="border-left:3px solid #ccc;'
            'margin:4px 0;padding:2px 8px;color:#555">'
            f"{_esc(rd.l1_critique_text).replace(chr(10), '<br>')}"
            "</blockquote>"
        )
    parts.append("</div>")
    return "".join(parts)


def _render_final(final: FinalWinnerView) -> str:
    prompt_json = json.dumps(final.winner_prompt_fields, indent=2, ensure_ascii=False, default=str)
    pp_json = json.dumps(final.winner_pipeline_params, indent=2, ensure_ascii=False, default=str)
    return (
        '<div style="font-family:monospace">'
        '<h3 style="margin:8px 0 4px">Final Winner</h3>'
        "<details><summary>Prompt fields</summary>"
        f"<pre>{_esc(prompt_json)}</pre></details>"
        "<details><summary>Pipeline params</summary>"
        f"<pre>{_esc(pp_json)}</pre></details>"
        "</div>"
    )


def _render_log_md(v: LogMdView) -> str:
    s = v.status
    parts: list[str] = [
        '<div style="font-family:monospace">',
        f"<h2 style='margin:0 0 4px'>Campaign {_esc(s.campaign_id) or '(unknown)'}</h2>",
        "<ul style='margin:0 0 8px 16px;padding:0'>",
        f"<li>status: <b>{_esc(s.status)}</b></li>",
        f"<li>stop reason: <code>{_esc(s.stop_reason)}</code></li>",
        f"<li>baseline: {_pct(s.baseline_accuracy)}</li>",
        f"<li>best: {_pct(s.best_accuracy)}"
        + (f" (round {s.best_round})" if s.best_round is not None else "")
        + "</li>",
        f"<li>rounds completed: {s.rounds_completed}</li>",
        "</ul>",
        "<h3 style='margin:8px 0 4px'>Rounds</h3>",
    ]
    if not v.rounds:
        parts.append("<p><i>No rounds yet.</i></p>")
    else:
        parts.extend(_render_round_digest(rd) for rd in v.rounds)
    if v.final is not None:
        parts.append(_render_final(v.final))
    parts.append("</div>")
    return "".join(parts)


def to_html(view: AnyView) -> str:
    """Dispatch a typed view to its HTML renderer.

    Stub: only ``RoundCompleteView``, ``LogMdView``, ``FinalWinnerView``
    return non-empty strings today. Other views return ``""`` — extend
    one at a time as the notebook surfaces them.
    """
    if isinstance(view, RoundCompleteView):
        return _render_round_complete(view)
    if isinstance(view, LogMdView):
        return _render_log_md(view)
    if isinstance(view, FinalWinnerView):
        return _render_final(view)
    return ""
