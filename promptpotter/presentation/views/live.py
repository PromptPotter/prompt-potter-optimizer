"""Live ledger subscriber — CLI and notebook share one ``LiveDisplay``.

Single ingress: the display consumes ``CycleRecord``s from the per-cycle
``CycleEventLog`` via ``on_record``. All per-sample, per-candidate, and
round-summary renderers are private to this file because nothing else
calls them. Post-hoc reads happen by opening
``campaigns/<cycle_id>/log.md``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from promptpotter.application.optimization.elimination import classify_result
from promptpotter.application.scoring.formula import extract_display_answer
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.phases import CampaignPhase, PhaseEvent
from promptpotter.domain.results import candidate_label
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
    CYAN,
    DIM,
    DISPLAY_TAGS,
    GREEN,
    RED,
    RESET,
    YELLOW,
    _box_bottom,
    _box_bottom_info,
    _box_line,
    _box_top,
    _fmt_delta,
    _node_bottom,
    _node_line,
    _node_top,
    _pp_val,
    _step_tag,
    fmt_ci,
    fmt_pvalue,
)
from promptpotter.presentation.views.render import to_text
from promptpotter.shared.composite import (
    render_composite_fitness_block,
    render_composite_fitness_oneliner,
)
from promptpotter.shared.errors import is_error_result
from promptpotter.shared.statistics import wilson_ci

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
# Per-candidate header + per-sample HIT/MISS formatters.
# Pure: no I/O, no mutation. Single caller is ``LiveDisplay``.
# ===========================================================================


def fmt_pp_override(pp: dict | None) -> str:
    """Flatten a nested pipeline_params override to ``node.key: val  …``.

    Returns ``""`` when the override is empty or None. Callers supply the
    leading label (e.g. ``cand 2/5  ``) and any ANSI wrapping.
    """
    if not pp:
        return ""
    parts: list[str] = []
    for node, val in pp.items():
        if isinstance(val, dict):
            for k, v in val.items():
                parts.append(f"{node}.{k}: {_pp_val(v)}")
        else:
            parts.append(f"{node}: {_pp_val(val)}")
    return "  ".join(parts)


def fmt_individual_header(
    idx: int,
    total: int,
    changes_description: str,
    pp_override: dict | None,
) -> str:
    """One-line pre-scoring header: ``ind k/N  <mutation>`` or ``ind k/N  parent re-eval``.

    Fired from ``on_candidate_started`` before the backend calls start. The
    mutation body comes from ``pp_override`` when present (authoritative
    machine-readable diff), else falls back to the human-written
    ``changes_description`` from the L1 variant plan, else the parent tag.
    """
    label = f"ind {idx + 1}/{total}"
    body = fmt_pp_override(pp_override)
    if not body and changes_description:
        body = changes_description.strip()
    body = f"{DIM}parent re-eval{RESET}" if not body else f"{CYAN}{body}{RESET}"
    return f"  {label}  {body}"


def _ellide(s: str, n: int) -> str:
    """Truncate *s* to *n* chars; append ``…`` when cut so the operator sees it."""
    return s if len(s) <= n else s[: n - 1] + "…"


def _append_annotation(line: str, indent: str, color: str, emoji: str, text: str) -> str:
    """Append a per-sample annotation line under the query it describes.

    ``indent`` mirrors the query line's leading whitespace so the emoji sits
    directly under the HIT/MISS tag instead of floating at column 0.
    """
    return line + f"\n{indent}{color}{emoji} {text}{RESET}"


def _fmt_query_result(
    r: dict,
    cached: bool = False,
    *,
    prefix: str = "",
    scoring_formula: str | None = None,
) -> str:
    """Format a single query result as a HIT/MISS line with timing.

    When *prefix* is given it replaces the default 8-space indent so the
    caller can merge a counter into the same line. *scoring_formula* is
    the dataset's scoring formula (from ``campaign_config["scoring"]``);
    when provided, ``predicted`` is parsed via ``extract_display_answer``
    so markers like ``**bold**`` or ``\\boxed{…}`` collapse to the
    single-token answer.
    """
    from promptpotter.application.scoring.metrics import find_rank

    raw_pred = r.get("predicted") or ""
    pred = _ellide(extract_display_answer(raw_pred, scoring_formula), 30)
    gt_full = r.get("ground_truth", "") or ""
    gt = _ellide(gt_full.strip(), 30)
    q = _ellide((r.get("query") or "").replace("\n", " ").strip(), 15)
    err = r.get("error") or ("pipeline error" if is_error_result(r) else None)
    pd = r.get("pipeline_data") or {}
    step_name = pd.get("terminated_at")
    if step_name is None and (st := pd.get("step_timings")):
        # Last non-None entry wins (dict insertion order).
        step_name = next((n for n, t in reversed(list(st.items())) if t is not None), None)
    step = _step_tag(step_name)

    tt = pd.get("total_time")

    if classify_result(r).is_fatal:
        tag = "DEPR"
    elif r.get("hit"):
        tag = "HIT"
    else:
        ranked = pd.get("ranked_items", [])
        n_cand = len(ranked)
        gt_rank: int | None = None
        if gt_full:
            for key in ("ranked_items", "token_matches"):
                if (gt_rank := find_rank(pd.get(key, []), gt_full)) is not None:
                    break
        if gt_rank is not None:
            tag = f"MISS {gt_rank}/{n_cand}"
        elif n_cand:
            tag = f"MISS --/{n_cand}"
        else:
            tag = "MISS"

    cache_marker = "\U0001f4d6" if cached else ""

    # Per-LLM-node token column: `[tag] io=N/M` groups in pipeline order.
    # Values are prefixed with `~` when the entry is a chars/4 estimate rather
    # than a provider-exact count. Single-node pipelines drop the io-group
    # tag (source is unambiguous). The standalone step tag is kept only as
    # the cache-marker anchor — `HIT [ai]📖 io=…` when cached, `HIT io=…`
    # when not.
    single_node = len(DISPLAY_TAGS) == 1
    step_tokens = pd.get("step_tokens") or {}
    tok_col = ""
    if step_tokens:
        groups = []
        for node_name, entry in step_tokens.items():
            mark = "~" if entry.get("estimated") else ""
            io_seg = f"io={mark}{entry.get('input', 0)}/{mark}{entry.get('output', 0)}"
            if single_node:
                groups.append(io_seg)
            else:
                tag_name = DISPLAY_TAGS.get(node_name, node_name[:4])
                groups.append(f"[{tag_name}] {io_seg}")
        tok_col = " " + " ".join(groups)

    step = "" if (single_node and not cached) else f"{step}{cache_marker}"

    indent = prefix if prefix else ""
    if r.get("retry_of_deprecated_cache"):
        indent = f"{indent}\U0001f504 "

    time_col = f"{tt:5.1f}s" if tt is not None else "     "
    sid = r.get("sample_id")
    sid_col = f"#{sid:03d}" if sid is not None else "    "
    step_block = f" {step}" if step else ""
    if err:
        return f"{indent}{time_col} {sid_col} {tag}{step_block}{tok_col} ERR:{str(err)[:40]!r} gt:{gt!r} q:{q!r}"

    line = f"{indent}{time_col} {sid_col} {tag}{step_block}{tok_col} -> {pred!r} gt:{gt!r} q:{q!r}"

    _ann_indent = " " * len(indent) if indent else "      "

    diag = pd.get("diagnostics", {})
    warnings = diag.get("warnings", [])
    if warnings:
        for w in warnings:
            stats = w.get("stats")
            if stats:
                msg = f"{stats['min']} min, {stats['usable']} usable, {stats['fetched']} fetched, {stats['requested']} requested"
            else:
                msg = w["message"]
            line += f"\n{_ann_indent}{YELLOW}⚠ {w['step']}: {msg}{RESET}"

    if r.get("retry_of_degraded"):
        comp = r.get("rerun_comparison") or {}
        detail = f"; result: {comp['hit_change']}" if comp.get("hit_change") else ""
        if comp.get("rank_change"):
            detail += f" (rank {comp['rank_change']})"
        line = _append_annotation(
            line,
            _ann_indent,
            YELLOW,
            "\U0001f504",
            f"cache had pipeline warnings → reran{detail}",
        )
    elif r.get("samplescan_resolved"):
        cfg = r.get("samplescan_config") or {}
        n = cfg.get("n_candidates", "?")
        thr = cfg.get("resolved_threshold", "?")
        line = _append_annotation(
            line,
            _ann_indent,
            YELLOW,
            "\U0001f52c",
            f"cache had warnings + rerun still degraded → resampled {n} fresh calls "
            f"(threshold {thr}); result accepted",
        )
    elif r.get("switched_out"):
        line = _append_annotation(
            line,
            _ann_indent,
            YELLOW,
            "\U0001f500",
            "query degrades ≥50% of the time historically → using cached answer "
            "(resampling would likely degrade again)",
        )
    elif r.get("persistently_degraded"):
        line = _append_annotation(
            line,
            _ann_indent,
            RED,
            "⚠",
            "entire stale-data ladder exhausted → still degraded; "
            "score counts but flag this candidate",
        )
    elif r.get("degraded_observed") and not classify_result(r).is_fatal:
        # Fatal classifications kill the candidate on this same query (see
        # DegradationCheck fast-path). The "toward rerun" counter would
        # suggest "more data coming" — meaningless when the candidate is
        # already dead. The ⚠ warning line above tells the real story.
        obs = r.get("degraded_obs_count", "?")
        threshold = r.get("degraded_obs_threshold", "?")
        line = _append_annotation(
            line,
            _ann_indent,
            DIM,
            "↩",
            f"pipeline warning observed; {obs}/{threshold} occurrences toward rerun "
            f"trigger (not yet at threshold)",
        )

    return line


# ===========================================================================
# Per-candidate summary classification — ``LiveDisplay.on_candidate_scored``
# also feeds notebook box rendering. Pure: no I/O, no mutation.
# ===========================================================================


@dataclass(frozen=True)
class IndividualSummary:
    """Structured candidate render — displays pick plain vs box wrapping.

    Pieces the two displays can compose independently:

    - ``tag`` — compact status slot (``80.0% [77-82%]`` or ``INVALID``);
      notebook puts it on the top-right of the box, CLI appends it to
      ``cand k/N``.
    - ``body_line`` — the mutations + hits + delta line; the notebook
      renders it as an inner box line, the CLI as an indented second line.
    - ``detail_lines`` — ordered extras (elimination summary, the 1-line
      composite_fitness-with-Δ render, degraded-count tag, or validation-failure
      entries); rendered inline by both displays, with the last entry
      folded onto the bottom info rule by callers that support it.
    """

    status: Literal["ok", "invalid", "aborted", "eliminated"]
    tag: str
    body_line: str
    detail_lines: tuple[str, ...]


def individual_summary_from_dict(
    scores: dict,
    origin_acc: float,
    *,
    origin_composite_fitness: float | None = None,
) -> IndividualSummary:
    """Classify a candidate score report and pre-format all display pieces.

    Single source of truth for what the CLI and notebook show per candidate.
    Invalid > aborted > eliminated > ok precedence matches the report's
    exclusive flag semantics (invalid never runs the backend; aborted and
    eliminated are mutually exclusive by construction in
    ``_handle_scored_candidate``).

    Per-candidate composite_fitness render is intentionally 1 line —
    ``composite_fitness=0.6042  (Δ+0.103 vs origin 0.5012)`` — so 5 candidates
    don't dump 60 lines of identical formula text into the terminal. The
    formula + per-evaluator breakdown lands once per round in the round
    summary block.

    *origin_composite_fitness* anchors the Δ against the campaign's first-round
    composite_fitness — even at deep rounds the operator sees how far the run
    has come from origin. ``None`` collapses to the no-Δ form.
    """
    mutations = fmt_pp_override(scores.get("pipeline_params_override"))
    mutations_chunk = f"{CYAN}{mutations}{RESET}  " if mutations else ""

    if scores.get("invalid"):
        out: list[str] = []
        for vf in scores["validation_failures"]:
            allowed = vf.get("allowed") or []
            allowed_str = ", ".join(allowed[:3]) + (
                f" (+{len(allowed) - 3})" if len(allowed) > 3 else ""
            )
            out.append(
                f"{YELLOW}⚠{RESET} {vf.get('axis', '?')} = {vf.get('value', '?')!r}  "
                f"∉ [{allowed_str}]"
            )
            out.append("  ↳ scored 0 (no backend call); L2 brief will name this value")
        return IndividualSummary(
            status="invalid",
            tag=f"{YELLOW}INVALID{RESET}",
            body_line="",
            detail_lines=tuple(out),
        )

    acc = scores["accuracy"]
    hits = scores.get("hits", 0)
    n = scores.get("total", 0)
    ci_lo, ci_hi = wilson_ci(hits, n)
    delta = acc - origin_acc
    tag = f"{acc:.1%} {fmt_ci(ci_lo, ci_hi)}"

    aborted = bool(scores.get("escalation_aborted"))
    if aborted:
        scored_q = scores.get("scored_samples", n)
        expected_q = scores.get("expected_samples", n)
        hit_str = f"{hits}/{scored_q} hits {YELLOW}⚠ aborted {scored_q}/{expected_q}{RESET}"
    else:
        hit_str = f"{hits}/{n} hits"
    body_line = f"{mutations_chunk}{hit_str}  vs origin: {_fmt_delta(delta)}"

    detail_lines: list[str] = []
    elim = scores.get("elimination_context") or {}
    if scores.get("elimination_stopped") or elim.get("leader_locked"):
        eq = int(elim.get("queries_scored", 0))
        eqt = int(elim.get("total_queries", 0))
        n_priors = int(elim.get("n_priors", 0))
        prior_s = "" if n_priors == 1 else "s"
        if scores.get("elimination_stopped"):
            label = elim.get("triggered_by_prior_label")
            if label is None:
                pi = elim.get("triggered_by_prior_idx", -1)
                label = f"prior #{pi}" if isinstance(pi, int) and pi >= 0 else "prior"
            detail_lines.append(
                f"{YELLOW}✂ eliminated q{eq}/{eqt}{RESET}  "
                f"{fmt_pvalue(float(elim.get('triggered_p', 1.0)))}  "
                f"vs {label} (of {n_priors} prior{prior_s})"
            )
        else:
            detail_lines.append(
                f"{GREEN}✓ leader locked q{eq}/{eqt}{RESET}  "
                f"p_best={float(elim.get('p_best', 0.0)):.1%} (of {n_priors} prior{prior_s})"
            )

    comp = scores.get("composite_fitness")
    degraded = scores.get("degraded_samples", 0)

    if comp is not None:
        detail_lines.append(
            render_composite_fitness_oneliner(comp, origin=origin_composite_fitness)
        )
    if degraded:
        detail_lines.append(f"{YELLOW}⚠ {degraded}/{n} degraded{RESET}")

    status: Literal["ok", "aborted", "eliminated"]
    if aborted:
        status = "aborted"
    elif scores.get("elimination_stopped"):
        status = "eliminated"
    else:
        status = "ok"
    return IndividualSummary(
        status=status,
        tag=tag,
        body_line=body_line,
        detail_lines=tuple(detail_lines),
    )


# ===========================================================================
# LiveDisplay — RunCallbacks adapter
# ===========================================================================


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

    def _write(self, line: str) -> None:
        print(line, flush=True)

    @property
    def origin_acc(self) -> float:
        """Running origin anchor — read-only mirror of the shared core."""
        return self._core.origin_acc

    def set_origin(self, fresh: float) -> None:
        """Post-origin rewire — replace pre-origin placeholder."""
        self._core.origin_acc = fresh
        if fresh > self._core.best_acc:
            self._core.best_acc = fresh

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

    def on_sample_scored(
        self, cand_idx: int, n_cands: int, result: dict, sample_idx: int, n_samples: int
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
        # (no comparable accuracy).
        if summary.status != "invalid":
            acc = scores.get("accuracy")
            if isinstance(acc, int | float):
                self._write(self._fmt_round_leader(label, float(acc), origin_acc))

    def _fmt_round_leader(self, label: str, acc: float, origin_acc: float) -> str:
        """One-liner scoreboard: ``★ leader`` on a new best, ``→`` else."""
        delta_origin = acc - origin_acc
        if self._round_best_acc is None or acc > self._round_best_acc:
            self._round_best_acc = acc
            self._round_best_label = label
            sign = "+" if delta_origin >= 0 else ""
            return f"  {GREEN}★ leader: {label} {acc:.1%}  (Δ {sign}{delta_origin:.1%} vs origin){RESET}"
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
            elapsed_label = f" — {_fmt_elapsed(elapsed)}"
        self._round_started_at = None
        self._write("")
        self._write(_node_top(f"ROUND {rn} SUMMARY{elapsed_label}"))
        for line in _render_progress_table(self.campaign_rounds).split("\n"):
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
        if stats := _render_round_stats(round_result, self.pipeline_schema):
            for line in stats.split("\n"):
                if line:
                    self._write(line)
        for line in _render_patience_status(
            round_result.improved, l1_stall_count, self.l1_patience
        ).split("\n"):
            self._write(line)
        self._write(_node_bottom())
