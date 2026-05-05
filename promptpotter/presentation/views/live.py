"""Live ledger subscriber — CLI and notebook share one ``LiveDisplay``.

Surface differentiation via constructor flags, not subclasses:
- ``sp_budget_ttest`` truthy → enables tqdm progress bars (CLI feel)
- ``store`` provided → enables ``note()`` / ``render_claude_notes()`` /
  ``render_journal()`` (notebook ↔ Claude exchange channel)

Single ingress: the display consumes ``CycleRecord``s from the per-cycle
``CycleLedger`` via ``on_record``. Per-query / per-candidate / per-round
formatters live in sibling modules; this file is the dispatch + tqdm
bar tracker. Post-hoc reads happen by opening ``campaigns/<cycle_id>/log.md``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.phases import CampaignPhase, PhaseEvent
from promptpotter.domain.run_records import CycleRecord, PhaseRecord, SnapshotRecord
from promptpotter.infrastructure.store.base import read_text_optional
from promptpotter.presentation.views.display import (
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
    build_individual_summary,
    fmt_individual_header,
    render_patience_status,
    render_progress_table,
    render_round_stats,
)
from promptpotter.shared.composite import (
    compact_display_enabled,
    render_composite_fitness_block,
)

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.results import RoundResult
    from promptpotter.infrastructure.store import Stores


class _BarTracker:
    """tqdm bar lifecycle driven by ``RunCallbacks`` events. Optional helper."""

    def __init__(self, sp_budget_ttest: int) -> None:
        self.budget = sp_budget_ttest
        self._pbar: Any = None
        self._cand_idx: int = -1
        self._in_baseline: bool = False

    def close(self) -> None:
        if self._pbar is not None:
            self._pbar.close()
            self._pbar = None
        self._cand_idx = -1

    def write(self, line: str) -> None:
        from tqdm.auto import tqdm

        tqdm.write(line)

    def on_phase(self, event: PhaseEvent) -> None:
        if event.event == "exit":
            if event.phase == CampaignPhase.BASELINE:
                self.close()
                self._in_baseline = False
            elif event.phase == CampaignPhase.L1_SCORE:
                self.close()
        elif event.event == "enter" and event.phase == CampaignPhase.BASELINE:
            self._in_baseline = True

    def on_sample_started(self, ci: int, ct: int, qt: int) -> None:
        from tqdm.auto import tqdm

        if self._in_baseline:
            if self._pbar is None:
                self._pbar = tqdm(total=qt or 1, desc="  baseline", unit="q", leave=False, ncols=60)
            return
        if ci != self._cand_idx:
            self.close()
            self._cand_idx = ci
            # Bar tops out at sp_budget_ttest; early t-test elimination leaves
            # it partially filled — which is the signal, not a bug.
            self._pbar = tqdm(
                total=self.budget, desc=f"  cand {ci + 1}/{ct}", unit="q", leave=False, ncols=60
            )

    def on_sample_scored(self) -> None:
        if self._pbar is not None:
            self._pbar.update(1)


class LiveDisplay:
    """Live ``RunCallbacks`` adapter — CLI + notebook share this one class."""

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
        # Composite-render context — read by ``on_candidate_scored`` for the
        # per-candidate baseline anchor and by ``on_round_complete`` for the
        # 3-line composite_fitness block. Populated from L1_SCORE:exit views and
        # mutated on ``scoring_steer:applied``. ``RunCallbacks`` wires its
        # shared ctx onto ``self._phase_ctx`` after construction so the
        # display sees the same dict the phase-view builder writes to.
        self._phase_ctx: dict = {}
        # Per-query Posterior-of-Being-Best snapshot from the prior firing —
        # used to render arrow glyphs (▲/▼) on the next ``on_p_best_update``.
        self._last_p_best: dict[str, float] = {}

    def _write(self, line: str) -> None:
        if self._bars is not None:
            self._bars.write(line)
        else:
            print(line, flush=True)

    def set_baseline(self, fresh: float) -> None:
        """Post-baseline rewire — replace pre-baseline placeholder."""
        self.baseline_acc = fresh

    # --- Ledger subscription ------------------------------------------

    def on_record(self, record: CycleRecord, offset: int) -> None:
        """Route a typed record to the corresponding internal handler."""
        del offset
        if isinstance(record, PhaseRecord):
            if record.phase == "round" and record.event == "complete":
                payload = record.payload or {}
                round_result = payload.get("round_result")
                l1_stall = int(payload.get("l1_stall_count") or 0)
                if round_result is not None:
                    # Re-sync phase ctx from listener-side snapshot so the
                    # composite_fitness block reads the same baseline anchors the
                    # listener saw at emit time.
                    ctx = payload.get("phase_ctx")
                    if isinstance(ctx, dict):
                        self._phase_ctx.update(ctx)
                    self.on_round_complete(round_result, l1_stall)
                return
            payload = record.payload or {}
            view = payload.get("view")
            data = payload.get("data") or {}
            event = PhaseEvent(
                phase=record.phase,
                event=record.event,
                round=record.round,
                data=data,
            )
            self.on_phase(event, view)
        elif isinstance(record, SnapshotRecord):
            ev = record.event
            payload = record.payload or {}
            if ev == "sample_started":
                self.on_sample_started(
                    int(record.candidate_idx or 0),
                    int(record.candidate_total or 0),
                    int(record.sample_idx or 0),
                    int(record.sample_total or 0),
                    payload.get("query_text") or "",
                )
            elif ev == "sample_scored":
                self.on_sample_scored(
                    int(record.candidate_idx or 0),
                    int(record.candidate_total or 0),
                    int(record.sample_idx or 0),
                    int(record.sample_total or 0),
                    payload.get("result") or {},
                )
            elif ev == "candidate_started":
                self.on_candidate_started(
                    int(record.candidate_idx or 0),
                    int(record.candidate_total or 0),
                    payload.get("changes_description") or "",
                    payload.get("pp_override"),
                )
            elif ev == "candidate_scored":
                ctx = payload.get("phase_ctx")
                if isinstance(ctx, dict):
                    self._phase_ctx.update(ctx)
                self.on_candidate_scored(
                    int(record.candidate_idx or 0),
                    int(record.candidate_total or 0),
                    payload.get("scores") or {},
                )
            elif ev == "p_best_update":
                self.on_p_best_update(
                    str(payload.get("current_id") or ""),
                    int(payload.get("n_queries") or 0),
                    {str(k): float(v) for k, v in (payload.get("p_best") or {}).items()},
                )

    # --- Public callback API ------------------------------------------
    #
    # These methods are the direct entry point for callers that don't
    # route through a ledger (notably ``baseline.py``, which fires before
    # the per-cycle ledger exists). The ``on_record`` dispatcher above
    # forwards ledger-driven events into the same handlers.

    def on_phase(self, event: PhaseEvent, view: dict | None = None) -> None:
        if self._bars is not None:
            self._bars.on_phase(event)
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

    def on_p_best_update(self, current_id: str, n_queries: int, p_best: dict[str, float]) -> None:
        """One-line per-query Posterior-of-Being-Best snapshot.

        Top-5 candidates by P(best); current candidate marked with asterisks;
        arrow glyphs show direction of change since the previous query.
        """
        if not p_best:
            return
        last = self._last_p_best
        top = sorted(p_best.items(), key=lambda kv: -kv[1])[:5]
        parts: list[str] = []
        for cid, prob in top:
            prev = last.get(cid)
            arrow = ""
            if prev is not None:
                if prob > prev + 1e-4:
                    arrow = "▲"  # BLACK UP-POINTING TRIANGLE
                elif prob < prev - 1e-4:
                    arrow = "▼"  # BLACK DOWN-POINTING TRIANGLE
            tag = f"*{cid[:6]}*" if cid == current_id else cid[:6]
            parts.append(f"{tag} {prob * 100:4.1f}%{arrow}")
        self._write(f"       p_best q{n_queries}: " + " ".join(parts))
        self._last_p_best = dict(p_best)

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
        baseline_acc = self._phase_ctx.get("baseline_accuracy", self.baseline_acc)
        baseline_comp = self._phase_ctx.get("baseline_composite_fitness")
        summary = build_individual_summary(
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

    def on_round_complete(self, round_result: RoundResult, l1_stall_count: int) -> None:
        if self._bars is not None:
            self._bars.close()
        self.query_counter = 0

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

        rn = self._round_num + 1
        self._write("")
        self._write(_node_top(f"ROUND {rn} SUMMARY"))
        for line in render_progress_table(self.campaign_rounds).split("\n"):
            self._write(line)
        # Composite block — full mode only. 3-line render: composite_fitness +
        # baseline anchor (line 1), abbreviated formula (line 2), short-
        # name evaluator values (line 3). Anchored to the campaign
        # baseline so operators see how far the run came from origin.
        # Short formula is None for custom user formulas — fall back to
        # full text and accept the wrap.
        formula_short = self._phase_ctx.get("composite_fitness_formula_short")
        formula_full = self._phase_ctx.get("composite_fitness_formula")
        if (formula_short or formula_full) and not compact_display_enabled():
            for line in render_composite_fitness_block(
                round_result.composite_fitness,
                dict(round_result.evaluators),
                formula_short or formula_full,
                baseline=self._phase_ctx.get("baseline_composite_fitness"),
                use_short_names=bool(formula_short),
            ):
                self._write(_node_line(line))
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
        from promptpotter.infrastructure.projections import append_journal

        append_journal(self._resolve_session_dir(), action, body)

    def render_claude_notes(self) -> None:
        """Render ``notes.md`` inline so Claude's notes appear in a cell."""
        from promptpotter.infrastructure.projections import read_claude_notes
        from promptpotter.presentation.views.display import render_markdown_box

        content = read_claude_notes(self._resolve_session_dir()).rstrip()
        print(render_markdown_box("CLAUDE NOTES", content, "(no claude notes yet)"))

    def render_journal(self) -> None:
        """Render ``journal.md`` inline - mirror of notes."""
        from promptpotter.presentation.views.display import render_markdown_box

        content = read_text_optional(self._resolve_session_dir() / "journal.md").rstrip()
        print(render_markdown_box("JOURNAL", content, "(no journal entries yet)"))
