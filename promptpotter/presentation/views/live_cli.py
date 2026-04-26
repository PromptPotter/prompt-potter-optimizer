"""CLI live display — informative terminal output during ``optimize``.

Reuses the notebook's shared primitives (``_fmt_query_result``,
``_scoreboard``, ``_fmt_delta``, ANSI colors) and adds a ``tqdm``
progress bar per candidate during scoring plus one for the baseline
phase. Non-sampling phases (generate, refine, modify-plan) emit a
single header line each.

All per-query lines go through ``tqdm.write`` so they don't trample
an active progress bar.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tqdm.auto import tqdm

from promptpotter.domain.phases import CampaignPhase
from promptpotter.presentation.views.candidate_view import (
    build_individual_summary,
    fmt_individual_header,
)
from promptpotter.presentation.views.display_primitives import (
    BOLD,
    GREEN,
    RESET,
    YELLOW,
    _fmt_delta,
    _node_bottom,
    _node_top,
    _scoreboard,
)
from promptpotter.presentation.views.formatting import fmt_pct
from promptpotter.presentation.views.query_format import _fmt_query_result
from promptpotter.presentation.views.round_summary import (
    render_patience_status,
    render_progress_table,
    render_round_stats,
)

if TYPE_CHECKING:
    from promptpotter.application.optimization.results import RoundResult
    from promptpotter.domain.phases import PhaseEvent
    from promptpotter.domain.pipeline_schema import PipelineSchema


_PHASE_HEADERS: dict[str, str] = {
    CampaignPhase.L1_GENERATE: "generate",
    CampaignPhase.REFINE_STRATEGY: "refine-context",
    CampaignPhase.MODIFY_PLAN: "modify-plan",
    CampaignPhase.ESCALATION: "escalation",
    CampaignPhase.BACKEND_WARNING: "backend-warning",
}


class CliDisplay:
    """Terminal listener for ``optimize`` — tqdm bars + reused primitives.

    Per-phase signal:

    - ``baseline``: tqdm bar over queries + per-query HIT/MISS via
      ``_fmt_query_result``.
    - ``generate`` / ``refine-context`` / ``modify-plan``: one header
      line (no bar — these are single LLM calls).
    - ``evaluate``: one tqdm bar per candidate (desc ``cand k/N``),
      per-query lines, then a terse scoreboard on phase exit (reuses
      ``_scoreboard`` when ≥4 candidates, inline ``C1=… | C2=…`` for 1-3).
    - ``on_round_complete``: a rolling-best + patience line.
    """

    def __init__(
        self,
        *,
        baseline_acc: float,
        max_rounds: int,
        l1_patience: int,
        sp_budget_ttest: int,
        scoring_formula: str | None = None,
        pipeline_schema: PipelineSchema | None = None,
    ) -> None:
        self.baseline_acc = baseline_acc  # rolling — bumped on improvement
        self.original_baseline = baseline_acc
        self.max_rounds = max_rounds
        self.l1_patience = l1_patience
        self.sp_budget_ttest = sp_budget_ttest  # per-SP cap; early-stop fills < cap
        self.scoring_formula = scoring_formula
        self._pipeline_schema = pipeline_schema
        self.current_round: int = 0
        self.best_acc: float = baseline_acc
        self._pbar: Any = None
        self._current_cand_idx: int = -1
        self._in_baseline: bool = False
        self._round_records: list[dict] = []

    def set_baseline(self, fresh: float) -> None:
        """Post-baseline rewire — replace the pre-baseline placeholder.

        Called after BASELINE produces a real accuracy; the display was
        constructed earlier (so per-query output reaches the terminal).
        ``baseline_acc`` and ``original_baseline`` take the fresh value;
        ``best_acc`` advances only if it's behind (never demote).
        """
        self.baseline_acc = fresh
        self.original_baseline = fresh
        if self.best_acc < fresh:
            self.best_acc = fresh

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _close_pbar(self) -> None:
        if self._pbar is not None:
            self._pbar.close()
            self._pbar = None
        self._current_cand_idx = -1

    @staticmethod
    def _write(line: str) -> None:
        # tqdm.write plays nicely with an active progress bar; also works
        # when no bar is active (falls back to stdout).
        tqdm.write(line)

    # ------------------------------------------------------------------
    # RunListener display protocol
    # ------------------------------------------------------------------

    def on_phase(self, event: PhaseEvent) -> None:
        if event.event == "exit":
            if event.phase == CampaignPhase.BASELINE:
                self._close_pbar()
                self._in_baseline = False
            elif event.phase == CampaignPhase.L1_SCORE:
                self._close_pbar()
                self._print_scoreboard(event.data)
            return

        # enter events
        phase = event.phase
        if phase == CampaignPhase.INIT:
            return  # baseline-enter is the real kickoff

        rn = event.round if event.round is not None else self.current_round

        if phase == CampaignPhase.BASELINE:
            self._in_baseline = True
            self._write("→ baseline")
            return

        self.current_round = rn or self.current_round

        if phase == CampaignPhase.L1_GENERATE:
            n_variants = event.data.get("n_variants") or event.data.get("n")
            suffix = f"  (n={n_variants})" if n_variants else ""
            self._write(f"→ R{self.current_round}/{self.max_rounds}  generate{suffix}")
            return

        if phase == CampaignPhase.L1_SCORE:
            n_cand = event.data.get("n_candidates", 0)
            n_q = event.data.get("n_queries", 0)
            calls = n_cand * n_q
            self._write(
                f"→ R{self.current_round}/{self.max_rounds}  evaluate  "
                f"({n_cand} \u00d7 {n_q} = {calls} calls)"
            )
            return

        header = _PHASE_HEADERS.get(phase, phase)
        self._write(f"→ R{self.current_round}/{self.max_rounds}  {header}")

    def on_candidate_started(
        self,
        idx: int,
        total: int,
        changes_description: str,
        pp_override: dict | None,
    ) -> None:
        # Close any still-open bar (e.g. final query of prior candidate) so
        # the header lands above the fresh bar.
        self._close_pbar()
        self._write(fmt_individual_header(idx, total, changes_description, pp_override))

    def on_sample_started(self, ci: int, ct: int, qi: int, qt: int, query_text: str) -> None:
        if self._in_baseline:
            if self._pbar is None:
                self._pbar = tqdm(
                    total=qt or 1,
                    desc="  baseline",
                    unit="q",
                    leave=False,
                    ncols=60,
                )
            return
        if ci != self._current_cand_idx:
            self._close_pbar()
            self._current_cand_idx = ci
            # Bar tops out at sp_budget_ttest; early t-test elimination leaves
            # it partially filled — which is the signal, not a bug.
            self._pbar = tqdm(
                total=self.sp_budget_ttest,
                desc=f"  cand {ci + 1}/{ct}",
                unit="q",
                leave=False,
                ncols=60,
            )

    def on_sample_scored(self, ci: int, ct: int, qi: int, qt: int, result: dict) -> None:
        is_cached = result.get("cached", False)
        self._write(
            _fmt_query_result(
                result,
                cached=is_cached,
                prefix="    ",
                scoring_formula=self.scoring_formula,
            )
        )
        if self._pbar is not None:
            self._pbar.update(1)

    def on_candidate_scored(self, idx: int, total: int, scores: dict) -> None:
        self._close_pbar()
        summary = build_individual_summary(scores, self.baseline_acc)
        # Flat CLI layout: label+tag on one line; body_line indented under it
        # when non-empty (skipped on invalid); detail lines follow.
        self._write(f"  cand {idx + 1}/{total}  {summary.tag}")
        if summary.body_line:
            self._write(f"    {summary.body_line}")
        for line in summary.detail_lines:
            self._write(f"    {line}")

    def on_round_complete(self, rr: RoundResult, l1_stall_count: int) -> None:
        self._close_pbar()
        if rr.accuracy > self.best_acc:
            self.best_acc = rr.accuracy
        self._round_records.append(
            {
                "round": rr.round,
                "label": rr.label,
                "accuracy": rr.accuracy,
                "composite": rr.composite,
                "improved": rr.improved,
            }
        )
        self._write(_node_top(f"ROUND {rr.round} SUMMARY"))
        for line in render_progress_table(self._round_records).split("\n"):
            self._write(line)
        stats = render_round_stats(rr, self._pipeline_schema)
        if stats:
            for line in stats.split("\n"):
                if line:
                    self._write(line)
        for line in render_patience_status(rr.improved, l1_stall_count, self.l1_patience).split(
            "\n"
        ):
            self._write(line)
        self._write(_node_bottom())

    # ------------------------------------------------------------------
    # Scoreboard (L1_SCORE:exit)
    # ------------------------------------------------------------------

    def _print_scoreboard(self, d: dict) -> None:
        scores = d.get("candidate_scores", [])
        if not scores:
            return
        for i, s in enumerate(scores):
            s.setdefault("label", f"C{i + 1}")
        non_aborted = [s for s in scores if not s.get("escalation_aborted")]
        best_s = (
            max(
                non_aborted,
                key=lambda s: (s.get("composite", s["accuracy"]), s["accuracy"]),
            )
            if non_aborted
            else {}
        )
        winner = best_s.get("label", "?")

        if len(scores) > 3:
            board = _scoreboard(scores, winner, self.baseline_acc)
            if board:
                self._write(board)
        else:
            ranked = sorted(
                scores,
                key=lambda s: (s.get("composite", s["accuracy"]), s["accuracy"]),
                reverse=True,
            )
            parts = []
            for s in ranked:
                lbl = s.get("label", "?")
                acc = s["accuracy"]
                ab = " (aborted)" if s.get("escalation_aborted") else ""
                parts.append(f"{lbl}={acc:.1%}{ab}")
            self._write(f"  Scoreboard: {' | '.join(parts)}")

        improved = d.get("improved", False)
        w_acc = d.get("winner_accuracy", 0.0)
        action = d.get("next_action", "?")
        if improved:
            delta = w_acc - self.baseline_acc
            self._write(
                f"  {GREEN}{BOLD}✓ IMPROVED{RESET}  {fmt_pct(w_acc)} "
                f"({_fmt_delta(delta)} vs prev)  →  next: {action}"
            )
            self.baseline_acc = w_acc
        else:
            self._write(f"  {YELLOW}{BOLD}⚠ NO IMPROVEMENT{RESET}  best {fmt_pct(w_acc)}")
