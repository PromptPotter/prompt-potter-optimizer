"""Render the live sliding-window dashboard at ``campaigns/{cycle_id}/log.md``.

One human-readable file, overwritten on every event. Layout:
header · CURRENT round (in-flight, summary) · LAST COMPLETED round
(critique + leaderboard + optimizer-node I/O) · EARLIER (compact list) ·
PER-QUERY DETAIL (all candidates × all samples of the current round,
long — appended at the bottom). Overwrite semantics kill the
duplicate-header bug that append-only caused on resume.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

__all__ = ["fmt_sample_line", "render_dashboard_md", "round_summary_from_trial"]


# Compact badge for the per-query terminator. Values above or below the
# dict are shown as the first two characters of the node name.
_NODE_BADGES: dict[str, str] = {
    "llm_only": "ai",
    "llm_ranking": "ai",
    "entity_profiling": "ai",
    "cache_lookup": "cache",
    "fuzzy_matching": "fz",
    "token_matching": "tk",
    "web_search": "ws",
}


def render_dashboard_md(
    campaign_dir: Path,
    state: dict[str, Any],
    current: dict[str, Any],
    history: list[dict[str, Any]],
    *,
    max_rounds: int = 0,
    model: str = "",
    active_nodes: list[str] | None = None,
    dataset_count: int | None = None,
    backend_id: str | None = None,
    elapsed_s: float = 0.0,
) -> str:
    """Compose the full sliding-window dashboard markdown.

    ``state`` is the emitter's live scalar dict. ``current`` carries the
    in-flight round's per-candidate state. ``history`` lists completed
    rounds oldest-first; the last entry renders as LAST COMPLETED.
    """
    parts: list[str] = [
        _header(
            state,
            history,
            max_rounds=max_rounds,
            model=model,
            active_nodes=active_nodes or [],
            dataset_count=dataset_count,
            backend_id=backend_id,
            elapsed_s=elapsed_s,
        )
    ]
    parts.append(_current_section(state, current))
    if history:
        parts.append(_last_completed_section(campaign_dir, history[-1]))
        if len(history) > 1:
            parts.append(_earlier_section(history[:-1]))
    detail = _per_query_detail(current)
    if detail:
        parts.append(detail)
    return "\n\n".join(p for p in parts if p).rstrip() + "\n"


def round_summary_from_trial(trial: dict[str, Any]) -> dict[str, Any]:
    """Extract the round_summary shape from a trial JSON dict. Used on
    resume to rebuild ``_round_history`` from ``trials/`` on disk."""
    r = int(trial.get("round", 0))
    accuracy = float(trial.get("accuracy", 0.0))
    hits = int(trial.get("hits", 0))
    total = int(trial.get("total", 0))
    prompt_fields = trial.get("prompt_fields") or {}
    label = prompt_fields.get("changes_description") or trial.get("label") or ""

    leaderboard: list[dict[str, Any]] = []
    cand_scores = trial.get("candidate_scores") or []
    for idx, cs in enumerate(cand_scores):
        leaderboard.append(
            {
                "idx": idx,
                "accuracy": float(cs.get("accuracy", 0.0)),
                "hits": int(cs.get("hits", 0)),
                "total": int(cs.get("total", 0)),
                "label": cs.get("changes_description") or cs.get("label") or "",
                "is_winner": bool(cs.get("is_winner", False)),
                "eliminated_at": cs.get("eliminated_at"),
            }
        )

    return {
        "round": r,
        "accuracy": accuracy,
        "hits": hits,
        "total": total,
        "winner_label": label,
        "improved": bool(trial.get("improved", False)),
        "leaderboard": leaderboard,
    }


# --- sections --------------------------------------------------------------


def _header(
    state: dict[str, Any],
    history: list[dict[str, Any]],
    *,
    max_rounds: int,
    model: str,
    active_nodes: list[str],
    dataset_count: int | None,
    backend_id: str | None,
    elapsed_s: float,
) -> str:
    cycle = state.get("cycle_id") or "—"
    phase = state.get("phase") or "—"
    r = state.get("round", 0)
    baseline = float(state.get("baseline") or 0.0)
    best = float(state.get("best") or 0.0)
    patience = state.get("patience", "0/0")
    hitl = state.get("hitl") or {}
    stop = hitl.get("stop_reason")
    requested = hitl.get("requested_state") or "running"
    pause_before_l2 = bool(hitl.get("pause_before_l2_scoring"))
    pause_point = hitl.get("pause_point")

    best_r, streak = _derive_best_and_streak(history)
    status = f"stopped — {stop}" if stop else "active"
    setup_bits = [f"Model: {model or '—'}", f"Active: {', '.join(active_nodes) or '—'}"]
    if dataset_count is not None:
        setup_bits.insert(0, f"Dataset: {dataset_count} queries")
    if backend_id:
        setup_bits.insert(0, f"Backend: {backend_id}")

    best_line = f"Baseline {baseline:.1%} · Best {best:.1%}"
    if best_r is not None:
        best_line += f" (R{best_r})"
    best_line += f" · Streak +{streak} · Patience {patience}"

    hitl_bits = [f"requested={requested}"]
    if pause_before_l2:
        hitl_bits.append("pause-before-L2")
    if pause_point:
        hitl_bits.append(f"paused@{pause_point}")
    hitl_line = "HITL: " + " · ".join(hitl_bits)

    return "\n".join(
        [
            f"# Campaign `{cycle}` — {status}",
            f"Phase: {phase} · Round {r}/{max_rounds} · elapsed {_fmt_elapsed(elapsed_s)}",
            " · ".join(setup_bits),
            best_line,
            hitl_line,
        ]
    )


def _current_section(state: dict[str, Any], current: dict[str, Any]) -> str:
    phase = state.get("phase") or "—"
    r = current.get("round")
    if r is None:
        r = state.get("round", 0)
    candidates = current.get("candidates") or {}
    header = f"═══ CURRENT — R{r} ({phase}) ═══"

    if not candidates:
        return f"{header}\n(no candidate activity yet)"

    cand_label = state.get("candidate") or ""
    cur_query = state.get("query") or ""
    running_acc = _running_accuracy(candidates)

    lines = [header]
    if cand_label or cur_query:
        lines.append(
            f"In flight: candidate {cand_label} · query {cur_query} · "
            f"running accuracy {running_acc:.1%}"
        )
    lines.append("")
    lines.append("Candidates this round:")
    for idx in sorted(candidates.keys()):
        c = candidates[idx]
        label = _trim(c.get("label") or c.get("changes_description") or "—", 70)
        scores = c.get("scores")
        if scores is None:
            samples = c.get("samples") or []
            n_scored = len(samples)
            hits = sum(1 for s in samples if s.get("hit"))
            status = "pending" if n_scored == 0 else f"scoring {n_scored} · {hits}/{n_scored} hits"
            lines.append(f"  C{idx + 1}  —     {label} · {status}")
        else:
            acc = float(scores.get("accuracy") or 0.0)
            hits = int(scores.get("hits") or 0)
            total = int(scores.get("total") or 0)
            invalid = "  INVALID" if scores.get("invalid") else ""
            lines.append(f"  C{idx + 1}  {acc:.1%}  {label} · {hits}/{total}{invalid}")
    return "\n".join(lines)


def _last_completed_section(campaign_dir: Path, rs: dict[str, Any]) -> str:
    r = rs.get("round", 0)
    winner = _trim(rs.get("winner_label") or "—", 80)
    acc = float(rs.get("accuracy") or 0.0)
    improved = bool(rs.get("improved", False))
    mark = "  ↑ improved" if improved else ""

    lines = [
        f"═══ LAST COMPLETED — R{r} ═══",
        f"Winner: {winner}",
        f"Accuracy: {acc:.1%}{mark}",
    ]

    critique = _read_critique(campaign_dir, r)
    if critique:
        lines.append("")
        lines.append("L1 critique:")
        for cl in critique.splitlines():
            lines.append(f"  > {cl}")

    leaderboard = rs.get("leaderboard") or []
    if leaderboard:
        lines.append("")
        lines.append("Leaderboard:")
        for lb in leaderboard:
            label = _trim(lb.get("label") or "—", 60)
            acc_l = float(lb.get("accuracy") or 0.0)
            hits = int(lb.get("hits") or 0)
            total = int(lb.get("total") or 0)
            tags = []
            if lb.get("is_winner"):
                tags.append("← winner")
            if lb.get("eliminated_at"):
                tags.append(f"eliminated @ q{lb['eliminated_at']}")
            tagstr = ("  " + " · ".join(tags)) if tags else ""
            lines.append(
                f"  C{lb.get('idx', 0) + 1}  {acc_l:.1%}  {label} · {hits}/{total}{tagstr}"
            )

    nodes = _optimizer_nodes_section(campaign_dir, r)
    if nodes:
        lines.append("")
        lines.extend(nodes)

    return "\n".join(lines)


def _earlier_section(older: list[dict[str, Any]]) -> str:
    lines = ["═══ EARLIER ═══"]
    for rs in older:
        r = rs.get("round", 0)
        acc = float(rs.get("accuracy") or 0.0)
        hits = int(rs.get("hits") or 0)
        total = int(rs.get("total") or 0)
        winner = _trim(rs.get("winner_label") or "", 60)
        mark = " ↑" if rs.get("improved") else ""
        lines.append(f"R{r}  {acc:.1%}  {hits}/{total}  {winner}{mark}")
    return "\n".join(lines)


def _per_query_detail(current: dict[str, Any]) -> str:
    candidates = current.get("candidates") or {}
    if not candidates:
        return ""
    r = current.get("round", 0)

    lines = [f"═══ PER-QUERY DETAIL — R{r} ═══"]
    for idx in sorted(candidates.keys()):
        c = candidates[idx]
        label = _trim(c.get("label") or c.get("changes_description") or "—", 80)
        lines.append("")
        lines.append(f"### Candidate {idx + 1} — {label}")
        samples = c.get("samples") or []
        if not samples:
            lines.append("  (pending)")
        else:
            for s in samples:
                lines.append(fmt_sample_line(s))
        scores = c.get("scores")
        if scores is not None:
            acc = float(scores.get("accuracy") or 0.0)
            hits = int(scores.get("hits") or 0)
            total = int(scores.get("total") or 0)
            lines.append(f"  → Final: {hits}/{total} · {acc:.1%}")
    return "\n".join(lines)


# --- helpers ---------------------------------------------------------------


def fmt_sample_line(s: dict[str, Any]) -> str:
    """One rich line per query: elapsed · idx · hit/miss · badge · cache ·
    token counts (when present) · prediction · gt · query prefix.

    Shared between the ``log.md`` PER-QUERY DETAIL section and the
    ``dashboard.json::current_round.nodes.l1_score.output.candidates[].samples``
    list — the two surfaces use the same compact CLI format so the dashboard
    JSON stays scannable instead of bloating with full ~2 kB query strings.
    """
    qi = int(s.get("qi", 0))
    hit = bool(s.get("hit"))
    cached = bool(s.get("cached"))
    time_s = float(s.get("time_s") or 0.0)
    badge = _NODE_BADGES.get(s.get("terminated_at") or "", (s.get("terminated_at") or "?")[:2])
    cache_icon = "📖" if cached else " "
    mark = "HIT " if hit else "MISS"
    query = _trim(s.get("query") or "", 42)
    pred = _trim(s.get("prediction") or "", 28)
    gt = _trim(s.get("ground_truth") or "", 20)
    in_tok = s.get("input_tokens")
    out_tok = s.get("output_tokens")
    tok_seg = ""
    if in_tok is not None or out_tok is not None:
        tok_seg = f" in={in_tok if in_tok is not None else '-'} out={out_tok if out_tok is not None else '-'}"
    return (
        f"  {time_s:4.1f}s #{qi:03d} {mark} [{badge}]{cache_icon}"
        f"{tok_seg} -> '{pred}' gt:'{gt}' q:'{query}'"
    )


def _running_accuracy(candidates: dict[int, dict[str, Any]]) -> float:
    """Compute the in-flight candidate's running accuracy from its samples.
    The in-flight candidate is the last one without finalized scores."""
    in_flight = [c for c in candidates.values() if c.get("scores") is None]
    if not in_flight:
        return 0.0
    c = in_flight[-1]
    samples = c.get("samples") or []
    if not samples:
        return 0.0
    hits = sum(1 for s in samples if s.get("hit"))
    return hits / len(samples)


def _derive_best_and_streak(history: list[dict[str, Any]]) -> tuple[int | None, int]:
    """Derive ``best_round`` and ``improvement_streak`` from completed rounds.
    Returns ``(None, 0)`` on an empty history."""
    if not history:
        return None, 0
    best_idx = max(range(len(history)), key=lambda i: float(history[i].get("accuracy") or 0.0))
    best_round = int(history[best_idx].get("round", 0))
    streak = 0
    for rs in reversed(history):
        if rs.get("improved"):
            streak += 1
        else:
            break
    return best_round, streak


def _optimizer_nodes_section(campaign_dir: Path, round_idx: int) -> list[str]:
    """Compact per-node table for the round. Reads
    ``rounds/round_{NNNN:04d}.json``; empty when absent.

    The round file lists one entry per node that ran (``l1_generate``,
    ``l1_critique``, ``l1_score``, plus ``l2_refine_strategy`` /
    ``l3_modify_plan`` when escalated). This renderer is for humans —
    the raw JSON is the authoritative view.
    """
    path = campaign_dir / "rounds" / f"round_{round_idx:04d}.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    nodes = data.get("nodes") or {}
    if not nodes:
        return []
    lines = [f"Optimizer nodes (R{round_idx}):"]
    for name, block in nodes.items():
        if name == "l1_score":
            output = block.get("output") or {}
            candidates = output.get("candidates") or []
            n_samples = sum(len(c.get("samples") or []) for c in candidates)
            lines.append(f"  {name:<20} {len(candidates)} candidates · {n_samples} samples")
            continue
        dur = float(block.get("duration_s") or 0.0)
        usage = block.get("usage") or {}
        in_t = int(usage.get("prompt_tokens") or 0)
        out_t = int(usage.get("completion_tokens") or 0)
        lines.append(f"  {name:<20} {dur:6.1f}s · in={in_t} out={out_t}")
        if name in ("l2_refine_strategy", "l3_modify_plan"):
            output = block.get("output") or {}
            snippet = _compact_response(output.get("response"))
            if snippet:
                lines.append(f"    → {snippet}")
    return lines


def _compact_response(resp: Any) -> str:
    """One-line digest for an L2/L3 LLM response. Picks a headline field
    when present, otherwise trims the str cast."""
    if resp is None:
        return ""
    if isinstance(resp, dict):
        for key in ("directive", "priority_fix", "plan", "summary", "next_move"):
            v = resp.get(key)
            if isinstance(v, str) and v.strip():
                return _trim(v, 160)
        return _trim(json.dumps(resp, ensure_ascii=False), 160)
    return _trim(str(resp), 160)


def _read_critique(campaign_dir: Path, round_idx: int) -> str:
    """Read L1 critique text from ``trials/trial_NNNN.json``; ``''`` if absent."""
    path = campaign_dir / "trials" / f"trial_{round_idx:04d}.json"
    if not path.exists():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ""
    opt_sp = data.get("opt_search_point") or {}
    memory = opt_sp.get("memory") or {}
    return (memory.get("critique_text") or "").strip()


def _trim(text: str, n: int) -> str:
    t = str(text or "").replace("\n", " ").strip()
    return t if len(t) <= n else t[: n - 1] + "…"


def _fmt_elapsed(s: float | int | None) -> str:
    sec = float(s or 0)
    if sec < 60:
        return f"{sec:.1f}s"
    mi, se = divmod(int(sec), 60)
    if mi < 60:
        return f"{mi:d}m {se:d}s"
    hr, mi = divmod(mi, 60)
    return f"{hr:d}h {mi:d}m"
