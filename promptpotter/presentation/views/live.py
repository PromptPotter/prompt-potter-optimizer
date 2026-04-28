"""Live ``RunListener`` adapter — CLI and notebook share one ``LiveDisplay``.

Surface differentiation via constructor flags, not subclasses:
- ``sp_budget_ttest`` truthy → enables tqdm progress bars (CLI feel)
- ``store`` provided → enables ``note()`` / ``render_claude_notes()`` /
  ``render_journal()`` (notebook ↔ Claude exchange channel)

Per-query / per-candidate / per-round formatters live in sibling modules
(``render_query``, ``render_individual``, ``render_round``,
``progress_bar``) — this file is just the listener orchestration.
Post-hoc reads happen by opening ``campaigns/<cycle_id>/log.md``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.phases import CampaignPhase, PhaseEvent
from promptpotter.presentation.views.display_primitives import (
    _box_bottom,
    _box_bottom_info,
    _box_line,
    _box_top,
    _node_bottom,
    _node_top,
)
from promptpotter.presentation.views.phase_events import render_phase_event
from promptpotter.presentation.views.progress_bar import _BarTracker
from promptpotter.presentation.views.render_individual import (
    build_individual_summary,
    fmt_individual_header,
)
from promptpotter.presentation.views.render_query import _fmt_query_result
from promptpotter.presentation.views.render_round import (
    render_patience_status,
    render_progress_table,
    render_round_stats,
)

if TYPE_CHECKING:
    from promptpotter.application.optimization.results import RoundResult
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.infrastructure.store import Stores


class LiveDisplay:
    """Live ``RunListener`` adapter — CLI + notebook share this one class."""

    def __init__(
        self,
        *,
        baseline_acc: float,
        l1_patience: int,
        pipeline_schema: PipelineSchema | None,
        scoring_formula: str | None = None,
        campaign_rounds: list | None = None,
        store: Stores | None = None,
        sp_budget_ttest: int | None = None,
    ) -> None:
        self.baseline_acc = baseline_acc
        self.l1_patience = l1_patience
        self.pipeline_schema = pipeline_schema
        self.scoring_formula = scoring_formula
        self.campaign_rounds = campaign_rounds if campaign_rounds is not None else []
        self.initial_len = len(self.campaign_rounds)
        self.query_counter = 0
        self._round_num = 0
        self._store = store
        self._bars = _BarTracker(sp_budget_ttest) if sp_budget_ttest is not None else None

    def _write(self, line: str) -> None:
        if self._bars is not None:
            self._bars.write(line)
        else:
            print(line, flush=True)

    def set_baseline(self, fresh: float) -> None:
        """Post-baseline rewire — replace pre-baseline placeholder."""
        self.baseline_acc = fresh

    # --- RunListener display protocol ---------------------------------

    def on_phase(self, event: PhaseEvent, view: dict | None) -> None:
        if self._bars is not None:
            self._bars.on_phase(event)
        if view is not None:
            record = {
                "phase": event.phase,
                "event": event.event,
                "round": event.round,
                "view": view,
            }
            if rendered := render_phase_event(record):
                self._write(rendered)
            # Track the running baseline directly from view dicts. INIT:exit
            # carries the post-baseline accuracy; L1_SCORE:exit promotes the
            # round winner to baseline when it improved.
            if event.phase == CampaignPhase.INIT and event.event == "exit":
                self.baseline_acc = view.get("baseline_acc", self.baseline_acc)
            elif (
                event.phase == CampaignPhase.L1_SCORE
                and event.event == "exit"
                and view.get("improved")
            ):
                self.baseline_acc = view.get("winner_accuracy", self.baseline_acc)
        if event.round is not None:
            self._round_num = event.round
        if event.phase == CampaignPhase.ESCALATION and event.event == "exit":
            self.query_counter = 0
        if (
            event.phase == CampaignPhase.INIT
            and event.event == "exit"
            and event.data["env"].resumed_from_round > 0
        ):
            del self.campaign_rounds[self.initial_len :]

    def on_sample_started(
        self, cand_idx: int, n_cands: int, query_idx: int, n_queries: int, query_text: str
    ) -> None:
        # Per-query output renders after the result lands; the emitter's
        # dashboard.json surfaces the in-flight state.
        if self._bars is not None:
            self._bars.on_sample_started(cand_idx, n_cands, n_queries)

    def on_sample_scored(
        self, cand_idx: int, n_cands: int, query_idx: int, n_queries: int, result: dict
    ) -> None:
        self.query_counter += 1
        prefix = f"  [{self.query_counter:>3d}] "
        self._write(
            _fmt_query_result(
                result,
                cached=bool(result.get("cached", False)),
                prefix=prefix,
                scoring_formula=self.scoring_formula,
            )
        )
        if self._bars is not None:
            self._bars.on_sample_scored()

    def on_candidate_started(
        self,
        idx: int,
        total: int,
        changes_description: str,
        pp_override: dict | None,
    ) -> None:
        # Close any still-open bar (e.g. final query of prior candidate) so
        # the header lands above the fresh bar.
        if self._bars is not None:
            self._bars.close()
        self._write(fmt_individual_header(idx, total, changes_description, pp_override))

    def on_candidate_scored(self, idx: int, total: int, scores: dict) -> None:
        if self._bars is not None:
            self._bars.close()
        w = 66
        label = f"C{idx + 1}"
        summary = build_individual_summary(scores, self.baseline_acc)

        self._write(f"  {_box_top(f'{label}/{total}', summary.tag, width=w)}")
        if summary.body_line:
            self._write(f"  {_box_line(summary.body_line, width=w)}")
        for line in summary.detail_lines[:-1]:
            self._write(f"  {_box_line(line, width=w)}")
        if summary.detail_lines:
            self._write(f"  {_box_bottom_info(summary.detail_lines[-1], width=w)}")
        else:
            self._write(f"  {_box_bottom(width=w)}")

    def on_round_complete(self, round_result: RoundResult, l1_stall_count: int) -> None:
        if self._bars is not None:
            self._bars.close()
        self.query_counter = 0

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

        rn = self._round_num + 1
        self._write("")
        self._write(_node_top(f"ROUND {rn} SUMMARY"))
        for line in render_progress_table(self.campaign_rounds).split("\n"):
            self._write(line)
        if stats := render_round_stats(round_result, self.pipeline_schema):
            for line in stats.split("\n"):
                if line:
                    self._write(line)
        for line in render_patience_status(
            round_result.improved, l1_stall_count, self.l1_patience
        ).split("\n"):
            self._write(line)
        self._write(_node_bottom())

    # --- Notebook ↔ Claude exchange channel (active when ``store`` is set) ---

    def _resolve_session_dir(self) -> Path:
        from promptpotter.infrastructure.store import read_active_pointer

        if self._store is None:
            raise RuntimeError("note()/render_*() require store=; pass it to LiveDisplay.")
        _, sid, _cid = read_active_pointer()
        if not sid:
            raise RuntimeError(
                "No active session - run init/auto-mint before calling "
                "display.note() or display.render_claude_notes()."
            )
        return self._store.sessions.session_dir(sid)

    def note(self, action: str, body: str = "") -> None:
        """Append a narrative note to ``journal.md`` for Claude."""
        from promptpotter.infrastructure.persistence.session_emitter import append_journal

        append_journal(self._resolve_session_dir(), action, body)

    def render_claude_notes(self) -> None:
        """Render ``notes.md`` inline so Claude's notes appear in a cell."""
        from promptpotter.infrastructure.persistence.session_emitter import read_claude_notes
        from promptpotter.presentation.views.formatting import render_markdown_box

        content = read_claude_notes(self._resolve_session_dir()).rstrip()
        print(render_markdown_box("CLAUDE NOTES", content, "(no claude notes yet)"))

    def render_journal(self) -> None:
        """Render ``journal.md`` inline - mirror of notes."""
        from promptpotter.presentation.views.formatting import render_markdown_box

        path = self._resolve_session_dir() / "journal.md"
        content = path.read_text(encoding="utf-8").rstrip() if path.exists() else ""
        print(render_markdown_box("JOURNAL", content, "(no journal entries yet)"))
