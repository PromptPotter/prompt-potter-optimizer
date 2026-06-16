"""``LiveDisplay`` — RunCallbacks adapter; CLI + notebook share this one class."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from promptpotter.application.views import AnyView, InitExitView
from promptpotter.config.settings import OPTIMIZER_PROMPT_WARN_CHARS
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.phases import CampaignPhase, PhaseEvent
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
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.results import RoundResult


class LiveDisplay(DerivedView):
    """Live ``RunCallbacks`` adapter — CLI + notebook share this one class."""

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
        self._round_best_acc: float | None = None
        self._round_best_label: str | None = None
        self._round_started_at: float | None = None
        self._pobb_printed_for: str = ""
        self._pending_calls: dict[str, int] = {}

    def _write(self, line: str) -> None:
        print(line, flush=True)

    @property
    def origin_acc(self) -> float:
        return self._core.origin_acc

    def set_origin(self, fresh: float) -> None:
        """Post-origin rewire; seeds the round-0 ``origin`` row in ``campaign_rounds``."""
        self._core.origin_acc = fresh
        if fresh > self._core.best_acc:
            self._core.best_acc = fresh
        if not any(rd.get("label") == "origin" for rd in self.campaign_rounds):
            self.campaign_rounds.insert(
                0,
                {
                    "round": 0,
                    "label": "origin",
                    "accuracy": fresh,
                    "composite_fitness": self._phase_ctx.get("origin_composite_fitness"),
                },
            )
            self.initial_len = max(self.initial_len, 1)

    # --- Ledger subscription (via DerivedView) ---------------------

    def _handle_phase(self, record: PhaseRecord) -> None:
        payload = record.payload or {}
        if record.phase == "round" and record.event == "display":
            round_result = payload.get("round_result")
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
        """In-flight optimizer LLM call as a one-line marker; oversized prompts flip to yellow ⚠."""
        self._pending_calls[record.call_id] = record.started_at_ms
        model = record.model or "(default)"
        round_tag = f"r{record.round}" if record.round is not None else ""
        node_label = f"{record.node}_{round_tag}" if round_tag else record.node
        oversize = record.prompt_chars > OPTIMIZER_PROMPT_WARN_CHARS
        marker = "⚠ " if oversize else "↻ "
        bits = [f"{marker}optimizer call: {node_label} · {model}"]
        if record.prompt_chars > 0:
            bits.append(f"{record.prompt_chars:,}c prompt")
        color = YELLOW if oversize else DIM
        self._write(f"  {color}{' · '.join(bits)}{RESET}")

    def _handle_round_warning(self, record: RoundWarningRecord) -> None:
        """Surface a self-healed round degradation as a one-line CLI/notebook marker.

        Parity with the dashboard's ``recent_loop_warnings`` + the round file's
        ``warnings`` block — the same fact on every channel. ``message`` is
        composed operator-readable at the emit site, so this just prints it.
        """
        round_tag = f"r{record.round}" if record.round is not None else ""
        glyph = "✗" if record.severity == "error" else "⚠"
        prefix = f"{glyph} {round_tag} ".rstrip() if round_tag else f"{glyph} "
        self._write(f"  {YELLOW}{prefix}{record.message}{RESET}")

    def _handle_llm_call_progress(self, record: LLMCallProgressRecord) -> None:
        """Heartbeat tick (``HEARTBEAT_INTERVAL_S``); cached replays skip this path."""
        round_tag = f"r{record.round}" if record.round is not None else ""
        node_label = f"{record.node}_{round_tag}" if round_tag else record.node
        self._write(f"  {DIM}  · {node_label} still waiting · {record.elapsed_s:.0f}s{RESET}")

    def _handle_llm_call(self, record: LLMCallRecord) -> None:
        """Paired LLM-completion marker; reports duration + tokens; tags cached."""
        payload = record.payload or {}
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
        if cached:
            bits.append("cached")
        self._write(f"  {DIM}✓ {' · '.join(bits)}{RESET}")

    def _handle_snapshot(self, record: SnapshotRecord) -> None:
        payload = record.payload or {}
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
                {str(k): float(v) for k, v in (payload.get("p_best") or {}).items()},
            )
        elif ev == "sample_order_preview":
            preview_raw = payload.get("preview") or []
            preview: list[tuple[int, float]] = [
                (int(p[0]), float(p[1])) for p in preview_raw if len(p) >= 2
            ]
            self.on_sample_order_preview(preview, int(payload.get("n_priors") or 0))
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
            self._round_best_acc = None
            self._round_best_label = None
            self._round_started_at = time.monotonic()
        # Patch the origin row's composite once INIT:exit surfaces it.
        if isinstance(view, InitExitView):
            origin_comp = view.origin_composite_fitness
            for rd in self.campaign_rounds:
                if rd.get("label") == "origin" and rd.get("composite_fitness") is None:
                    rd["composite_fitness"] = origin_comp
                    break
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
                        "round": len(self.campaign_rounds),
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
        # cand_idx < 0 = paired-PoBB backfill sentinel; distinct prefix, no counter bump.
        if cand_idx < 0:
            prefix = "  ↻ bf "
        else:
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

    def on_p_best_update(self, current_id: str, n_samples: int, p_best: dict[str, float]) -> None:
        """Stash PoBB snapshot; print one-line summary on first fire per candidate at q≥8."""
        apply_p_best_update(self._core, current_id, n_samples, p_best)
        POBB_DISPLAY_MIN_SAMPLES = 8  # matches ``lock_in_n_min`` in pobb/elimination/checks.py
        if (
            current_id
            and current_id != self._pobb_printed_for
            and n_samples >= POBB_DISPLAY_MIN_SAMPLES
        ):
            self._pobb_printed_for = current_id
            current_p = p_best.get(current_id, 0.0)
            # Paired PoBB: hardest prior = min P(cand > prior).
            prior_entries = {pid: pv for pid, pv in p_best.items() if pid != current_id}
            if prior_entries:
                hardest_id, hardest_p = min(prior_entries.items(), key=lambda kv: kv[1])
                hardest_tag = hardest_id[:6]
            else:
                hardest_tag, hardest_p = "*self*", current_p
            n_priors = len(prior_entries)
            prior_s = "" if n_priors == 1 else "s"
            self._write(
                f"  {DIM}pobb:{RESET} P(best)={current_p:.1%} @ q{n_samples}  "
                f"vs hardest={hardest_tag} (P(c>p)={hardest_p:.1%})  "
                f"(of {n_priors} prior{prior_s})"
            )

    def on_sample_order_preview(self, preview: list[tuple[int, float]], n_priors: int) -> None:
        """Adaptive queue mechanism's expected-information-gain preview (1PL Rasch CAT)."""
        if not preview:
            return
        prior_s = "" if n_priors == 1 else "s"
        picks = ", ".join(f"#{sid:03d} (info={val:.3f})" for sid, val in preview)
        self._write(f"  {DIM}next samples:{RESET} {picks}  ({n_priors} candidate prior{prior_s})")

    def on_pobb_backfill(self, sample_id: int, prior_ids: list[str]) -> None:
        """Priors backfilled on this sample. Cache-covered priors filtered upstream."""
        if not prior_ids:
            return
        tags = [cid if cid == "origin" or cid.endswith("_winner") else cid[:6] for cid in prior_ids]
        self._write(f"  {DIM}↻ pobb backfill #{sample_id}:{RESET} " + ", ".join(tags))

    def _render_p_best_line(self) -> str | None:
        """Top-5 P(best) with ▲/▼ vs prior round; ``None`` when no PoBB this round."""
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
            if isinstance(acc, int | float):
                self._write(self._fmt_round_leader(label, float(acc), origin_acc))

    def _fmt_round_leader(self, label: str, acc: float, origin_acc: float) -> str:
        """Scoreboard one-liner; ``★ leader`` only when round-best strictly beats origin."""
        delta_origin = acc - origin_acc
        new_round_max = self._round_best_acc is None or acc > self._round_best_acc
        if new_round_max:
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
            for line in render_composite_fitness_block(
                round_result.composite_fitness,
                dict(round_result.evaluators),
                formula_short or formula_full,
                origin=self._phase_ctx.get("origin_composite_fitness"),
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
