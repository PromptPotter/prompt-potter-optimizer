"""Notebook display callbacks for the optimization loop.

Extracted from ``optimize.py`` — owns all notebook-side rendering of
per-phase, per-query, per-candidate, and per-round events. Plain class
so state is explicit and each callback is testable in isolation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from promptpotter.application.optimization.phases import CampaignPhase, PhaseEvent
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.shared.errors import is_error_result

from .display import (
    GREEN,
    RED,
    RESET,
    YELLOW,
    _box_bottom,
    _box_bottom_info,
    _box_line,
    _box_top,
    _fmt_delta,
    _fmt_query_result,
    fmt_ci,
)
from .phase_display import (
    _CycleDisplayState,
    _dispatch_phase,
    _node_bottom,
    _node_line,
    _node_top,
    _pp_val,
)

if TYPE_CHECKING:
    from promptpotter.application.campaign.callbacks import RunCallbacks
    from promptpotter.application.optimization.results import RoundResult
    from promptpotter.application.recon.recon_report import ReconBrief
    from promptpotter.domain.pipeline_schema import PipelineSchema

CYAN = "\033[36m"

__all__ = ["NotebookDisplay"]


class NotebookDisplay:
    """Callback bundle for notebook optimization runs."""

    def __init__(
        self,
        *,
        campaign_rounds: list,
        baseline_acc: float,
        l1_patience: int,
        pipeline_schema: PipelineSchema | None,
        recon_brief: ReconBrief | None = None,
    ) -> None:
        self.campaign_rounds = campaign_rounds
        self.baseline_acc = baseline_acc
        self.l1_patience = l1_patience
        self.pipeline_schema = pipeline_schema
        self.initial_len = len(campaign_rounds)
        self.state = _CycleDisplayState(baseline_accuracy=baseline_acc)
        self.state.recon_brief = recon_brief
        self.query_counter = 0

    def as_callbacks(self) -> RunCallbacks:
        from promptpotter.application.campaign.callbacks import RunCallbacks

        return RunCallbacks(
            on_round_complete=self.on_round,
            on_candidate_scored=self.on_candidate,
            on_sample_scored=self.on_query,
            on_phase=self.on_phase,
        )

    def on_phase(self, event: PhaseEvent) -> None:
        _dispatch_phase(event, self.state)
        # Reset query counter on escalation exit (on_round is skipped)
        if event.phase == CampaignPhase.ESCALATION and event.event == "exit":
            self.query_counter = 0
        # On resume: clear stale optimization rounds before replay re-appends them
        if (
            event.phase == CampaignPhase.INIT
            and event.event == "exit"
            and event.data["env"].resumed_from_round > 0
        ):
            del self.campaign_rounds[self.initial_len :]

    def on_query(
        self, cand_idx: int, n_cands: int, query_idx: int, n_queries: int, result: dict
    ) -> None:
        self.query_counter += 1
        is_cached = result.get("cached", False)
        prefix = f"  [{self.query_counter:>3d}] "
        print(_fmt_query_result(result, cached=is_cached, prefix=prefix), flush=True)

    def on_candidate(self, idx: int, total: int, scores: dict) -> None:
        label = f"C{idx + 1}"
        w = 66

        acc = scores["accuracy"]
        hits = scores.get("hits", 0)
        n = scores.get("total", 0)
        comp = scores.get("composite")
        from promptpotter.application.recon.failure_groups import wilson_ci

        ci_lo, ci_hi = wilson_ci(hits, n)
        delta = acc - self.state.baseline_accuracy

        # Line 1 (top frame): label + accuracy with CI
        acc_tag = f"{acc:.1%} {fmt_ci(ci_lo, ci_hi)}"
        print(f"  {_box_top(f'{label}/{total}', acc_tag, width=w)}")

        # Line 2 (content): cyan mutations + hits + vs baseline
        meta: dict[str, Any] = {}
        if idx < len(self.state.candidates_meta):
            meta = self.state.candidates_meta[idx]
        pp = meta.get("pipeline_params_override")
        parts: list[str] = []
        if pp:
            for node, val in pp.items():
                if isinstance(val, dict):
                    for k, v in val.items():
                        parts.append(f"{node}.{k}: {_pp_val(v)}")
                else:
                    parts.append(f"{node}: {_pp_val(val)}")
        mutations = f"{CYAN}{'  '.join(parts)}{RESET}  " if parts else ""
        if scores.get("escalation_aborted"):
            scored_q = scores.get("scored_queries", n)
            expected_q = scores.get("expected_queries", n)
            hit_str = f"{hits}/{scored_q} hits {YELLOW}⚠ aborted {scored_q}/{expected_q}{RESET}"
        else:
            hit_str = f"{hits}/{n} hits"
        content = f"{mutations}{hit_str}  vs baseline: {_fmt_delta(delta)}"
        print(f"  {_box_line(content, width=w)}")

        # Line 3 (bottom frame): composite + degraded
        bottom_parts: list[str] = []
        if comp is not None and comp != acc:
            bottom_parts.append(f"composite={comp:.4f}")
        degraded = scores.get("degraded_queries", 0)
        if degraded:
            bottom_parts.append(f"{YELLOW}\u26a0 {degraded}/{n} degraded{RESET}")
        if bottom_parts:
            print(f"  {_box_bottom_info('  '.join(bottom_parts), width=w)}")
        else:
            print(f"  {_box_bottom(width=w)}")

    def on_round(self, round_result: RoundResult, stall_count: int) -> None:
        self.query_counter = 0
        self.state.stall_count = stall_count

        self.campaign_rounds.append(
            {
                "round": len(self.campaign_rounds),
                "label": round_result.label,
                "accuracy": round_result.accuracy,
                "composite": round_result.composite,
                "hits": round_result.hits,
                "total": round_result.total,
                "improved": round_result.improved,
                "prompt_fields": OptSearchPoint.from_prompt_fields(round_result.prompt_fields),
                "results": round_result.results,
                "candidates_scored": round_result.candidates_scored,
                "candidate_scores": list(round_result.candidate_scores),
            }
        )

        rn = self.state.round_num + 1

        print()
        print(_node_top(f"ROUND {rn} SUMMARY"))

        self._print_progress_table()
        self._print_round_stats(round_result)
        self._print_patience_status(round_result, stall_count)

        print(_node_bottom())

    def _print_progress_table(self) -> None:
        _accs: list[float] = []
        has_comp = any(
            rd.get("composite") is not None and rd.get("composite") != rd["accuracy"]
            for rd in self.campaign_rounds
        )
        if has_comp:
            print(
                _node_line(
                    f"{'Round':<7s} {'Accuracy':>9s} {'Composite':>10s}"
                    f" {'Rolling Avg':>13s} {'Trend':>8s}"
                )
            )
        else:
            print(_node_line(f"{'Round':<7s} {'Accuracy':>9s} {'Rolling Avg':>13s} {'Trend':>8s}"))

        for rd in self.campaign_rounds:
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
                print(_node_line(f"  {rl:<5s} {acc:>8.1%} {comp:>9.4f} {rolling:>12.1%}  {trend}"))
            else:
                print(_node_line(f"  {rl:<5s} {acc:>8.1%} {rolling:>12.1%}  {trend}"))

        if len(_accs) >= 3:
            recent = _accs[-3:]
            recent_avg = sum(recent) / len(recent)
            if all(abs(a - recent_avg) < 0.005 for a in recent):
                print(
                    _node_line(
                        f"{YELLOW}-- Plateau: rolling avg stable at"
                        f" {recent_avg:.1%} for 3 rounds{RESET}"
                    )
                )

        print(_node_line(""))

    def _print_round_stats(self, round_result: RoundResult) -> None:
        hits = round_result.hits
        total = round_result.total
        if total == 0 and round_result.candidate_scores:
            best = max(round_result.candidate_scores, key=lambda s: s.get("accuracy", 0))
            hits = best.get("hits", 0)
            total = best.get("total", 0)
        print(
            _node_line(
                f"hits: {hits}/{total}  |  evaluated: {round_result.candidates_scored} candidates"
            )
        )

        if not round_result.results:
            return

        try:
            from collections import Counter

            from promptpotter.application.optimization.nodes.critique import (
                candidate_keys_from_schema,
                get_candidates,
            )
            from promptpotter.application.scoring.metrics import find_rank

            candidate_keys = candidate_keys_from_schema(self.pipeline_schema)
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
                print(
                    _node_line(
                        f"Pipeline: {' | '.join(f'{k}:{v}' for k, v in terminations.most_common())}"
                    )
                )
            if degraded > 0:
                print(_node_line(f"Degradation: {degraded / n_results:.0%}"))

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

                print(_node_line(f"Recall: top-1={recall_at_k(1):.0%} top-5={recall_at_k(5):.0%}"))
        except Exception:
            pass  # stats are best-effort

    def _print_patience_status(self, round_result: RoundResult, stall_count: int) -> None:
        if round_result.improved:
            print(_node_line(f"{GREEN}✓ Improvement detected, auto-continuing...{RESET}"))
            return
        print(
            _node_line(
                f"{YELLOW}⚠ No improvement ({stall_count}/{self.l1_patience} patience){RESET}"
            )
        )
        if stall_count >= self.l1_patience:
            print(
                _node_line(
                    f"{RED}Stopping: patience exhausted"
                    f" ({self.l1_patience} consecutive stalls){RESET}"
                )
            )
