"""``LiveDisplay`` — RunCallbacks adapter; CLI + notebook share this one class.

Subclasses ``DerivedView`` so the ``isinstance`` dispatch over ledger
``CycleRecord`` subtypes lives in one place. The ``on_*`` public methods
stay because pre-cycle paths (``origin.py``) call them directly without
a ledger.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.phases import CampaignPhase, PhaseEvent
from promptpotter.domain.results import candidate_label
from promptpotter.domain.run_records import (
    LLMCallProgressRecord,
    LLMCallRecord,
    LLMCallStartRecord,
    PhaseRecord,
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
    """Live ``RunCallbacks`` adapter — CLI + notebook share this one class.

    Subclasses ``DerivedView`` so the ``isinstance`` dispatch over
    ledger ``CycleRecord`` subtypes lives in one place; this class only
    overrides ``_handle_phase`` / ``_handle_snapshot``. The ``on_*``
    public methods stay because pre-cycle paths (``origin.py``) call
    them directly without a ledger.
    """

    def __init__(
        self,
        *,
        origin_acc: float,
        l1_patience: int,
        pipeline_schema: PipelineSchema | None,
        scoring_formula: str | None = None,
        campaign_rounds: list | None = None,
    ) -> None:
        # Per-cycle scalars live on a shared ``LiveStateCore`` (round number,
        # origin + best anchors, P(best) round snapshot) so the dashboard
        # projection and this terminal renderer maintain one shape, not two.
        self._core = LiveStateCore(origin_acc=origin_acc)
        self.l1_patience = l1_patience
        self.pipeline_schema = pipeline_schema
        self.scoring_formula = scoring_formula
        self.campaign_rounds = campaign_rounds if campaign_rounds is not None else []
        self.initial_len = len(self.campaign_rounds)
        self.sample_counter = 0
        # Composite-render context — read by ``on_candidate_scored`` for
        # the per-candidate origin anchor and by ``on_round_complete``
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
        # "no measurement yet" (resume points, pre-origin) — render
        # falls back to omitting the elapsed field.
        self._round_started_at: float | None = None
        # PoBB first-fire-per-candidate guard — tracks the candidate_id
        # most recently surfaced in a ``pobb:`` snapshot line so each
        # candidate prints exactly one (the first time `check()` produces
        # a posterior with non-empty priors). Resets implicitly when a
        # new candidate_id appears on the stream.
        self._pobb_printed_for: str = ""
        # Pending-call register — ``call_id → started_at_ms`` for any
        # optimizer LLM call whose start marker has printed but whose
        # completion has not yet landed. Indexed so the completion
        # handler can compute wall-clock duration and pair the line
        # with the start. Same record types the dashboard's
        # ``in_flight`` slot consumes; no new ledger writes.
        self._pending_calls: dict[str, int] = {}

    def _write(self, line: str) -> None:
        print(line, flush=True)

    @property
    def origin_acc(self) -> float:
        """Running origin anchor — read-only mirror of the shared core."""
        return self._core.origin_acc

    def set_origin(self, fresh: float) -> None:
        """Post-origin rewire — replace pre-origin placeholder.

        Also seeds ``campaign_rounds`` with an origin row at index 0 so
        the round-summary trend table reads as ``Origin → 1 → 2 → …``
        instead of treating the first L1 round as round 0 and dropping
        the origin.
        """
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
                # Re-sync phase ctx from listener-side snapshot so the
                # composite_fitness block reads the same origin anchors
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

    def _handle_llm_call_start(self, record: LLMCallStartRecord) -> None:
        """Surface an in-flight optimizer LLM call as a one-line CLI marker.

        Closes the multi-minute blind spot between phase-boundary boxes
        — after SCOREBOARD an L1_CRITIQUE call fires for 30-60s with no
        terminal output today (the dashboard's ``in_flight`` slot
        already carries this signal; the CLI just wasn't reading it).
        Same record the dashboard projection consumes — no new ledger
        writes. ``_pending_calls`` indexes the call so the paired
        completion handler can compute and report wall-clock duration.

        ``record.prompt_chars`` is on the line so the operator sees the
        input size upfront — a 12k-char l1_generate vs an 800-char l3_plan
        have very different latency expectations. Char count is shown
        verbatim (not converted to tokens) because the tokenizer isn't
        on this path; total tokens land on the completion line.
        """
        self._pending_calls[record.call_id] = record.started_at_ms
        model = record.model or "(default)"
        round_tag = f"r{record.round}" if record.round is not None else ""
        node_label = f"{record.node}_{round_tag}" if round_tag else record.node
        bits = [f"↻ optimizer call: {node_label} · {model}"]
        if record.prompt_chars > 0:
            bits.append(f"{record.prompt_chars:,}c prompt")
        self._write(f"  {DIM}{' · '.join(bits)}{RESET}")

    def _handle_llm_call_progress(self, record: LLMCallProgressRecord) -> None:
        """Render an elapsed-time heartbeat while an LLM call is in flight.

        Fires every ``HEARTBEAT_INTERVAL_S`` (15s) for the duration of
        the call, so the operator sees a live counter — `... 15s`, `...
        30s`, `... 45s` — instead of staring at the static start line.
        Cached replays short-circuit before the heartbeat coroutine
        starts (in ``llm_call``), so this only fires for real network
        round-trips. The pairing call_id isn't shown to keep the line
        terse; node + round are enough for the operator to map the tick
        back to the start line two lines above.
        """
        round_tag = f"r{record.round}" if record.round is not None else ""
        node_label = f"{record.node}_{round_tag}" if round_tag else record.node
        self._write(f"  {DIM}  · {node_label} still waiting · {record.elapsed_s:.0f}s{RESET}")

    def _handle_llm_call(self, record: LLMCallRecord) -> None:
        """Surface optimizer LLM completion as a paired one-line CLI marker.

        Pairs with :meth:`_handle_llm_call_start` via ``call_id`` so
        duration is the operator-visible wall-clock between start and
        completion. ``payload.duration_s`` is preferred (set by
        ``llm_call``) — falls back to the start-to-now delta if the
        payload omits it. ``payload.usage.total_tokens`` lands on the
        line so the cost mental model is on the headline alongside
        the time. Cached calls are tagged so a quiet cache replay isn't
        confused with a real round-trip.
        """
        payload = record.payload or {}
        # Cached replays usually carry no start marker (call short-circuits
        # before LLMCallStartRecord) — render a one-line cache hit instead
        # of pairing.
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
        if ev == "sample_scored":
            self.on_sample_scored(ci, ct, payload.get("result") or {}, qi, qt)
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
            backfilled = payload.get("backfilled") or {}
            self.on_pobb_backfill(
                {str(k): [str(s) for s in (v or [])] for k, v in backfilled.items()}
            )

    # --- Public callback API ------------------------------------------
    #
    # These methods are the direct entry point for callers that don't
    # route through a ledger (notably ``origin.py``, which fires before
    # the per-cycle ledger exists). The ``on_record`` dispatcher above
    # forwards ledger-driven events into the same handlers.

    def on_phase(self, event: PhaseEvent, view: dict | None = None) -> None:
        if event.phase == CampaignPhase.L1_SCORE and event.event == "enter":
            self._write("\n" + _node_top("SCORE"))
        if view is not None:
            from promptpotter.presentation.views.view_ingress import view_from_record

            record = {
                "phase": event.phase,
                "event": event.event,
                "round": event.round,
                "view": view,
            }
            typed = view_from_record(record)
            if typed is not None and (rendered := to_text(typed)):
                self._write(rendered)
        # Round number + origin/best anchors flow through the shared core
        # (INIT:exit carries the post-origin accuracy; L1_SCORE:exit on
        # ``improved`` promotes the new winner). ``view=None`` still tracks
        # the round number.
        apply_phase(self._core, event, view)
        if event.phase == CampaignPhase.L1_GENERATE and event.event == "enter":
            self._round_best_acc = None
            self._round_best_label = None
            self._round_started_at = time.monotonic()
        # INIT:exit carries origin_composite_fitness in its view payload.
        # Patch the origin row's composite once it's available so the
        # trend table's Composite column reads against the real composite
        # instead of the accuracy fallback.
        if event.phase == CampaignPhase.INIT and event.event == "exit" and view is not None:
            origin_comp = view.get("origin_composite_fitness")
            if origin_comp is not None:
                for rd in self.campaign_rounds:
                    if rd.get("label") == "origin" and rd.get("composite_fitness") is None:
                        rd["composite_fitness"] = origin_comp
                        break
        if event.phase == CampaignPhase.ESCALATION and event.event == "exit":
            self.sample_counter = 0
        if (
            event.phase == CampaignPhase.INIT
            and event.event == "exit"
            and event.data["env"].state.resumed_from_round > 0
        ):
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
                        "candidate_scores": list(rr.candidate_scores),
                    }
                )
        # Mirror an interactive-steer formula swap onto the shared phase
        # ctx so the next round's renderers print the new formula.
        if event.phase == "scoring_steer" and event.event == "applied":
            new_formula = event.data.get("formula")
            if new_formula:
                self._phase_ctx["composite_fitness_formula"] = new_formula

    def on_sample_scored(
        self, cand_idx: int, n_cands: int, result: dict, sample_idx: int, n_samples: int
    ) -> None:
        # ``cand_idx < 0`` is the paired-PoBB backfill sentinel — these
        # measurements are on the leader, not the current candidate, so
        # they ride a distinct prefix and don't advance the main-loop
        # counter. Without this branch the only signal an operator gets
        # during a 14-sample backfill was the opaque "deprecated retry:
        # resuming" stderr line; here each backfill measurement lands
        # as its own row so the LLM spend is visible.
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
        """Stash latest Posterior-of-Being-Best snapshot for the round-end roll-up.

        Per-sample mid-round detail lives in ``dashboard.json``; the
        terminal sees one consolidated p_best line in the round-summary
        box (rendered by ``_render_p_best_line``). First fire for each
        candidate also prints a one-line "the check is alive" snapshot —
        the operator's evidence that PoBB sees non-empty priors and is
        actively comparing this candidate against them.
        """
        apply_p_best_update(self._core, current_id, n_samples, p_best)
        # Suppress the per-candidate snapshot below the lock-in floor.
        # PoBB at q<8 with hard-sample-sorted draws is too noisy to act
        # on — a candidate read P(best)=73.6% at q6 then finished
        # ±0.0% vs origin at q20 in the cycle that motivated this gate.
        # Matches ``lock_in_n_min`` in ``pobb/elimination.py`` so display
        # tracks the same authoritative-read threshold the optimizer uses.
        POBB_DISPLAY_MIN_SAMPLES = 8
        if (
            current_id
            and current_id != self._pobb_printed_for
            and n_samples >= POBB_DISPLAY_MIN_SAMPLES
        ):
            self._pobb_printed_for = current_id
            current_p = p_best.get(current_id, 0.0)
            leader_id, leader_p = (
                max(p_best.items(), key=lambda kv: kv[1]) if p_best else (current_id, current_p)
            )
            leader_tag = "*self*" if leader_id == current_id else leader_id[:6]
            n_priors = max(0, len(p_best) - 1)
            prior_s = "" if n_priors == 1 else "s"
            self._write(
                f"  {DIM}pobb:{RESET} P(best)={current_p:.1%} @ q{n_samples}  "
                f"leader={leader_tag} ({leader_p:.1%})  "
                f"(of {n_priors} prior{prior_s})"
            )

    def on_sample_order_preview(self, preview: list[tuple[int, float]], n_priors: int) -> None:
        """Print PoBB's next-3 picks for this candidate by cross-candidate
        discrimination (``p*(1-p)``, max 0.25 at 50/50 hit rate).

        Fires after ``on_candidate_started`` and before any sample
        scoring. Empty ``preview`` means the sorter had no observations
        to fit (e.g. round-1 candidate-1 with no in-round priors and an
        empty cycle); the line is suppressed in that case so the
        operator isn't told "next: " over an empty list.
        """
        if not preview:
            return
        prior_s = "" if n_priors == 1 else "s"
        picks = ", ".join(f"#{sid:03d} (disc={val:.3f})" for sid, val in preview)
        self._write(f"  {DIM}next samples:{RESET} {picks}  ({n_priors} candidate prior{prior_s})")

    def on_pobb_backfill(self, backfilled: dict[str, list[str]]) -> None:
        """Print which priors got fresh leader-on-hard-sample measurements.

        Fires only when backfill actually measured something (the
        ``RunCallbacks`` constructor suppresses no-op events). Empty
        priors-with-zero-additions are filtered upstream so a quiet
        line here means every prior was already cached on this
        candidate's sample set.
        """
        if not backfilled:
            return
        parts = []
        for cid, sids in backfilled.items():
            tag = cid if cid in ("origin",) or cid.endswith("_winner") else cid[:6]
            parts.append(f"{tag} +{len(sids)} ({','.join('#' + s for s in sids[:5])})")
        self._write(f"  {DIM}↻ pobb backfill:{RESET} " + "  ".join(parts))

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
        self._write(fmt_individual_header(idx, total, changes_description, pp_override))

    def on_candidate_scored(self, idx: int, total: int, scores: dict) -> None:
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

        # Mid-round leader scoreboard — one tight line so the operator can read
        # "is anything beating origin yet" without scrolling through 100+
        # query lines to find the round-summary box. Skip invalid candidates
        # and any candidate whose run halted on a degradation/scoring-error
        # check — partial-measurement accuracy on the valid subset can inflate
        # above origin on a 6/20 abort. Mirrors the leader-pick exclusion in
        # ``l1/score.py`` so the live ★ leader pointer can't crown a
        # candidate the round-summary would never elect.
        if summary.status != "invalid" and not scores.get("degradation_context"):
            acc = scores.get("accuracy")
            if isinstance(acc, int | float):
                self._write(self._fmt_round_leader(label, float(acc), origin_acc))

    def _fmt_round_leader(self, label: str, acc: float, origin_acc: float) -> str:
        """One-liner scoreboard.

        ``★ leader`` is reserved for a new round-best that strictly beats
        origin — the only candidate that actually leads. A new round-best
        that ties or trails origin shows as ``→ round-best`` (still tracks
        the prior pointer so later candidates can report ``from {prior}``),
        and non-max candidates fall through to ``→ {label} … from {prior}``.
        """
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
                "candidate_scores": list(round_result.candidate_scores),
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
        # Roll p_best snapshot into the cross-round origin so next
        # round's arrows compare against this round's final.
        roll_p_best_at_round_complete(self._core)
        # Composite block — full mode only. 3-line render: composite_fitness +
        # origin anchor (line 1), abbreviated formula (line 2), short-
        # name evaluator values (line 3). Anchored to the campaign
        # origin so operators see how far the run came from origin.
        # Short formula is None for custom user formulas — fall back to
        # full text and accept the wrap.
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
