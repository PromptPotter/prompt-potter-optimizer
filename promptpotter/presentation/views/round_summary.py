"""Round-summary renderers — shared between CLI and notebook displays.

Functions return multi-line strings; callers route through ``print()`` or
``tqdm.write()`` depending on the entry point. Splits out from
``display_primitives`` the ~200 lines that compose the round-boundary
report block (progress trajectory, round stats, patience status).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from promptpotter.shared.errors import is_error_result

from .display_primitives import GREEN, RED, RESET, YELLOW, _node_line

if TYPE_CHECKING:
    from promptpotter.application.optimization.results import RoundResult
    from promptpotter.domain.pipeline_schema import PipelineSchema


def render_progress_table(campaign_rounds: list[dict]) -> str:
    """Round-over-round trajectory table: accuracy, composite, rolling avg, trend, plateau.

    ``campaign_rounds`` items must have at minimum ``round``, ``accuracy``.
    ``composite`` is optional; column appears only when it differs from
    accuracy in at least one round.
    """
    lines: list[str] = []
    _accs: list[float] = []
    has_comp = any(
        rd.get("composite") is not None and rd.get("composite") != rd["accuracy"]
        for rd in campaign_rounds
    )
    if has_comp:
        lines.append(
            _node_line(
                f"{'Round':<7s} {'Accuracy':>9s} {'Composite':>10s}"
                f" {'Rolling Avg':>13s} {'Trend':>8s}"
            )
        )
    else:
        lines.append(
            _node_line(f"{'Round':<7s} {'Accuracy':>9s} {'Rolling Avg':>13s} {'Trend':>8s}")
        )

    for rd in campaign_rounds:
        acc = rd["accuracy"]
        _accs.append(acc)
        window_slice = _accs[-8:]
        rolling = sum(window_slice) / len(window_slice)
        if len(_accs) <= 1:
            trend = "-"
        else:
            d = acc - _accs[-2]
            if abs(d) < 0.001:
                trend = "+0.0%  <-- plateau"
            elif d > 0:
                trend = f"+{d:.1%}"
            else:
                trend = f"{d:.1%}"
        rl = "G" if rd.get("round") == "grid" else str(rd["round"])
        if has_comp:
            comp = rd.get("composite", acc)
            lines.append(
                _node_line(f"  {rl:<5s} {acc:>8.1%} {comp:>9.4f} {rolling:>12.1%}  {trend}")
            )
        else:
            lines.append(_node_line(f"  {rl:<5s} {acc:>8.1%} {rolling:>12.1%}  {trend}"))

    if len(_accs) >= 3:
        recent = _accs[-3:]
        recent_avg = sum(recent) / len(recent)
        if all(abs(a - recent_avg) < 0.005 for a in recent):
            lines.append(
                _node_line(
                    f"{YELLOW}-- Plateau: rolling avg stable at"
                    f" {recent_avg:.1%} for 3 rounds{RESET}"
                )
            )

    lines.append(_node_line(""))
    return "\n".join(lines)


def render_round_stats(
    round_result: RoundResult,
    pipeline_schema: PipelineSchema | None,
) -> str:
    """hits/total, candidate count, pipeline terminations, degradation%, recall@1/5.

    Best-effort: the pipeline-stats block is wrapped in try/except and
    returns an empty string when ``round_result.results`` is empty.
    """
    lines: list[str] = []
    hits = round_result.hits
    total = round_result.total
    if total == 0 and round_result.candidate_scores:
        best = max(round_result.candidate_scores, key=lambda s: s.get("accuracy", 0))
        hits = best.get("hits", 0)
        total = best.get("total", 0)
    lines.append(
        _node_line(
            f"hits: {hits}/{total}  |  evaluated: {round_result.candidates_scored} candidates"
        )
    )

    if not round_result.results:
        return "\n".join(lines)

    try:
        from collections import Counter

        from promptpotter.application.optimization.utils import (
            candidate_keys_from_schema,
            get_candidates,
        )
        from promptpotter.application.scoring.metrics import find_rank

        candidate_keys = candidate_keys_from_schema(pipeline_schema)
        results = round_result.results
        n_results = len(results)
        terminations: Counter[str] = Counter()
        degraded = 0
        for r in results:
            pd = r.get("pipeline_data") or {}
            terminations[pd.get("terminated_at", "unknown")] += 1
            if (pd.get("diagnostics") or {}).get("warnings"):
                degraded += 1

        if terminations:
            lines.append(
                _node_line(
                    f"Pipeline: {' | '.join(f'{k}:{v}' for k, v in terminations.most_common())}"
                )
            )
        if degraded > 0:
            lines.append(_node_line(f"Degradation: {degraded / n_results:.0%}"))

        valid = [r for r in results if not is_error_result(r)]
        if valid:

            def recall_at_k(k: int) -> float:
                hit_count = 0
                for r in valid:
                    rank = find_rank(
                        get_candidates(r, candidate_keys),
                        r.get("ground_truth", ""),
                    )
                    if rank is not None and rank <= k:
                        hit_count += 1
                return hit_count / len(valid)

            lines.append(
                _node_line(f"Recall: top-1={recall_at_k(1):.0%} top-5={recall_at_k(5):.0%}")
            )
    except Exception:
        pass

    return "\n".join(lines)


def render_patience_status(
    improved: bool,
    l1_stall_count: int,
    l1_patience: int,
) -> str:
    """Green tick on improvement; yellow patience counter; red stop on exhaustion."""
    lines: list[str] = []
    if improved:
        lines.append(_node_line(f"{GREEN}✓ Improvement detected, auto-continuing...{RESET}"))
        return "\n".join(lines)
    lines.append(
        _node_line(f"{YELLOW}⚠ No improvement ({l1_stall_count}/{l1_patience} patience){RESET}")
    )
    if l1_stall_count >= l1_patience:
        lines.append(
            _node_line(
                f"{RED}Stopping: patience exhausted ({l1_patience} consecutive stalls){RESET}"
            )
        )
    return "\n".join(lines)
