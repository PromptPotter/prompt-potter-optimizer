"""Live ``RunListener`` adapter + the per-query / per-candidate / per-round
formatters it drives. CLI and notebook share one ``LiveDisplay`` class.

Surface differentiation via constructor flags, not subclasses:
- ``sp_budget_ttest`` truthy → enables tqdm progress bars (CLI feel)
- ``store`` provided → enables ``note()`` / ``render_claude_notes()`` /
  ``render_journal()`` (notebook ↔ Claude exchange channel)

Sections:
1. Per-query formatter (``_fmt_query_result`` + helpers)
2. Per-candidate (``IndividualSummary``, ``build_individual_summary``,
   ``fmt_individual_header``, ``format_elimination_summary``,
   ``fmt_pp_override``)
3. Per-round (``render_progress_table``, ``render_round_stats``,
   ``render_patience_status``)
4. tqdm bar lifecycle helper (``_BarTracker``)
5. ``LiveDisplay`` — the listener class.

``render_progress_table`` is also called by post-hoc CLI ``show-results``;
the rest is live-only.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from promptpotter.application.optimization.diagnostics import classify_result
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.phases import CampaignPhase, PhaseEvent
from promptpotter.domain.scoring import extract_display_answer
from promptpotter.presentation.views.composite_render import (
    compact_display_enabled,
    render_composite_block,
    render_composite_inline,
)
from promptpotter.presentation.views.display_primitives import (
    _DISPLAY_TAGS,
    CYAN,
    DIM,
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
)
from promptpotter.presentation.views.formatting import fmt_ci, fmt_pvalue
from promptpotter.presentation.views.phase_events import render_phase_event
from promptpotter.shared.errors import is_error_result
from promptpotter.shared.statistics import wilson_ci

if TYPE_CHECKING:
    from promptpotter.application.optimization.results import RoundResult
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.infrastructure.store import Stores


# --- 1. Per-query result formatter ----------------------------------------


def _infer_terminated_step(step_timings: dict) -> str | None:
    """Infer last executed step from timing dict (insertion-order fallback)."""
    last = None
    for name, t in step_timings.items():
        if t is not None:
            last = name
    return last


def _find_gt_rank(r: dict) -> int | None:
    """Find ground truth rank in candidates. Returns 1-indexed rank or None."""
    from promptpotter.application.scoring.metrics import find_rank

    gt = r.get("ground_truth", "")
    if not gt:
        return None
    pd = r.get("pipeline_data") or {}
    for key in ("ranked_candidates", "token_matched_candidates"):
        rank = find_rank(pd.get(key, []), gt)
        if rank is not None:
            return rank
    return None


def _append_annotation(line: str, indent: str, color: str, emoji: str, text: str) -> str:
    """Append a per-query annotation line under the query it describes.

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
    raw_pred = r.get("predicted") or ""
    pred = extract_display_answer(raw_pred, scoring_formula)[:30]
    gt = (r.get("ground_truth") or "").strip()[:30]
    q = ((r.get("query") or "").replace("\n", " ").strip())[:15]
    err = r.get("error") or ("pipeline error" if is_error_result(r) else None)
    pd = r.get("pipeline_data") or {}
    step_name = pd.get("terminated_at")
    if step_name is None:
        st = pd.get("step_timings")
        if st:
            step_name = _infer_terminated_step(st)
    step = _step_tag(step_name)

    tt = pd.get("total_time")

    if classify_result(r).is_fatal:
        tag = "DEPR"
    elif r.get("hit"):
        tag = "HIT"
    else:
        ranked = pd.get("ranked_candidates", [])
        n_cand = len(ranked)
        gt_rank = _find_gt_rank(r)
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
    single_node = len(_DISPLAY_TAGS) == 1
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
                tag_name = _DISPLAY_TAGS.get(node_name, node_name[:4])
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


# --- 2. Per-candidate renderers -------------------------------------------


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
    """One-line pre-scoring header: ``cand k/N  <mutation>`` or ``cand k/N  parent re-eval``.

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


def format_elimination_summary(ctx: dict, prior_label: str | None = None) -> str:
    """One-line eliminated-candidate summary.

    Example: ``✂ eliminated q7/15  p=0.023 *  vs C3 (of 3 priors)``.
    ``prior_label`` is the resolved label of the prior that triggered the
    Wilcoxon rejection; falls back to the integer index in ctx when absent.
    """
    q = int(ctx.get("queries_evaluated", 0))
    qt = int(ctx.get("total_queries", 0))
    p = float(ctx.get("triggered_p", 1.0))
    n_priors = int(ctx.get("n_priors", 0))

    if prior_label is None:
        idx = ctx.get("triggered_by_prior_idx", ctx.get("triggered_by_prior", -1))
        prior_label = f"prior #{idx}" if isinstance(idx, int) and idx >= 0 else "prior"

    return (
        f"{YELLOW}✂ eliminated q{q}/{qt}{RESET}  "
        f"{fmt_pvalue(p)}  vs {prior_label} (of {n_priors} prior{'s' if n_priors != 1 else ''})"
    )


@dataclass(frozen=True)
class IndividualSummary:
    """Structured candidate render — displays pick plain vs box wrapping.

    Pieces the two displays can compose independently:

    - ``tag`` — compact status slot (``80.0% [77-82%]`` or ``INVALID``);
      notebook puts it on the top-right of the box, CLI appends it to
      ``cand k/N``.
    - ``body_line`` — the mutations + hits + delta line; the notebook
      renders it as an inner box line, the CLI as an indented second line.
    - ``detail_lines`` — ordered extras (elimination summary, optional
      compact-mode composite + degraded count, or validation-failure
      entries); rendered inline by both displays, with the notebook
      folding the last entry into the bottom info rule when possible.
    - ``composite_lines`` — full-mode composite block (composite value +
      formula text + named-evaluator pairs). Empty in compact mode and
      when no formula is resolvable. When non-empty, callers render
      these as ordinary box lines (no bottom-info-rule fold).
    """

    status: Literal["ok", "invalid", "aborted", "eliminated"]
    tag: str
    body_line: str
    detail_lines: tuple[str, ...]
    composite_lines: tuple[str, ...] = ()


def _fmt_validation_failure_lines(failures: list[dict]) -> tuple[str, ...]:
    """Render one '⚠ axis = value ∉ allowed' + '↳ scored 0' pair per failure."""
    out: list[str] = []
    for vf in failures:
        axis = vf.get("axis", "?")
        value = vf.get("value", "?")
        allowed = vf.get("allowed") or []
        allowed_str = ", ".join(allowed[:3]) + (
            f" (+{len(allowed) - 3})" if len(allowed) > 3 else ""
        )
        out.append(f"{YELLOW}⚠{RESET} {axis} = {value!r}  ∉ [{allowed_str}]")
        out.append("  ↳ scored 0 (no backend call); L2 directive will name this value")
    return tuple(out)


def build_individual_summary(
    scores: dict,
    baseline_acc: float,
    *,
    formula: str | None = None,
) -> IndividualSummary:
    """Classify a candidate score report and pre-format all display pieces.

    Single source of truth for what the CLI and notebook show per candidate.
    Invalid > aborted > eliminated > ok precedence matches the report's
    exclusive flag semantics (invalid never runs the backend; aborted and
    eliminated are mutually exclusive by construction in
    ``_handle_scored_candidate``).

    *formula* is the active per-round formula string — when provided and
    ``PROMPTPOTTER_COMPACT_DISPLAY`` is unset, drives a multi-line
    composite block (composite value + formula + named evaluator pairs)
    on ``composite_lines``. In compact mode (env-var set) or when
    *formula* is None, falls back to the legacy single-line
    ``composite=0.4f`` bottom rule shown only when composite ≠ accuracy.
    """
    mutations = fmt_pp_override(scores.get("pipeline_params_override"))
    mutations_chunk = f"{CYAN}{mutations}{RESET}  " if mutations else ""

    if scores.get("invalid"):
        failures = scores["validation_failures"]
        return IndividualSummary(
            status="invalid",
            tag=f"{YELLOW}INVALID{RESET}",
            body_line="",
            detail_lines=_fmt_validation_failure_lines(failures),
        )

    acc = scores["accuracy"]
    hits = scores.get("hits", 0)
    n = scores.get("total", 0)
    ci_lo, ci_hi = wilson_ci(hits, n)
    delta = acc - baseline_acc
    tag = f"{acc:.1%} {fmt_ci(ci_lo, ci_hi)}"

    aborted = bool(scores.get("escalation_aborted"))
    if aborted:
        scored_q = scores.get("scored_queries", n)
        expected_q = scores.get("expected_queries", n)
        hit_str = f"{hits}/{scored_q} hits {YELLOW}⚠ aborted {scored_q}/{expected_q}{RESET}"
    else:
        hit_str = f"{hits}/{n} hits"
    body_line = f"{mutations_chunk}{hit_str}  vs baseline: {_fmt_delta(delta)}"

    detail_lines: list[str] = []
    if scores.get("elimination_stopped"):
        elim_ctx = scores["elimination_context"]
        prior_label = elim_ctx.get("triggered_by_prior_label")
        detail_lines.append(format_elimination_summary(elim_ctx, prior_label))

    comp = scores.get("composite")
    degraded = scores.get("degraded_queries", 0)

    # Two render modes:
    #   - compact (env var set, or no formula): legacy single-line bottom
    #     rule, ``composite=...  ⚠ K/N degraded``, only when comp ≠ acc.
    #   - full: composite_lines carry the multi-line block (composite +
    #     formula + named evaluators). Degraded gets its own detail line
    #     so the box bottom is a plain rule.
    composite_lines: tuple[str, ...] = ()
    if comp is not None and not compact_display_enabled() and formula:
        # Box wraps content at width-4 (two walls + two-space pad). Per-
        # candidate boxes are 66 wide → 62 chars content room.
        block = render_composite_block(comp, scores.get("evaluators"), formula, width=62)
        composite_lines = tuple(block)
        if degraded:
            detail_lines.append(f"{YELLOW}⚠ {degraded}/{n} degraded{RESET}")
    else:
        bottom_extras: list[str] = []
        if comp is not None and comp != acc:
            bottom_extras.append(render_composite_inline(comp))
        if degraded:
            bottom_extras.append(f"{YELLOW}⚠ {degraded}/{n} degraded{RESET}")
        if bottom_extras:
            detail_lines.append("  ".join(bottom_extras))

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
        composite_lines=composite_lines,
    )


# --- 3. Round-summary renderers -------------------------------------------


def render_progress_table(
    rounds: list[dict],
    window: int = 8,
    *,
    framed: bool = True,
) -> str:
    """Round-over-round trajectory table: accuracy, composite, rolling avg, trend, plateau.

    Items in ``rounds`` must have at minimum ``round`` and ``accuracy``.
    The ``Composite`` column is always shown so the operator never has to
    wonder whether composite was hidden because it equalled accuracy on
    every round so far.

    ``framed=True`` (default) wraps each line in ``_node_line()`` for
    box-drawing terminals (live CLI / notebook live view). ``framed=False``
    emits plain text with a ``PROGRESS`` title for ``show-results`` and
    other plain-text reports.
    """
    if not rounds:
        return "" if framed else "No rounds to display."

    def wrap(s: str) -> str:
        return _node_line(s) if framed else s

    row_rnd_w = 5 if framed else 7
    row_comp_w = 9 if framed else 10
    trend_w = 8 if framed else 10
    plateau_step = "+0.0%  <-- plateau" if framed else "+0.0% plateau"

    header = (
        f"{'Round':<7s} {'Accuracy':>9s} {'Composite':>10s}"
        f" {'Rolling Avg':>13s} {'Trend':>{trend_w}s}"
    )

    lines: list[str] = []
    if framed:
        lines.append(wrap(header))
    else:
        lines.append("\nPROGRESS")
        lines.append(f"\n  {header}")

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
                trend = plateau_step
            elif d > 0:
                trend = f"+{d:.1%}"
            else:
                trend = f"{d:.1%}"
        rl = "G" if rd.get("round") == "grid" else str(rd.get("round", "?"))
        comp = rd.get("composite") if rd.get("composite") is not None else acc
        row = f"  {rl:<{row_rnd_w}s} {acc:>8.1%} {comp:>{row_comp_w}.4f} {rolling:>12.1%}  {trend}"
        lines.append(wrap(row))

    if len(accs) >= 3:
        recent_avg = sum(accs[-3:]) / 3
        if all(abs(a - recent_avg) < 0.005 for a in accs[-3:]):
            if framed:
                lines.append(
                    wrap(
                        f"{YELLOW}-- Plateau: rolling avg stable at"
                        f" {recent_avg:.1%} for 3 rounds{RESET}"
                    )
                )
            else:
                lines.append(f"  -- Plateau: rolling avg stable at {recent_avg:.1%} for 3 rounds")

    if framed:
        lines.append(wrap(""))
    return "\n".join(lines)


def render_round_stats(
    round_result: RoundResult,
    pipeline_schema: PipelineSchema | None,
) -> str:
    """hits/total, candidate count, pipeline terminations, degradation%, recall@1/5.

    Best-effort: the pipeline-stats block is wrapped in try/except and
    returns an empty string when ``round_result.results`` is empty.
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

        from promptpotter.application.optimization.utils import (
            candidate_keys_from_schema,
            get_candidates,
        )
        from promptpotter.application.scoring.metrics import find_rank

        candidate_keys = candidate_keys_from_schema(pipeline_schema)
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
                        get_candidates(r, candidate_keys),
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


def render_patience_status(
    improved: bool,
    l1_stall_count: int,
    l1_patience: int,
) -> str:
    """Green tick on improvement; yellow patience counter; red stop on exhaustion."""
    lines: list[str] = []
    if improved:
        lines.append(_node_line(f"{GREEN}✓ Improvement detected, auto-continuing...{RESET}"))
        return "\n".join(lines)
    lines.append(
        _node_line(f"{YELLOW}⚠ No improvement ({l1_stall_count}/{l1_patience} patience){RESET}")
    )
    if l1_stall_count >= l1_patience:
        lines.append(
            _node_line(
                f"{RED}Stopping: patience exhausted ({l1_patience} consecutive stalls){RESET}"
            )
        )
    return "\n".join(lines)


# --- 4. tqdm bar lifecycle helper -----------------------------------------


class _BarTracker:
    """tqdm bar lifecycle driven by ``RunListener`` events. Optional helper."""

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


# --- 5. LiveDisplay -------------------------------------------------------


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
        # Phase-view ctx accumulator. Initialised empty here; ``RunListener``
        # overwrites this attribute at construction with its own shared dict
        # so emitter + display read the same state machine.
        self._phase_ctx: dict[str, Any] = {}
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
        self._phase_ctx["baseline_accuracy"] = fresh

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
        self._round_num = self._phase_ctx.get("round_num", self._round_num)
        if event.phase == CampaignPhase.ESCALATION and event.event == "exit":
            self.query_counter = 0
        if (
            event.phase == CampaignPhase.INIT
            and event.event == "exit"
            and event.data["env"].resumed_from_round > 0
        ):
            del self.campaign_rounds[self.initial_len :]
        # Mirror an interactive-steer formula swap onto the shared phase
        # ctx so the next round's renderers print the new formula.
        if event.phase == "scoring_steer" and event.event == "applied":
            new_formula = event.data.get("formula")
            if new_formula:
                self._phase_ctx["composite_formula"] = new_formula

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
        baseline_acc = self._phase_ctx.get("baseline_accuracy", self.baseline_acc)
        formula = self._phase_ctx.get("composite_formula")
        summary = build_individual_summary(scores, baseline_acc, formula=formula)

        self._write(f"  {_box_top(f'{label}/{total}', summary.tag, width=w)}")
        if summary.body_line:
            self._write(f"  {_box_line(summary.body_line, width=w)}")
        # Full mode (composite_lines populated): all detail_lines render as
        # plain box lines, then composite block as box lines, then a flat
        # bottom rule. Compact mode keeps the legacy "fold last detail onto
        # bottom-info" rule.
        if summary.composite_lines:
            for line in summary.detail_lines:
                self._write(f"  {_box_line(line, width=w)}")
            for line in summary.composite_lines:
                self._write(f"  {_box_line(line, width=w)}")
            self._write(f"  {_box_bottom(width=w)}")
        else:
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
        self._write("")
        self._write(_node_top(f"ROUND {rn} SUMMARY"))
        for line in render_progress_table(self.campaign_rounds).split("\n"):
            self._write(line)
        # Composite block — full mode only. The per-round formula + the
        # winner's per-evaluator values, so the operator sees what drove
        # the round's composite without opening the trial JSON.
        formula = self._phase_ctx.get("composite_formula")
        if formula and not compact_display_enabled():
            for line in render_composite_block(
                round_result.composite,
                dict(round_result.evaluators),
                formula,
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
