"""``LiveDisplay`` — RunCallbacks adapter; CLI + notebook share this one class."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.application.views.view_models import AnyView
from promptpotter.config.settings import OPTIMIZER_PROMPT_BUDGET_CHARS
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.phases import CampaignPhase, PhaseEvent
from promptpotter.domain.rendering import round_winner_key
from promptpotter.domain.results import candidate_label
from promptpotter.domain.run_records import (
    LLMCallProgressRecord,
    LLMCallRecord,
    LLMCallStartRecord,
    PhaseRecord,
    RoundWarningRecord,
    SnapshotRecord,
)
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
from promptpotter.presentation.views.live.candidate import (
    fmt_individual_header,
    individual_summary_from_dict,
)
from promptpotter.presentation.views.live.phase import (
    fmt_elapsed,
    render_patience_status,
    render_progress_table,
    render_round_stats,
)
from promptpotter.presentation.views.live.sample import fmt_query_result
from promptpotter.presentation.views.render import to_text
from promptpotter.shared.composite import render_composite_fitness_block

if TYPE_CHECKING:
    from typing import TextIO

    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.results import RoundResult


# Rolling mirror of the live stdout stream to a gitignored, most-recent-only file, so a
# headless reader (or a returning operator) can open the last run's full readout — satisfies
# the presentation "everything emitted to stdout is findable on disk" constraint. Truncated
# per run in ``__init__``; ANSI-stripped per line. Captures the LiveDisplay stream only —
# ``logging``-level warnings route through Python logging, not ``_write``.
_READOUT_PATH = Path("logs/latest.log")
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _open_readout() -> TextIO | None:
    """Truncate and hold open the run-readout mirror; ``None`` if the filesystem refuses — capture is best-effort and must
    never abort a costly run. Held open rather than reopened per line, and line-buffered so a hard kill still leaves them."""
    try:
        _READOUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        return _READOUT_PATH.open("w", encoding="utf-8", buffering=1)
    except OSError:
        return None


class LiveDisplay(DerivedView):
    def __init__(
        self,
        *,
        origin_acc: float,
        l1_patience: int,
        pipeline_schema: PipelineSchema | None,
        scoring_formula: str | None = None,
        campaign_rounds: list[dict[str, Any]] | None = None,
    ) -> None:
        self._core = LiveStateCore(origin_acc=origin_acc)
        self.l1_patience = l1_patience
        self.pipeline_schema = pipeline_schema
        self.scoring_formula = scoring_formula
        self.campaign_rounds = campaign_rounds if campaign_rounds is not None else []
        self.initial_len = len(self.campaign_rounds)
        self.sample_counter = 0
        self._phase_ctx: dict[str, Any] = {}  # wired by RunCallbacks; shared with phase-view
        # Live round-leader tracker, ordered by the shared `round_winner_key`
        # (composite-first, accuracy tie-break) so ★ can't contradict the display
        # ranking; `_round_best_acc` is kept alongside for the Δ-from-leader line.
        self._round_best_key: tuple[float, float] | None = None
        self._round_best_acc: float | None = None
        self._round_best_label: str | None = None
        self._round_started_at: float | None = None
        self._pobb_printed_for: str = ""
        self._pending_calls: dict[str, int] = {}
        self._readout = _open_readout()

    def _write(self, line: str) -> None:
        print(line, flush=True)
        if self._readout is not None:
            try:
                self._readout.write(_ANSI_RE.sub("", line) + "\n")
            except (OSError, ValueError):
                self._readout = None  # stop retrying; never break the run for a dev mirror

    @property
    def origin_acc(self) -> float:
        return self._core.origin_acc

    def set_origin(self, fresh: float) -> None:
        """Post-origin rewire — the headline scalars ONLY. It does not seed a row: round 0 arrives through ``on_round_complete``
        like every round, and a synthetic one put the origin in the table twice and shifted every later round number."""
        self._core.origin_acc = fresh
        self._core.best_acc = max(self._core.best_acc, fresh)

    # --- Ledger subscription (via DerivedView) ---------------------

    def _handle_phase(self, record: PhaseRecord) -> None:
        payload = record.payload
        if record.phase == "round" and record.event == "display":
            # Full RoundResult rides the in-memory-only field; the persisted
            # payload['round_result'] is the lean 3-scalar form for the SSE tail.
            round_result = record.live_round_result
            if round_result is not None:
                # Re-sync phase ctx so composite_fitness reads listener-side anchors.
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

    def _handle_llm_call_start(self, record: LLMCallStartRecord) -> None:
        self._pending_calls[record.call_id] = record.started_at_ms
        model = record.model or "(default)"
        round_tag = f"r{record.round}" if record.round is not None else ""
        node_label = f"{record.node}_{round_tag}" if round_tag else record.node
        # The node's own budget, not one line across all of them: a shared 8000 sat below
        # every node's minimum, so the marker was yellow on every call and meant nothing.
        budget = OPTIMIZER_PROMPT_BUDGET_CHARS.get(record.node)
        oversize = budget is not None and record.prompt_chars > budget
        marker = "⚠ " if oversize else "↻ "
        bits = [f"{marker}optimizer call: {node_label} · {model}"]
        if record.prompt_chars > 0:
            bits.append(f"{record.prompt_chars:,}c prompt")
        color = YELLOW if oversize else DIM
        self._write(f"  {color}{' · '.join(bits)}{RESET}")

    def _handle_round_warning(self, record: RoundWarningRecord) -> None:
        """Surface a self-healed round degradation as a one-line marker — parity with the dashboard's warning list and the round
        file's block, the same fact on every channel. ``message`` is composed at the emit site, so this just prints it."""
        round_tag = f"r{record.round}" if record.round is not None else ""
        glyph = "✗" if record.severity == "error" else "⚠"
        prefix = f"{glyph} {round_tag} ".rstrip() if round_tag else f"{glyph} "
        self._write(f"  {YELLOW}{prefix}{record.message}{RESET}")

    def _handle_llm_call_progress(self, record: LLMCallProgressRecord) -> None:
        """Heartbeat tick; cached replays skip it. A BARE tick proves only that the process is alive, one carrying ``detail`` reports
        progress — print ``detail`` VERBATIM: dropping it let a healthy inner campaign read as a frozen call."""
        round_tag = f"r{record.round}" if record.round is not None else ""
        node_label = f"{record.node}_{round_tag}" if round_tag else record.node
        line = f"  · {node_label} still waiting · {record.elapsed_s:.0f}s"
        if record.detail:
            line += f" · {record.detail}"
        self._write(f"  {DIM}{line}{RESET}")

    def _handle_llm_call(self, record: LLMCallRecord) -> None:
        payload = record.payload
        cached = bool(payload.get("cached"))
        started = self._pending_calls.pop(record.call_id, None) if record.call_id else None
        duration_s = payload.get("duration_s")
        if duration_s is None and started is not None:
            duration_s = max(0.0, (time.time() * 1000 - started) / 1000.0)
        usage = payload.get("usage") or {}
        total_tokens = usage.get("total_tokens")
        round_tag = f"r{record.round}" if record.round is not None else ""
        node_label = f"{record.node}_{round_tag}" if round_tag else record.node
        bits: list[str] = [node_label]
        if isinstance(duration_s, (int, float)):
            bits.append(f"{duration_s:.1f}s")
        if isinstance(total_tokens, int) and total_tokens > 0:
            bits.append(f"{total_tokens} tok")
        # What the seconds beside it actually bought. A reasoning model can spend nearly its
        # whole output budget thinking — measured at ~94% on the shipped optimizer route, where
        # the answer is schema-capped at ~1300 characters — and the duration alone reads as a
        # slow provider rather than as a node that was asked to think about something small.
        # Silent at 0 so a non-reasoning model's line stays clean.
        completion = usage.get("completion_tokens")
        reasoning = usage.get("reasoning_tokens")
        if (
            isinstance(reasoning, int)
            and reasoning > 0
            and isinstance(completion, int)
            and completion > 0
        ):
            bits.append(f"{reasoning / completion:.0%} reasoning")
        if cached:
            bits.append("cached")
        self._write(f"  {DIM}✓ {' · '.join(bits)}{RESET}")

    def _handle_snapshot(self, record: SnapshotRecord) -> None:
        payload = record.payload
        ci = int(record.candidate_idx or 0)
        ct = int(record.candidate_total or 0)
        qi = int(record.sample_idx or 0)
        qt = int(record.sample_total or 0)
        ev = record.event
        # sample_started: LiveDashboardView pulses the in-flight row; CLI has no equivalent (sample_scored covers it).
        if ev == "sample_started":
            return
        if ev == "sample_scored":
            self.on_sample_scored(ci, payload.get("result") or {}, qi, qt)
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
                float(payload.get("p_best") or 0.0),
                {
                    str(pid): {str(k): float(v) for k, v in (entry or {}).items()}
                    for pid, entry in (payload.get("paired_breakdown") or {}).items()
                },
            )
        elif ev == "sample_order_preview":
            self.on_sample_order_preview(
                [int(sid) for sid in (payload.get("sample_order") or [])],
                int(payload.get("n_priors") or 0),
            )
        elif ev == "pobb_backfill":
            self.on_pobb_backfill(
                int(payload.get("sample_id") or 0),
                [str(p) for p in (payload.get("prior_ids") or [])],
            )

    # --- Public callback API (pre-ledger paths call these directly) ---

    def on_phase(self, event: PhaseEvent, view: AnyView | None = None) -> None:
        if event.phase == CampaignPhase.L1_SCORE and event.event == "enter":
            self._write("\n" + _node_top("SCORE"))
        if view is not None and (rendered := to_text(view)):
            self._write(rendered)
        apply_phase(self._core, event, view)
        if event.phase == CampaignPhase.L1_GENERATE and event.event == "enter":
            self._round_best_key = None
            self._round_best_acc = None
            self._round_best_label = None
            self._round_started_at = time.monotonic()
        if event.phase == CampaignPhase.ESCALATION and event.event == "exit":
            self.sample_counter = 0
        # Resume-rewind rebuild needs the live ``env``/``state`` objects, which
        # exist only on the direct in-memory callback path — they're stripped
        # from the persisted/streamed record (``RunCallbacks._DATA_KEYS_RUNTIME_ONLY``).
        # On the ledger path ``env`` is absent and the display rebuilds from the
        # replayed ``round:display`` records instead, so skip cleanly.
        env_obj = (
            event.data.get("env")
            if event.phase == CampaignPhase.INIT and event.event == "exit"
            else None
        )
        if env_obj is not None and env_obj.state.resumed_from_round > 0:
            del self.campaign_rounds[self.initial_len :]
            cycle_state = event.data.get("state")
            for rr in getattr(cycle_state, "rounds", None) or []:
                self.campaign_rounds.append(
                    {
                        "round": rr.round,
                        "label": rr.label,
                        "accuracy": rr.accuracy,
                        "composite_fitness": rr.composite_fitness,
                        "hits": rr.hits,
                        "total": rr.total,
                        "improved": rr.improved,
                        "results": rr.results,
                        "candidates_scored": rr.candidates_scored,
                        "candidate_scores": [c.model_dump() for c in rr.candidate_scores],
                    }
                )
        if event.phase == "scoring_steer" and event.event == "applied":
            new_formula = event.data.get("formula")
            if new_formula:
                self._phase_ctx["composite_fitness_formula"] = new_formula

    def on_sample_scored(
        self, cand_idx: int, result: dict[str, Any], sample_idx: int, n_samples: int
    ) -> None:
        self.sample_counter += 1
        prefix = f"  [{self.sample_counter:>3d}] "
        self._write(
            fmt_query_result(
                result,
                cached=bool(result.get("cached", False)),
                prefix=prefix,
                scoring_formula=self.scoring_formula,
            )
        )

    def on_p_best_update(
        self,
        current_id: str,
        n_samples: int,
        p_best: float,
        paired_breakdown: dict[str, dict[str, float]],
    ) -> None:
        apply_p_best_update(self._core, current_id, n_samples, p_best)
        POBB_DISPLAY_MIN_SAMPLES = 8  # matches ``lock_in_n_min`` in pobb/checks.py
        if (
            current_id
            and current_id != self._pobb_printed_for
            and n_samples >= POBB_DISPLAY_MIN_SAMPLES
        ):
            self._pobb_printed_for = current_id
            current_p = p_best
            # Paired PoBB: hardest prior = min P(cand > prior). Read off the field that NAMES
            # that quantity rather than off the P(best) reading, which is one number about
            # this candidate and cannot answer a per-prior question.
            if paired_breakdown:
                hardest_id, hardest = min(
                    paired_breakdown.items(), key=lambda kv: kv[1].get("p_better", 1.0)
                )
                hardest_tag, hardest_p = hardest_id[:6], hardest.get("p_better", 0.0)
            else:
                hardest_tag, hardest_p = "*self*", current_p
            n_priors = len(paired_breakdown)
            prior_s = "" if n_priors == 1 else "s"
            self._write(
                f"  {DIM}pobb:{RESET} P(best)={current_p:.1%} @ q{n_samples}  "
                f"vs hardest={hardest_tag} (P(c>p)={hardest_p:.1%})  "
                f"(of {n_priors} prior{prior_s})"
            )

    def on_sample_order_preview(self, sample_order: list[int], n_priors: int) -> None:
        if not sample_order:
            return
        prior_s = "" if n_priors == 1 else "s"
        head = ", ".join(f"#{sid:03d}" for sid in sample_order[:3])
        self._write(
            f"  {DIM}shared order:{RESET} {head}, …  "
            f"({len(sample_order)} samples, win-opportunities first; "
            f"{n_priors} candidate prior{prior_s})"
        )

    def on_pobb_backfill(self, sample_id: int, prior_ids: list[str]) -> None:
        if not prior_ids:
            return
        tags = [cid if cid == "origin" or cid.endswith("_winner") else cid[:6] for cid in prior_ids]
        self._write(f"  {DIM}↻ pobb backfill #{sample_id}:{RESET} " + ", ".join(tags))

    def _render_p_best_line(self) -> str | None:
        """Top-5 P(best) across the round's CANDIDATES, with each arrow against that candidate's own previous reading. Ranking one
        snapshot's dict instead ranks its odds against each prior, and last round's ids never match — they are round-scoped."""
        if not self._core.round_p_best:
            return None
        last = self._core.round_p_best_prev
        parts: list[str] = []
        for cid, prob in top_n_p_best(self._core.round_p_best):
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
        pp_override: dict[str, Any] | None,
    ) -> None:
        self._write(fmt_individual_header(idx, total, changes_description, pp_override))

    def on_candidate_scored(self, idx: int, total: int, scores: dict[str, Any]) -> None:
        w = 66
        label = scores.get("label") or candidate_label(self._core.round_num, idx)
        origin_acc = self._phase_ctx.get("origin_accuracy", self.origin_acc)
        origin_comp = self._phase_ctx.get("origin_composite_fitness")
        summary = individual_summary_from_dict(
            scores, origin_acc, origin_composite_fitness=origin_comp
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

        # Skip invalid + degradation-aborted candidates — partial accuracy can inflate above origin.
        if summary.status != "invalid" and not scores.get("degradation_context"):
            acc = scores.get("accuracy")
            comp = scores.get("composite_fitness")
            if isinstance(acc, int | float):
                self._write(
                    self._fmt_round_leader(
                        label,
                        float(acc),
                        origin_acc,
                        float(comp) if isinstance(comp, int | float) else None,
                    )
                )

    def _fmt_round_leader(
        self, label: str, acc: float, origin_acc: float, composite: float | None
    ) -> str:
        """Scoreboard one-liner, ordered by the shared ``round_winner_key`` so the live ★ cannot contradict the display ranking.
        Point-estimate only — the true θ-LCB election prints at round close."""
        delta_origin = acc - origin_acc
        key = round_winner_key(composite, acc)
        new_round_max = self._round_best_key is None or key > self._round_best_key
        if new_round_max:
            self._round_best_key = key
            self._round_best_acc = acc
            self._round_best_label = label
            if delta_origin > 0:
                return (
                    f"  {GREEN}★ leader: {label} {acc:.1%}  "
                    f"(Δ +{delta_origin:.1%} vs origin){RESET}"
                )
            if delta_origin == 0:
                return f"  {YELLOW}= ties origin: {label} {acc:.1%}  (Δ ±0.0% vs origin){RESET}"
            return (
                f"  {DIM}→ round-best: {label} {acc:.1%}  (Δ {delta_origin:.1%} vs origin){RESET}"
            )
        gap = acc - (self._round_best_acc or acc)
        prior = self._round_best_label or "leader"
        return f"  {DIM}→ {label} {acc:.1%}  ({gap:.1%} from {prior}){RESET}"

    def on_round_complete(self, round_result: RoundResult, l1_stall_count: int) -> None:
        self.sample_counter = 0

        self.campaign_rounds.append(
            {
                "round": round_result.round,
                "label": round_result.label,
                "accuracy": round_result.accuracy,
                "composite_fitness": round_result.composite_fitness,
                "total": round_result.total,
                "improved": round_result.improved,
                "prompt_fields": OptSearchPoint.from_prompt_fields(round_result.prompt_fields),
                "results": round_result.results,
                "candidates_scored": round_result.candidates_scored,
                "candidate_scores": [c.model_dump() for c in round_result.candidate_scores],
            }
        )

        rn = self._core.round_num
        elapsed_label = ""
        if self._round_started_at is not None:
            elapsed = time.monotonic() - self._round_started_at
            elapsed_label = f" — {fmt_elapsed(elapsed)}"
        self._round_started_at = None
        self._write("")
        self._write(_node_top(f"ROUND {rn} SUMMARY{elapsed_label}"))
        for line in render_progress_table(self.campaign_rounds).split("\n"):
            self._write(line)
        if (p_best_line := self._render_p_best_line()) is not None:
            self._write(_node_line(p_best_line))
        roll_p_best_at_round_complete(self._core)
        formula_short = self._phase_ctx.get("composite_fitness_formula_short")
        formula_full = self._phase_ctx.get("composite_fitness_formula")
        if formula_short or formula_full:
            # Compare against the MATCHED origin (origin re-scored on this round's
            # own subset) — always populated by the winner election (round 0 = full
            # origin). Under per_round_resubset the round-0 origin composite is a
            # different subset, so comparing against it reads draw difficulty as
            # candidate lift; a legitimately 0.0 matched origin is a real floor, not
            # an absent one, so there is no cross-subset fallback to slip through.
            for line in render_composite_fitness_block(
                round_result.composite_fitness,
                dict(round_result.evaluators),
                formula_short or formula_full,
                origin=round_result.matched_origin_composite,
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


__all__ = ["LiveDisplay"]
