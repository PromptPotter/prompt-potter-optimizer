"""Per-candidate + round-summary renderers — single module so per-round display surfaces share imports.

Two related sections:

1. **Per-candidate (individual) renderers** — ``IndividualSummary`` is the
   structured output ``LiveDisplay`` and the notebook's box renderer both
   compose into their respective layouts. ``build_individual_summary`` /
   ``fmt_individual_header`` produce pre-scoring header + post-scoring summary.

2. **Round-summary renderers** — ``render_progress_table`` (round-over-round
   trajectory), ``render_round_stats`` (per-round hits / pipeline / recall),
   ``render_patience_status`` (patience banner).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from promptpotter.application.optimization.elimination import classify_result
from promptpotter.application.scoring.formula import extract_display_answer
from promptpotter.presentation.views.display import (
    _DISPLAY_TAGS,
    CYAN,
    DIM,
    GREEN,
    RED,
    RESET,
    YELLOW,
    _fmt_delta,
    _node_line,
    _pp_val,
    _step_tag,
    fmt_ci,
    fmt_pvalue,
)
from promptpotter.shared.composite import (
    compact_display_enabled,
    render_composite_inline,
    render_composite_oneliner,
)
from promptpotter.shared.errors import is_error_result
from promptpotter.shared.statistics import wilson_ci

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.results import RoundResult


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
    q = int(ctx.get("queries_scored", 0))
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


def format_leader_locked_summary(ctx: dict) -> str:
    """One-line leader-locked-candidate summary.

    Example: ``✓ leader locked q8/20  p_best=98.5% (of 3 priors)``.
    Fired when PoBB confirms the current candidate is the round leader past
    the lock-in threshold and the round terminates early.
    """
    q = int(ctx.get("queries_scored", 0))
    qt = int(ctx.get("total_queries", 0))
    p_best = float(ctx.get("p_best", 0.0))
    n_priors = int(ctx.get("n_priors", 0))
    return (
        f"{GREEN}✓ leader locked q{q}/{qt}{RESET}  "
        f"p_best={p_best:.1%} (of {n_priors} prior{'s' if n_priors != 1 else ''})"
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
    - ``detail_lines`` — ordered extras (elimination summary, the 1-line
      composite-with-Δ render, degraded-count tag, or validation-failure
      entries); rendered inline by both displays, with the last entry
      folded onto the bottom info rule by callers that support it.
    """

    status: Literal["ok", "invalid", "aborted", "eliminated"]
    tag: str
    body_line: str
    detail_lines: tuple[str, ...]


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
    baseline_composite: float | None = None,
) -> IndividualSummary:
    """Classify a candidate score report and pre-format all display pieces.

    Single source of truth for what the CLI and notebook show per candidate.
    Invalid > aborted > eliminated > ok precedence matches the report's
    exclusive flag semantics (invalid never runs the backend; aborted and
    eliminated are mutually exclusive by construction in
    ``_handle_scored_candidate``).

    Per-candidate composite render is intentionally 1 line —
    ``composite=0.6042  (Δ+0.103 vs baseline 0.5012)`` — so 5 candidates
    don't dump 60 lines of identical formula text into the terminal. The
    formula + per-evaluator breakdown lands once per round in the round
    summary block. ``PROMPTPOTTER_COMPACT_DISPLAY=1`` reverts to the
    legacy ``composite=0.4f`` bottom rule (only when composite ≠ accuracy).

    *baseline_composite* anchors the Δ against the campaign's first-round
    composite — even at deep rounds the operator sees how far the run
    has come from origin. ``None`` collapses to the no-Δ form.
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
    elim_ctx_raw = scores.get("elimination_context") or {}
    if scores.get("elimination_stopped"):
        prior_label = elim_ctx_raw.get("triggered_by_prior_label")
        detail_lines.append(format_elimination_summary(elim_ctx_raw, prior_label))
    elif elim_ctx_raw.get("leader_locked"):
        detail_lines.append(format_leader_locked_summary(elim_ctx_raw))

    comp = scores.get("composite")
    degraded = scores.get("degraded_queries", 0)

    # Two render modes:
    #   - compact (env var set): legacy single-line bottom rule,
    #     ``composite=...  ⚠ K/N degraded``, only when comp ≠ acc.
    #   - default: 1-line composite-with-Δ as a detail line; degraded
    #     count joins as a separate detail. Box bottom stays plain.
    if comp is not None and not compact_display_enabled():
        detail_lines.append(render_composite_oneliner(comp, baseline=baseline_composite))
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
    )


# ===========================================================================
# Per-query HIT/MISS line formatter — feeds ``LiveDisplay.on_sample_scored``.
# A single function (``_fmt_query_result``) produces one display line plus
# zero-or-more annotation lines (diagnostic warnings, stale-data ladder
# markers). Pure: no I/O, no mutation. Three private helpers carry it.
# ===========================================================================


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


# ===========================================================================
# Round-summary renderers — progress table, round stats, patience banner
# ===========================================================================


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
    emits plain text with a ``PROGRESS`` title for plain-text reports.
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

        from promptpotter.application.optimization.elimination import (
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
