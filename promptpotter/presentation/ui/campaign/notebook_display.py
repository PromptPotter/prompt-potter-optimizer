"""Notebook display — ``NotebookDisplay`` listener.

Implements the ``RunListener`` display protocol (``on_phase``,
``on_candidate_scored``, ``on_sample_started``, ``on_sample_scored``,
``on_round_complete``)
directly — no adapter needed. Pass an instance as ``display=`` to
``run_optimization``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

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
    DIM,
    RESET,
    _box_bottom,
    _box_bottom_info,
    _box_line,
    _box_top,
    _node_bottom,
    _node_top,
)
from promptpotter.presentation.views.query_format import _fmt_query_result
from promptpotter.presentation.views.round_summary import (
    render_patience_status,
    render_progress_table,
    render_round_stats,
)

from .notebook_phase import _dispatch_phase
from .notebook_sp_diff import _CycleDisplayState

if TYPE_CHECKING:
    from promptpotter.application.optimization.results import RoundResult
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.infrastructure.store import Stores


class NotebookDisplay:
    """Notebook listener — consumed directly by ``RunListener.display``."""

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
        self.state = _CycleDisplayState(baseline_accuracy=baseline_acc)
        self.query_counter = 0
        self.scoring_formula = scoring_formula
        # Resolved lazily each call so auto-mint during the run is picked up.
        self._store = store

    def set_baseline(self, fresh: float) -> None:
        """Post-baseline rewire — mirrors ``CliDisplay.set_baseline``.

        The notebook caches baseline in two fields (``self.baseline_acc``
        and ``state.baseline_accuracy``); both need the fresh value.
        """
        self.baseline_acc = fresh
        self.state.baseline_accuracy = fresh

    def _resolve_session_dir(self) -> Path:
        """Return the active session directory. Raises if no pointer set."""
        from promptpotter.infrastructure.store import read_active_pointer

        _, sid, _cid = read_active_pointer()
        if not sid:
            raise RuntimeError(
                "No active session — run init/auto-mint before calling "
                "display.note() or display.render_claude_notes()."
            )
        return self._store.sessions.session_dir(sid)

    def note(self, action: str, body: str = "") -> None:
        """Append a narrative note to ``journal.md`` for Claude.

        Call from any notebook cell to record intent or observations that
        don't surface in the scalar dashboard — e.g. ``display.note(
        "skipping recon", "axes already known")``. Raises if no active
        session is set.
        """
        append_journal(self._resolve_session_dir(), action, body)

    def _render_markdown_box(self, title: str, content: str, empty_label: str) -> None:
        """Render a markdown file's contents in a titled box, or an empty label."""
        w = 74
        if not content:
            print(f"  {DIM}{empty_label}{RESET}")
            return
        print(f"  {_box_top(title, width=w)}")
        for line in content.split("\n"):
            print(f"  {_box_line(line, width=w)}")
        print(f"  {_box_bottom(width=w)}")

    def render_claude_notes(self) -> None:
        """Render ``notes.md`` inline so Claude's notes appear in a cell."""
        content = read_claude_notes(self._resolve_session_dir()).rstrip()
        self._render_markdown_box("CLAUDE NOTES", content, "(no claude notes yet)")

    def render_journal(self) -> None:
        """Render ``journal.md`` inline — user-written narrative, mirror of notes."""
        path = self._resolve_session_dir() / "journal.md"
        content = path.read_text(encoding="utf-8").rstrip() if path.exists() else ""
        self._render_markdown_box("JOURNAL", content, "(no journal entries yet)")

    def on_phase(self, event: PhaseEvent) -> None:
        _dispatch_phase(event, self.state)
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
        # Notebook display renders per-query output after the result lands;
        # the dashboard (FileSink) surfaces the in-flight state instead.
        pass

    def on_sample_scored(
        self, cand_idx: int, n_cands: int, query_idx: int, n_queries: int, result: dict
    ) -> None:
        self.query_counter += 1
        is_cached = result.get("cached", False)
        prefix = f"  [{self.query_counter:>3d}] "
        print(
            _fmt_query_result(
                result,
                cached=is_cached,
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
        summary = build_candidate_summary(scores, self.state.baseline_accuracy)

        print(f"  {_box_top(f'{label}/{total}', summary.tag, width=w)}")

        if summary.body_line:
            print(f"  {_box_line(summary.body_line, width=w)}")

        # Detail lines: all but last go as box_line; last folds into the
        # bottom_info rule when present so the box doesn't grow a dead row.
        for line in summary.detail_lines[:-1]:
            print(f"  {_box_line(line, width=w)}")
        if summary.detail_lines:
            print(f"  {_box_bottom_info(summary.detail_lines[-1], width=w)}")
        else:
            print(f"  {_box_bottom(width=w)}")

    def on_round_complete(self, round_result: RoundResult, l1_stall_count: int) -> None:
        self.query_counter = 0
        self.state.l1_stall_count = l1_stall_count

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
        self._print_patience_status(round_result, l1_stall_count)

        print(_node_bottom())

    def _print_progress_table(self) -> None:
        for line in render_progress_table(self.campaign_rounds).split("\n"):
            print(line)

    def _print_round_stats(self, round_result: RoundResult) -> None:
        out = render_round_stats(round_result, self.pipeline_schema)
        if out:
            for line in out.split("\n"):
                print(line)

    def _print_patience_status(self, round_result: RoundResult, l1_stall_count: int) -> None:
        for line in render_patience_status(
            round_result.improved, l1_stall_count, self.l1_patience
        ).split("\n"):
            print(line)
