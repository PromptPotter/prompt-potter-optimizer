"""Notebook display — thin ``RunListener`` adapter.

Each callback: build the serializable view via the application layer
(``build_phase_view``) or read it from the result dict, hand it to the
shared renderer in ``presentation/views``, ``print()`` the resulting
string. Zero domain-model mutation, zero ANSI assembly here. The
emitter writes the same JSON to disk independently.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.application.campaign.phase_views import build_phase_view
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.phases import CampaignPhase, PhaseEvent
from promptpotter.infrastructure.persistence.session_emitter import (
    append_journal,
    read_claude_notes,
)
from promptpotter.presentation.views.candidate_view import (
    build_candidate_summary,
    fmt_candidate_header,
)
from promptpotter.presentation.views.display_primitives import (
    _box_bottom,
    _box_bottom_info,
    _box_line,
    _box_top,
    _node_bottom,
    _node_top,
)
from promptpotter.presentation.views.markdown_box import render_markdown_box
from promptpotter.presentation.views.phase_events import render_phase_event
from promptpotter.presentation.views.query_format import _fmt_query_result
from promptpotter.presentation.views.round_summary import (
    render_patience_status,
    render_progress_table,
    render_round_stats,
)

if TYPE_CHECKING:
    from promptpotter.application.optimization.results import RoundResult
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.infrastructure.store import Stores


class NotebookDisplay:
    """Notebook listener — pure adapter that prints rendered strings."""

    def __init__(
        self,
        *,
        campaign_rounds: list,
        baseline_acc: float,
        l1_patience: int,
        pipeline_schema: PipelineSchema | None,
        store: Stores,
        scoring_formula: str | None = None,
    ) -> None:
        self.campaign_rounds = campaign_rounds
        self.baseline_acc = baseline_acc
        self.l1_patience = l1_patience
        self.pipeline_schema = pipeline_schema
        self.initial_len = len(campaign_rounds)
        self.scoring_formula = scoring_formula
        self.query_counter = 0
        self._store = store
        # Per-cycle accumulator threaded through ``build_phase_view`` —
        # parallel to the emitter's ``_phase_ctx``. Both observe the same
        # event stream and converge on identical view dicts.
        self._phase_ctx: dict[str, Any] = {}
        # Mirrors the round number the emitter tracks; populated by
        # ``build_phase_view`` as a side-effect on each event.
        self._round_num = 0

    def set_baseline(self, fresh: float) -> None:
        """Post-baseline rewire — mirrors ``CliDisplay.set_baseline``."""
        self.baseline_acc = fresh
        self._phase_ctx["baseline_accuracy"] = fresh

    def _resolve_session_dir(self) -> Path:
        from promptpotter.infrastructure.store import read_active_pointer

        _, sid, _cid = read_active_pointer()
        if not sid:
            raise RuntimeError(
                "No active session - run init/auto-mint before calling "
                "display.note() or display.render_claude_notes()."
            )
        return self._store.sessions.session_dir(sid)

    def note(self, action: str, body: str = "") -> None:
        """Append a narrative note to ``journal.md`` for Claude."""
        append_journal(self._resolve_session_dir(), action, body)

    def render_claude_notes(self) -> None:
        """Render ``notes.md`` inline so Claude's notes appear in a cell."""
        content = read_claude_notes(self._resolve_session_dir()).rstrip()
        print(render_markdown_box("CLAUDE NOTES", content, "(no claude notes yet)"))

    def render_journal(self) -> None:
        """Render ``journal.md`` inline - mirror of notes."""
        path = self._resolve_session_dir() / "journal.md"
        content = path.read_text(encoding="utf-8").rstrip() if path.exists() else ""
        print(render_markdown_box("JOURNAL", content, "(no journal entries yet)"))

    def on_phase(self, event: PhaseEvent) -> None:
        view = build_phase_view(event, self._phase_ctx)
        if view is not None:
            record = {
                "phase": event.phase,
                "event": event.event,
                "round": event.round,
                "view": view,
            }
            rendered = render_phase_event(record)
            if rendered:
                print(rendered)
        self._round_num = self._phase_ctx.get("round_num", self._round_num)
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
        pass

    def on_sample_scored(
        self, cand_idx: int, n_cands: int, query_idx: int, n_queries: int, result: dict
    ) -> None:
        self.query_counter += 1
        prefix = f"  [{self.query_counter:>3d}] "
        print(
            _fmt_query_result(
                result,
                cached=bool(result.get("cached", False)),
                prefix=prefix,
                scoring_formula=self.scoring_formula,
            ),
            flush=True,
        )

    def on_candidate_started(
        self,
        idx: int,
        total: int,
        changes_description: str,
        pp_override: dict | None,
    ) -> None:
        print(fmt_candidate_header(idx, total, changes_description, pp_override), flush=True)

    def on_candidate_scored(self, idx: int, total: int, scores: dict) -> None:
        w = 66
        label = f"C{idx + 1}"
        baseline_acc = self._phase_ctx.get("baseline_accuracy", self.baseline_acc)
        summary = build_candidate_summary(scores, baseline_acc)

        print(f"  {_box_top(f'{label}/{total}', summary.tag, width=w)}")
        if summary.body_line:
            print(f"  {_box_line(summary.body_line, width=w)}")
        for line in summary.detail_lines[:-1]:
            print(f"  {_box_line(line, width=w)}")
        if summary.detail_lines:
            print(f"  {_box_bottom_info(summary.detail_lines[-1], width=w)}")
        else:
            print(f"  {_box_bottom(width=w)}")

    def on_round_complete(self, round_result: RoundResult, l1_stall_count: int) -> None:
        self.query_counter = 0
        self._phase_ctx["l1_stall_count"] = l1_stall_count

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
        print()
        print(_node_top(f"ROUND {rn} SUMMARY"))
        for line in render_progress_table(self.campaign_rounds).split("\n"):
            print(line)
        stats = render_round_stats(round_result, self.pipeline_schema)
        if stats:
            for line in stats.split("\n"):
                print(line)
        for line in render_patience_status(
            round_result.improved, l1_stall_count, self.l1_patience
        ).split("\n"):
            print(line)
        print(_node_bottom())
