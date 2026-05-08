"""Per-candidate + per-sample renderers shared by the CLI and notebook surfaces.

- ``IndividualSummary`` + ``build_individual_summary`` / ``fmt_individual_header``
  produce the per-candidate pre-scoring header + post-scoring summary.
- ``_fmt_query_result`` formats a single HIT/MISS query line plus
  diagnostic annotations for ``LiveDisplay.on_sample_scored``.

Round-summary renderers (progress table, round stats, patience banner)
live in ``live.py`` because they have a single caller there.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from promptpotter.application.optimization.elimination import classify_result
from promptpotter.application.scoring.formula import extract_display_answer
from promptpotter.presentation.views.display import (
    CYAN,
    DIM,
    DISPLAY_TAGS,
    GREEN,
    RED,
    RESET,
    YELLOW,
    _fmt_delta,
    _pp_val,
    _step_tag,
    fmt_ci,
    fmt_pvalue,
)
from promptpotter.shared.composite import (
    render_composite_fitness_oneliner,
)
from promptpotter.shared.errors import is_error_result
from promptpotter.shared.statistics import wilson_ci


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


def build_individual_summary(
    scores: dict,
    baseline_acc: float,
    *,
    baseline_composite_fitness: float | None = None,
) -> IndividualSummary:
    """Classify a candidate score report and pre-format all display pieces.

    Single source of truth for what the CLI and notebook show per candidate.
    Invalid > aborted > eliminated > ok precedence matches the report's
    exclusive flag semantics (invalid never runs the backend; aborted and
    eliminated are mutually exclusive by construction in
    ``_handle_scored_candidate``).

    Per-candidate composite_fitness render is intentionally 1 line —
    ``composite_fitness=0.6042  (Δ+0.103 vs baseline 0.5012)`` — so 5 candidates
    don't dump 60 lines of identical formula text into the terminal. The
    formula + per-evaluator breakdown lands once per round in the round
    summary block.

    *baseline_composite_fitness* anchors the Δ against the campaign's first-round
    composite_fitness — even at deep rounds the operator sees how far the run
    has come from origin. ``None`` collapses to the no-Δ form.
    """
    mutations = fmt_pp_override(scores.get("pipeline_params_override"))
    mutations_chunk = f"{CYAN}{mutations}{RESET}  " if mutations else ""

    if scores.get("invalid"):
        # One '⚠ axis = value ∉ allowed' + '↳ scored 0' pair per failure.
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
    delta = acc - baseline_acc
    tag = f"{acc:.1%} {fmt_ci(ci_lo, ci_hi)}"

    aborted = bool(scores.get("escalation_aborted"))
    if aborted:
        scored_q = scores.get("scored_samples", n)
        expected_q = scores.get("expected_samples", n)
        hit_str = f"{hits}/{scored_q} hits {YELLOW}⚠ aborted {scored_q}/{expected_q}{RESET}"
    else:
        hit_str = f"{hits}/{n} hits"
    body_line = f"{mutations_chunk}{hit_str}  vs baseline: {_fmt_delta(delta)}"

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
            render_composite_fitness_oneliner(comp, baseline=baseline_composite_fitness)
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
# Per-sample HIT/MISS line formatter — feeds ``LiveDisplay.on_sample_scored``.
# Pure: no I/O, no mutation.
# ===========================================================================


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
