from __future__ import annotations

from typing import TYPE_CHECKING, Any

from promptpotter.application.views.render.primitives import (
    DIM,
    RED,
    RESET,
    YELLOW,
    _step_tag,
)
from promptpotter.domain.results_health import is_deprecated, terminal_node
from promptpotter.domain.scoring import is_hit
from promptpotter.shared.answer_text import (
    extract_boxed_number,
    extract_gsm8k_number,
    extract_last_bold,
    text_list_items,
)

if TYPE_CHECKING:
    from promptpotter.domain.scoring import Grade, MeasuredCell


def _ellide(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def _append_annotation(line: str, indent: str, color: str, emoji: str, text: str) -> str:
    return line + f"\n{indent}{color}{emoji} {text}{RESET}"


def _count_or_unknown(count: int | None) -> str:
    return "?" if count is None else str(count)


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _extract_gsm8k_display(text: str) -> str:
    n = extract_gsm8k_number(text or "")
    if n is None:
        return (text or "").strip()
    return str(int(n)) if n.is_integer() else str(n)


def _extract_boxed_display(text: str) -> str:
    # The shared AIME extractor, so the shown answer IS the value `_aime_match` matched.
    n = extract_boxed_number(text or "")
    if n is None:
        return (text or "").strip()
    return str(int(n)) if n.is_integer() else str(n)


def _extract_list_display(text: str) -> str:
    items = text_list_items(text or "")
    return " | ".join(items) if items else (text or "")


DISPLAY_EXTRACTORS: dict[str, Any] = {
    "label_match": extract_last_bold,
    "gsm8k_match": _extract_gsm8k_display,
    "aime_match": _extract_boxed_display,
    "list_rr": _extract_list_display,
}


def extract_display_answer(predicted: str, formula: str | None) -> str:
    """Single-line is the CONTRACT: a multi-line answer splits the row of every one-line readout."""
    text = predicted or ""
    if formula:
        for name, extractor in DISPLAY_EXTRACTORS.items():
            if name in formula:
                return _one_line(str(extractor(text)))
    return _one_line(text)


def fmt_query_result(
    facts: MeasuredCell,
    grade: Grade,
    *,
    prefix: str = "",
    scoring_formula: str | None = None,
    display_tags: dict[str, str],
) -> str:
    cached = facts.cached
    pred = _ellide(extract_display_answer(facts.predicted, scoring_formula), 30)
    gt = _ellide(facts.ground_truth.strip(), 30)
    q = _ellide(facts.query.replace("\n", " ").strip(), 15)
    err = facts.error or ("pipeline error" if facts.errored else None)
    pd = facts.pipeline
    step = _step_tag(terminal_node(facts), display_tags)

    tt = facts.shown_s

    if err:
        # Asked FIRST: an errored row carries no ``fitness``, which the MISS ladder reads as a grade.
        tag = "ERR"
    elif is_deprecated(facts):
        tag = "DEPR"
    elif grade.unscored is not None:
        # Before the hit ladder: MISS would report the FORMULA's silence as the arm's failure.
        tag = "UNSC"
    elif is_hit(grade.fitness):
        tag = "HIT"
    else:
        gt_rank = facts.ground_truth_rank
        n_cand = facts.n_candidates or 0
        if gt_rank is not None:
            tag = f"MISS {gt_rank}/{n_cand}"
        elif n_cand:
            tag = f"MISS --/{n_cand}"
        else:
            tag = "MISS"

    cache_marker = "\U0001f4d6" if cached else ""

    single_node = len(display_tags) == 1
    tok_col = ""
    if pd.step_tokens:
        groups = []
        for node_name, usage in pd.step_tokens.items():
            mark = "~" if usage.estimated else ""
            io_seg = f"io={mark}{usage.input}/{mark}{usage.output}"
            if badge := usage.account.prefix(replayed=cached).badge:
                io_seg += f" {badge}"
            if single_node:
                groups.append(io_seg)
            else:
                tag_name = display_tags.get(node_name, node_name[:4])
                groups.append(f"[{tag_name}] {io_seg}")
        tok_col = " " + " ".join(groups)

    step = "" if (single_node and not cached) else f"{step}{cache_marker}"

    indent = prefix if prefix else ""
    if facts.retry_of_deprecated_cache:
        indent = f"{indent}\U0001f504 "

    time_col = f"{tt:5.1f}s" if tt is not None else "     "
    sid_col = f"#{facts.sample_id:03d}"
    step_block = f" {step}" if step else ""
    if err:
        return f"{indent}{time_col} {sid_col} {tag}{step_block}{tok_col} {str(err)[:40]!r} gt:{gt!r} q:{q!r}"

    # An L4 outer sample never reaches the HIT threshold and its ground truth is a placeholder.
    proxy_delta = pd.mean_round_delta
    if proxy_delta is not None:
        fit_col = f" fit {grade.fitness:.2f}" if grade.fitness is not None else ""
        return (
            f"{indent}{time_col} {sid_col} Δ{proxy_delta:+.3f}{fit_col}"
            f"{step_block}{tok_col} q:{q!r}"
        )

    # A verifier-graded cell has no label: `predicted` is the `NO_RESULT` sentinel.
    if facts.verifier_graded:
        return f"{indent}{time_col} {sid_col} {tag}{step_block}{tok_col} q:{q!r}"

    line = f"{indent}{time_col} {sid_col} {tag}{step_block}{tok_col} -> {pred!r} gt:{gt!r} q:{q!r}"

    _ann_indent = " " * len(indent) if indent else "      "

    for w in pd.diagnostics.warnings:
        msg = w.message or w.code or w.step or "warning"
        line += f"\n{_ann_indent}{YELLOW}⚠ {w.step}: {msg}{RESET}"

    if facts.retry_of_degraded:
        comp = facts.rerun_comparison
        detail = f"; result: {comp['hit_change']}" if comp and comp["hit_change"] else ""
        if comp and comp["rank_change"]:
            detail += f" (rank {comp['rank_change']})"
        line = _append_annotation(
            line,
            _ann_indent,
            YELLOW,
            "\U0001f504",
            f"cache had pipeline warnings → reran{detail}",
        )
    elif facts.switched_out:
        line = _append_annotation(
            line,
            _ann_indent,
            YELLOW,
            "\U0001f500",
            "query degrades ≥50% of the time historically → using cached answer "
            "(resampling would likely degrade again)",
        )
    elif facts.config_fundamental_skip:
        line = _append_annotation(
            line,
            _ann_indent,
            RED,
            "⚠",
            "cached failure was token-budget exhaustion + rerun max_tokens "
            "≤ cached output → skipped LLM rerun (would repeat); marked fatal",
        )
    elif facts.persistently_degraded:
        line = _append_annotation(
            line,
            _ann_indent,
            RED,
            "⚠",
            "entire stale-data ladder exhausted → still degraded; "
            "score counts but flag this candidate",
        )
    elif facts.degraded_observed and not is_deprecated(facts):
        obs = _count_or_unknown(facts.degraded_obs_count)
        threshold = _count_or_unknown(facts.degraded_obs_threshold)
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
