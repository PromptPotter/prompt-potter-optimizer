from __future__ import annotations

from typing import TYPE_CHECKING

from promptpotter.application.views.render.primitives import (
    DIM,
    RED,
    RESET,
    YELLOW,
    _step_tag,
)
from promptpotter.domain.dashboard_rows import sample_status
from promptpotter.domain.l4.inner_facts import series_reason_head
from promptpotter.domain.l4.proxies import inner_facts_of, outer_proxies_of
from promptpotter.domain.results_health import is_deprecated, terminal_node
from promptpotter.domain.sample import sample_tag
from promptpotter.domain.scoring import StaleOutcome, graded_answer

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


def _miss_tag(grade: Grade) -> str:
    if grade.field_hits is not None:
        return "MISS " + ",".join(name for name, hit in grade.field_hits if not hit)
    gt_rank = grade.ground_truth_rank
    n_cand = grade.n_candidates or 0
    if gt_rank is not None:
        return f"MISS {gt_rank}/{n_cand}"
    return f"MISS --/{n_cand}" if n_cand else "MISS"


def fmt_query_result(
    position: int,
    facts: MeasuredCell,
    grade: Grade,
    *,
    prefix: str = "",
    display_tags: dict[str, str],
) -> str:
    cached = facts.cached
    # One line is the CONTRACT: a multi-line answer splits the row of every one-line readout.
    pred = _ellide(_one_line(graded_answer(facts, grade) or ""), 30)
    gt = _ellide(facts.ground_truth.strip(), 30)
    q = _ellide(facts.query.replace("\n", " ").strip(), 15)
    pd = facts.pipeline
    step = _step_tag(terminal_node(facts), display_tags)

    tt = facts.shown_s

    status = sample_status(facts, grade)
    if status == "ERR":
        tag: str = status
    elif is_deprecated(facts):
        tag = "DEPR"
    elif status == "MISS":
        tag = _miss_tag(grade)
    else:
        tag = status

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
    sid_col = sample_tag(position)
    step_block = f" {step}" if step else ""
    if status == "ERR":
        err = facts.error or "pipeline error"
        return f"{indent}{time_col} {sid_col} {tag}{step_block}{tok_col} {err[:40]!r} gt:{gt!r} q:{q!r}"

    # An L4 outer sample never reaches the HIT threshold and its ground truth is a placeholder.
    # The connector's report rides the ledger, so a live tape and its replay read one line.
    if (scored := outer_proxies_of(pd)) is not None and (inner := inner_facts_of(pd)) is not None:
        fit_col = f" fit {grade.fitness:.2f}" if grade.fitness is not None else ""
        lift = inner.mean_round_delta
        delta_col = " θ Δ —" if lift is None else f" θ Δ {lift:+.3f} logits"
        line = (
            f"{indent}{time_col} {sid_col} held-out lift {scored.held_out_lift * 100:+.1f} acc pts"
            f"{delta_col}{fit_col}{step_block}{tok_col} q:{q!r}"
        )
        if inner.series_reason is None:
            return line
        return _append_annotation(
            line,
            " " * len(indent) or "      ",
            DIM,
            "Δ",
            f"withheld: {series_reason_head(inner.series_reason)}",
        )

    # A verifier-graded cell has no label: `predicted` is the `NO_RESULT` sentinel.
    if facts.verifier_graded:
        return f"{indent}{time_col} {sid_col} {tag}{step_block}{tok_col} q:{q!r}"

    line = f"{indent}{time_col} {sid_col} {tag}{step_block}{tok_col} -> {pred!r} gt:{gt!r} q:{q!r}"

    _ann_indent = " " * len(indent) if indent else "      "

    for w in pd.diagnostics.warnings:
        msg = w.message or w.code or w.step or "warning"
        line += f"\n{_ann_indent}{YELLOW}⚠ {w.step}: {msg}{RESET}"

    match facts.stale_outcome:
        # An exhausted ladder reran first, and the tape reads its rerun.
        case StaleOutcome.RETRIED | StaleOutcome.PERSISTENTLY_DEGRADED:
            line = _append_annotation(
                line,
                _ann_indent,
                YELLOW,
                "\U0001f504",
                f"cache had pipeline warnings → reran{_rerun_detail(facts)}",
            )
        case StaleOutcome.SWITCHED_OUT:
            line = _append_annotation(
                line,
                _ann_indent,
                YELLOW,
                "\U0001f500",
                "query degrades ≥50% of the time historically → using cached answer "
                "(resampling would likely degrade again)",
            )
        case StaleOutcome.CONFIG_SKIP:
            line = _append_annotation(
                line,
                _ann_indent,
                RED,
                "⚠",
                "cached failure was token-budget exhaustion + rerun max_tokens "
                "≤ cached output → skipped LLM rerun (would repeat); marked fatal",
            )
        case StaleOutcome.PAUSED:
            line = _append_annotation(
                line,
                _ann_indent,
                DIM,
                "⏸",
                f"pause requested mid stale-data ladder → kept as it stood{_rerun_detail(facts)}",
            )
        case StaleOutcome.OBSERVED if not is_deprecated(facts):
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


def _rerun_detail(facts: MeasuredCell) -> str:
    comp = facts.rerun_comparison
    detail = f"; result: {comp['hit_change']}" if comp and comp["hit_change"] else ""
    if comp and comp["rank_change"]:
        detail += f" (rank {comp['rank_change']})"
    return detail
