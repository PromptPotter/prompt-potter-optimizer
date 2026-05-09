"""Live ledger subscriber — CLI and notebook share one ``LiveDisplay``.

Surface differentiation via constructor flag, not subclasses:
``sp_budget_ttest`` truthy → enables tqdm progress bars (CLI feel).

Single ingress: the display consumes ``CycleRecord``s from the per-cycle
``CycleEventLog`` via ``on_record``. Per-sample / per-candidate formatters
live in ``round_render``; the three round-summary renderers
(``_render_progress_table`` / ``_render_round_stats`` /
``_render_patience_status``) are private to this file because nothing
else calls them. Post-hoc reads happen by opening
``campaigns/<cycle_id>/log.md``.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.phases import CampaignPhase, PhaseEvent
from promptpotter.domain.run_records import PhaseRecord, SnapshotRecord
from promptpotter.infrastructure.projections.base import DerivedView
from promptpotter.infrastructure.projections.live_state import (
    LiveStateCore,
    apply_p_best_update,
    apply_phase,
    roll_p_best_at_round_complete,
    top_n_p_best,
)
from promptpotter.presentation.views.display import (
    DIM,
    GREEN,
    RED,
    RESET,
    YELLOW,
    _box_bottom,
    _box_bottom_info,
    _box_line,
    _box_top,
    _node_bottom,
    _node_line,
    _node_top,
)
from promptpotter.presentation.views.render_text import to_text
from promptpotter.presentation.views.round_render import (
    _fmt_query_result,
    fmt_individual_header,
)
from promptpotter.presentation.views.view_factories import individual_summary_from_dict
from promptpotter.shared.composite import (
    render_composite_fitness_block,
)
from promptpotter.shared.errors import is_error_result

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.results import RoundResult


# ===========================================================================
# Round-summary renderers — single-caller (``LiveDisplay.on_round_complete``).
# Inlined here from ``round_render`` so the module surface tracks usage.
# ===========================================================================


def _fmt_elapsed(seconds: float) -> str:
    """Render a wall-clock duration as ``Xm YYs`` or ``Xh YYm``."""
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60:02d}s"
    return f"{s // 3600}h {(s % 3600) // 60:02d}m"


def _render_progress_table(rounds: list[dict], window: int = 8) -> str:
    """Round-over-round trajectory table: accuracy, composite_fitness, rolling avg, trend, plateau.

    Items in ``rounds`` must have at minimum ``round`` and ``accuracy``.
    The ``Composite`` column is always shown so the operator never has to
    wonder whether composite_fitness was hidden because it equalled
    accuracy on every round so far.
    """
    if not rounds:
        return ""

    header = f"{'Round':<7s} {'Accuracy':>9s} {'Composite':>10s} {'Rolling Avg':>13s} {'Trend':>8s}"
    lines: list[str] = [_node_line(header)]

    accs: list[float] = []
    for rd in rounds:
        acc = rd.get("accuracy") or 0
        accs.append(acc)
        rolling = sum(accs[-window:]) / len(accs[-window:])
        if len(accs) <= 1:
            trend = "-"
        else:
            d = acc - accs[-2]
            if abs(d) < 0.001:
                trend = "+0.0%  <-- plateau"
            elif d > 0:
                trend = f"+{d:.1%}"
            else:
                trend = f"{d:.1%}"
        rl = "G" if rd.get("round") == "grid" else str(rd.get("round", "?"))
        comp = rd.get("composite_fitness") if rd.get("composite_fitness") is not None else acc
        row = f"  {rl:<5s} {acc:>8.1%} {comp:>9.4f} {rolling:>12.1%}  {trend}"
        lines.append(_node_line(row))

    if len(accs) >= 3:
        recent_avg = sum(accs[-3:]) / 3
        if all(abs(a - recent_avg) < 0.005 for a in accs[-3:]):
            lines.append(
                _node_line(
                    f"{YELLOW}-- Plateau: rolling avg stable at"
                    f" {recent_avg:.1%} for 3 rounds{RESET}"
                )
            )

    lines.append(_node_line(""))
    return "\n".join(lines)


def _render_round_stats(
    round_result: RoundResult,
    pipeline_schema: PipelineSchema | None,
) -> str:
    """hits/total, candidate count, pipeline terminations, degradation%, recall@1/5.

    Best-effort: the pipeline-stats block is wrapped in try/except and
    returns just the hits line when ``round_result.results`` is empty.
    """
    lines: list[str] = []
    hits = round_result.hits
    total = round_result.total
    deprecated = round_result.deprecated
    if total == 0 and round_result.candidate_scores:
        best = max(round_result.candidate_scores, key=lambda s: s.get("accuracy", 0))
        hits = best.get("hits", 0)
        total = best.get("total", 0)
        deprecated = best.get("deprecated", 0)
    suffix = f"  ({deprecated} deprecated)" if deprecated else ""
    lines.append(
        _node_line(
            f"hits: {hits}/{total}{suffix}  |  evaluated: "
            f"{round_result.candidates_scored} candidates"
        )
    )

    if not round_result.results:
        return "\n".join(lines)

    try:
        from collections import Counter

        from promptpotter.application.optimization.elimination import (
            get_ranked_items,
            ranked_item_keys_from_schema,
        )
        from promptpotter.application.scoring.metrics import find_rank

        ranked_item_keys = ranked_item_keys_from_schema(pipeline_schema)
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
                        get_ranked_items(r, ranked_item_keys),
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


def _render_patience_status(improved: bool, l1_stall_count: int, l1_patience: int) -> str:
    """Green tick on improvement; yellow patience counter; red stop on exhaustion."""
    if improved:
        return _node_line(f"{GREEN}✓ Improvement detected, auto-continuing...{RESET}")
    lines = [
        _node_line(f"{YELLOW}⚠ No improvement ({l1_stall_count}/{l1_patience} patience){RESET}")
    ]
    if l1_stall_count >= l1_patience:
        lines.append(
            _node_line(
                f"{RED}Stopping: patience exhausted ({l1_patience} consecutive stalls){RESET}"
            )
        )
    return "\n".join(lines)


# ===========================================================================
# LiveDisplay — RunCallbacks adapter
# ===========================================================================


class LiveDisplay(DerivedView):
    """Live ``RunCallbacks`` adapter — CLI + notebook share this one class.

    When ``sp_budget_ttest`` is provided, tqdm progress bars are rendered
    inline (CLI feel). The bar lifecycle is fused into ``LiveDisplay``
    state — no separate inner class.

    Subclasses ``DerivedView`` so the ``isinstance`` dispatch over
    ledger ``CycleRecord`` subtypes lives in one place; this class only
    overrides ``_handle_phase`` / ``_handle_snapshot``. The ``on_*``
    public methods stay because pre-cycle paths (``baseline.py``) call
    them directly without a ledger.
    """

    def __init__(
        self,
        *,
        baseline_acc: float,
        l1_patience: int,
        pipeline_schema: PipelineSchema | None,
        scoring_formula: str | None = None,
        campaign_rounds: list | None = None,
        sp_budget_ttest: int | None = None,
    ) -> None:
        # Per-cycle scalars live on a shared ``LiveStateCore`` (round number,
        # baseline + best anchors, P(best) round snapshot) so the dashboard
        # projection and this terminal renderer maintain one shape, not two.
        self._core = LiveStateCore(baseline_acc=baseline_acc)
        self.l1_patience = l1_patience
        self.pipeline_schema = pipeline_schema
        self.scoring_formula = scoring_formula
        self.campaign_rounds = campaign_rounds if campaign_rounds is not None else []
        self.initial_len = len(self.campaign_rounds)
        self.sample_counter = 0
        # Composite-render context — read by ``on_candidate_scored`` for
        # the per-candidate baseline anchor and by ``on_round_complete``
        # for the 3-line composite_fitness block. Populated from
        # L1_SCORE:exit views and mutated on ``scoring_steer:applied``.
        # ``RunCallbacks`` wires its shared ctx onto ``self._phase_ctx``
        # after construction so the display sees the same dict the
        # phase-view builder writes to.
        self._phase_ctx: dict = {}
        # Mid-round running leader — updated after each
        # ``on_candidate_scored`` so the operator sees a one-line
        # scoreboard between candidates instead of waiting for the
        # round-summary box past 100+ query lines. Reset on
        # ``L1_GENERATE:enter``.
        self._round_best_acc: float | None = None
        self._round_best_label: str | None = None
        # Wall-clock anchor — set on ``L1_GENERATE:enter`` so the
        # round-summary block can report elapsed seconds. ``None`` means
        # "no measurement yet" (resume points, pre-baseline) — render
        # falls back to omitting the elapsed field.
        self._round_started_at: float | None = None
        # tqdm bar lifecycle (CLI surface). When ``sp_budget_ttest`` is
        # None the bars are skipped entirely; ``_write`` falls back to
        # plain ``print`` so the notebook surface stays stdout-clean.
        self._bar_budget = sp_budget_ttest
        self._bar: Any = None
        self._bar_cand_idx: int = -1
        self._in_baseline: bool = False

    # --- tqdm bar helpers (active only when ``_bar_budget`` is set) -----

    def _bars_close(self) -> None:
        if self._bar is not None:
            self._bar.close()
            self._bar = None
        self._bar_cand_idx = -1

    def _write(self, line: str) -> None:
        if self._bar_budget is not None:
            from tqdm.auto import tqdm

            tqdm.write(line)
        else:
            print(line, flush=True)

    @property
    def baseline_acc(self) -> float:
        """Running baseline anchor — read-only mirror of the shared core."""
        return self._core.baseline_acc

    def set_baseline(self, fresh: float) -> None:
        """Post-baseline rewire — replace pre-baseline placeholder."""
        self._core.baseline_acc = fresh
        if fresh > self._core.best_acc:
            self._core.best_acc = fresh

    # --- Ledger subscription (via DerivedView) ---------------------

    def _handle_phase(self, record: PhaseRecord) -> None:
        payload = record.payload or {}
        if record.phase == "round" and record.event == "display":
            round_result = payload.get("round_result")
            if round_result is not None:
                # Re-sync phase ctx from listener-side snapshot so the
                # composite_fitness block reads the same baseline anchors
                # the listener saw at emit time.
                ctx = payload.get("phase_ctx")
                if isinstance(ctx, dict):
                    self._phase_ctx.update(ctx)
                self.on_round_complete(round_result, int(payload.get("l1_stall_count") or 0))
            return
        self.on_phase(
            PhaseEvent(
                phase=record.phase,
                event=record.event,
                round=record.round,
                data=payload.get("data") or {},
            ),
            payload.get("view"),
        )

    def _handle_snapshot(self, record: SnapshotRecord) -> None:
        payload = record.payload or {}
        ci = int(record.candidate_idx or 0)
        ct = int(record.candidate_total or 0)
        qi = int(record.sample_idx or 0)
        qt = int(record.sample_total or 0)
        ev = record.event
        if ev == "sample_started":
            self.on_sample_started(ci, ct, qi, qt, payload.get("query_text") or "")
        elif ev == "sample_scored":
            self.on_sample_scored(ci, ct, qi, qt, payload.get("result") or {})
        elif ev == "candidate_started":
            self.on_candidate_started(
                ci, ct, payload.get("changes_description") or "", payload.get("pp_override")
            )
        elif ev == "candidate_scored":
            ctx = payload.get("phase_ctx")
            if isinstance(ctx, dict):
                self._phase_ctx.update(ctx)
            self.on_candidate_scored(ci, ct, payload.get("scores") or {})
        elif ev == "p_best_update":
            self.on_p_best_update(
                str(payload.get("current_id") or ""),
                int(payload.get("n_samples") or 0),
                {str(k): float(v) for k, v in (payload.get("p_best") or {}).items()},
            )

    # --- Public callback API ------------------------------------------
    #
    # These methods are the direct entry point for callers that don't
    # route through a ledger (notably ``baseline.py``, which fires before
    # the per-cycle ledger exists). The ``on_record`` dispatcher above
    # forwards ledger-driven events into the same handlers.

    def on_phase(self, event: PhaseEvent, view: dict | None = None) -> None:
        # Bar lifecycle inline — close on phase-exit boundaries; mark
        # baseline phase so ``on_sample_started`` opens the right bar.
        if self._bar_budget is not None:
            if event.event == "exit":
                if event.phase == CampaignPhase.BASELINE:
                    self._bars_close()
                    self._in_baseline = False
                elif event.phase == CampaignPhase.L1_SCORE:
                    self._bars_close()
            elif event.event == "enter" and event.phase == CampaignPhase.BASELINE:
                self._in_baseline = True

        if event.phase == CampaignPhase.L1_SCORE and event.event == "enter":
            self._write("\n" + _node_top("SCORE"))
        if view is not None:
            from promptpotter.presentation.views.view_factories import view_from_record

            record = {
                "phase": event.phase,
                "event": event.event,
                "round": event.round,
                "view": view,
            }
            typed = view_from_record(record)
            if typed is not None and (rendered := to_text(typed)):
                self._write(rendered)
        # Round number + baseline/best anchors flow through the shared core
        # (INIT:exit carries the post-baseline accuracy; L1_SCORE:exit on
        # ``improved`` promotes the new winner). ``view=None`` still tracks
        # the round number.
        apply_phase(self._core, event, view)
        if event.phase == CampaignPhase.L1_GENERATE and event.event == "enter":
            self._round_best_acc = None
            self._round_best_label = None
            self._round_started_at = time.monotonic()
        if event.phase == CampaignPhase.ESCALATION and event.event == "exit":
            self.sample_counter = 0
        if (
            event.phase == CampaignPhase.INIT
            and event.event == "exit"
            and event.data["env"].state.resumed_from_round > 0
        ):
            del self.campaign_rounds[self.initial_len :]
        # Mirror an interactive-steer formula swap onto the shared phase
        # ctx so the next round's renderers print the new formula.
        if event.phase == "scoring_steer" and event.event == "applied":
            new_formula = event.data.get("formula")
            if new_formula:
                self._phase_ctx["composite_fitness_formula"] = new_formula

    def on_sample_started(
        self, cand_idx: int, n_cands: int, sample_idx: int, n_samples: int, query_text: str
    ) -> None:
        # Per-sample output renders after the result lands; the emitter's
        # dashboard.json surfaces the in-flight state.
        if self._bar_budget is None:
            return
        from tqdm.auto import tqdm

        if self._in_baseline:
            if self._bar is None:
                self._bar = tqdm(
                    total=n_samples or 1, desc="  baseline", unit="q", leave=False, ncols=60
                )
            return
        if cand_idx != self._bar_cand_idx:
            self._bars_close()
            self._bar_cand_idx = cand_idx
            # Bar tops out at sp_budget_ttest; early t-test elimination
            # leaves it partially filled — which is the signal, not a bug.
            self._bar = tqdm(
                total=self._bar_budget,
                desc=f"  cand {cand_idx + 1}/{n_cands}",
                unit="q",
                leave=False,
                ncols=60,
            )

    def on_sample_scored(
        self, cand_idx: int, n_cands: int, sample_idx: int, n_samples: int, result: dict
    ) -> None:
        self.sample_counter += 1
        prefix = f"  [{self.sample_counter:>3d}] "
        self._write(
            _fmt_query_result(
                result,
                cached=bool(result.get("cached", False)),
                prefix=prefix,
                scoring_formula=self.scoring_formula,
            )
        )
        if self._bar is not None:
            self._bar.update(1)

    def on_p_best_update(self, current_id: str, n_samples: int, p_best: dict[str, float]) -> None:
        """Stash latest Posterior-of-Being-Best snapshot for the round-end roll-up.

        Per-sample mid-round detail lives in ``dashboard.json``; the
        terminal sees one consolidated p_best line in the round-summary
        box (rendered by ``_render_p_best_line``).
        """
        apply_p_best_update(self._core, current_id, n_samples, p_best)

    def _render_p_best_line(self) -> str | None:
        """Top-5 P(best) snapshot with cross-round arrow glyphs (▲/▼).

        Returns ``None`` when no p_best has been seen this round (e.g.
        single-candidate rounds skip the t-test). Arrows compare against
        the prior round's final snapshot.
        """
        if not self._core.current_p_best:
            return None
        last = self._core.last_p_best
        parts: list[str] = []
        for cid, prob in top_n_p_best(self._core.current_p_best):
            prev = last.get(cid)
            arrow = ""
            if prev is not None:
                if prob > prev + 1e-4:
                    arrow = "▲"
                elif prob < prev - 1e-4:
                    arrow = "▼"
            tag = f"*{cid[:6]}*" if cid == self._core.current_p_best_id else cid[:6]
            parts.append(f"{tag} {prob * 100:4.1f}%{arrow}")
        return f"P(best) @ q{self._core.current_p_best_n}: " + " | ".join(parts)

    def on_candidate_started(
        self,
        idx: int,
        total: int,
        changes_description: str,
        pp_override: dict | None,
    ) -> None:
        # Close any still-open bar (e.g. final query of prior candidate) so
        # the header lands above the fresh bar.
        self._bars_close()
        self._write(fmt_individual_header(idx, total, changes_description, pp_override))

    def on_candidate_scored(self, idx: int, total: int, scores: dict) -> None:
        self._bars_close()
        w = 66
        label = f"C{idx + 1}"
        baseline_acc = self._phase_ctx.get("baseline_accuracy", self.baseline_acc)
        baseline_comp = self._phase_ctx.get("baseline_composite_fitness")
        summary = individual_summary_from_dict(
            scores, baseline_acc, baseline_composite_fitness=baseline_comp
        )

        self._write(f"  {_box_top(f'{label}/{total}', summary.tag, width=w)}")
        if summary.body_line:
            self._write(f"  {_box_line(summary.body_line, width=w)}")
        for line in summary.detail_lines[:-1]:
            self._write(f"  {_box_line(line, width=w)}")
        if summary.detail_lines:
            self._write(f"  {_box_bottom_info(summary.detail_lines[-1], width=w)}")
        else:
            self._write(f"  {_box_bottom(width=w)}")

        # Mid-round leader scoreboard — one tight line so the operator can read
        # "is anything beating baseline yet" without scrolling through 100+
        # query lines to find the round-summary box. Skip invalid candidates
        # (no comparable accuracy).
        if summary.status != "invalid":
            acc = scores.get("accuracy")
            if isinstance(acc, int | float):
                self._write(self._fmt_round_leader(label, float(acc), baseline_acc))

    def _fmt_round_leader(self, label: str, acc: float, baseline_acc: float) -> str:
        """One-liner scoreboard: ``★ leader`` on a new best, ``→`` else."""
        delta_base = acc - baseline_acc
        if self._round_best_acc is None or acc > self._round_best_acc:
            self._round_best_acc = acc
            self._round_best_label = label
            sign = "+" if delta_base >= 0 else ""
            return (
                f"  {GREEN}★ leader: {label} {acc:.1%}  (Δ {sign}{delta_base:.1%} vs base){RESET}"
            )
        gap = acc - (self._round_best_acc or acc)
        prior = self._round_best_label or "leader"
        return f"  {DIM}→ {label} {acc:.1%}  ({gap:.1%} from {prior}){RESET}"

    def on_round_complete(self, round_result: RoundResult, l1_stall_count: int) -> None:
        self._bars_close()
        self.sample_counter = 0

        self.campaign_rounds.append(
            {
                "round": len(self.campaign_rounds),
                "label": round_result.label,
                "accuracy": round_result.accuracy,
                "composite_fitness": round_result.composite_fitness,
                "hits": round_result.hits,
                "total": round_result.total,
                "improved": round_result.improved,
                "prompt_fields": OptSearchPoint.from_prompt_fields(round_result.prompt_fields),
                "results": round_result.results,
                "candidates_scored": round_result.candidates_scored,
                "candidate_scores": list(round_result.candidate_scores),
            }
        )

        rn = self._core.round_num + 1
        elapsed_label = ""
        if self._round_started_at is not None:
            elapsed = time.monotonic() - self._round_started_at
            elapsed_label = f" — {_fmt_elapsed(elapsed)}"
        self._round_started_at = None
        self._write("")
        self._write(_node_top(f"ROUND {rn} SUMMARY{elapsed_label}"))
        for line in _render_progress_table(self.campaign_rounds).split("\n"):
            self._write(line)
        if (p_best_line := self._render_p_best_line()) is not None:
            self._write(_node_line(p_best_line))
        # Roll p_best snapshot into the cross-round baseline so next
        # round's arrows compare against this round's final.
        roll_p_best_at_round_complete(self._core)
        # Composite block — full mode only. 3-line render: composite_fitness +
        # baseline anchor (line 1), abbreviated formula (line 2), short-
        # name evaluator values (line 3). Anchored to the campaign
        # baseline so operators see how far the run came from origin.
        # Short formula is None for custom user formulas — fall back to
        # full text and accept the wrap.
        formula_short = self._phase_ctx.get("composite_fitness_formula_short")
        formula_full = self._phase_ctx.get("composite_fitness_formula")
        if formula_short or formula_full:
            for line in render_composite_fitness_block(
                round_result.composite_fitness,
                dict(round_result.evaluators),
                formula_short or formula_full,
                baseline=self._phase_ctx.get("baseline_composite_fitness"),
                use_short_names=bool(formula_short),
            ):
                self._write(_node_line(line))
        if stats := _render_round_stats(round_result, self.pipeline_schema):
            for line in stats.split("\n"):
                if line:
                    self._write(line)
        for line in _render_patience_status(
            round_result.improved, l1_stall_count, self.l1_patience
        ).split("\n"):
            self._write(line)
        self._write(_node_bottom())
