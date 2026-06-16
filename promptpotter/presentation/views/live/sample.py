"""Per-sample HIT/MISS line formatter (``LiveDisplay.on_sample_scored``). Pure."""

from __future__ import annotations

from typing import Any

from promptpotter.domain.rendering import classify_result, extract_display_answer
from promptpotter.presentation.views.display import (
    DIM,
    DISPLAY_TAGS,
    RED,
    RESET,
    YELLOW,
    _step_tag,
)
from promptpotter.shared.errors import is_error_result


def _ellide(s: str, n: int) -> str:
    """Truncate to *n* chars; append ``…`` when cut."""
    return s if len(s) <= n else s[: n - 1] + "…"


def _append_annotation(line: str, indent: str, color: str, emoji: str, text: str) -> str:
    """Append an annotation line under the query; indent mirrors the query's leading whitespace."""
    return line + f"\n{indent}{color}{emoji} {text}{RESET}"


def fmt_query_result(
    r: dict[str, Any],
    cached: bool = False,
    *,
    prefix: str = "",
    scoring_formula: str | None = None,
) -> str:
    """Format a single query result as a HIT/MISS line with timing.

    *scoring_formula* (from ``campaign_config["scoring"]``) routes ``predicted``
    through ``extract_display_answer`` so ``**bold**`` / ``\\boxed{…}`` collapse to one token.
    """
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
        # Read the rank + candidate count stamped at scoring time
        # (``sample_measurement.py`` — computed once from the canonical
        # ``result_ranking``); never re-derive here. The old re-derivation read
        # ``ranked_items``/``token_matches`` — keys this pipeline never emits (it
        # stamps ``result_ranking``/``candidate_ranking``) — so every MISS rendered
        # ``--/0`` even when the GT was genuinely ranked (e.g. rank 20 of 20).
        gt_rank = r.get("ground_truth_rank")
        n_cand = r.get("n_candidates") or 0
        if gt_rank is not None:
            tag = f"MISS {gt_rank}/{n_cand}"
        elif n_cand:
            tag = f"MISS --/{n_cand}"
        else:
            tag = "MISS"

    cache_marker = "\U0001f4d6" if cached else ""

    # Per-LLM-node token column: `[tag] io=N/M`; `~` prefix = chars/4 estimate.
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
            # The backend owns the warning's human string (``message``); display
            # it verbatim rather than re-deriving from ``stats`` (that duplicated
            # the format across the repo boundary — a key rename on one side broke
            # the other). Fall back to the step/code if a message is ever absent.
            msg = w.get("message") or w.get("code") or w.get("step", "warning")
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
    elif r.get("config_fundamental_skip"):
        line = _append_annotation(
            line,
            _ann_indent,
            RED,
            "⚠",
            "cached failure was token-budget exhaustion + rerun max_tokens "
            "≤ cached output → skipped LLM rerun (would repeat); marked fatal",
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
        # Skip the "toward rerun" annotation on fatal classifications — candidate is already dead.
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


__all__ = ["fmt_query_result"]
