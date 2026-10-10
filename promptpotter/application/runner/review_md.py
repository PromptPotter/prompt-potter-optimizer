from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, get_args

from promptpotter.application.optimizers.nodes import CheckResult, ReviewReading, ReviewStat
from promptpotter.application.views.render.optimizer_prompt_text import fmt_pct
from promptpotter.application.views.render.primitives import fmt_ci, fmt_fitness, overlap_series
from promptpotter.domain.bench import BandedValue, BenchColumn, BenchReading, BenchScore
from promptpotter.domain.paired_reading import LiftEstimate
from promptpotter.domain.phases import StopReason, stop_next_step
from promptpotter.domain.results import (
    CEILING_FRACTION,
    DegradationHealth,
    RoundClocks,
    RoundResult,
    ScoredCandidate,
    candidate_label,
    round_clocks,
)
from promptpotter.domain.spend import (
    RATE_PRICED_LABEL,
    SpendBucket,
    SpendRollup,
)

if TYPE_CHECKING:
    from promptpotter.application.optimizer_manifest import SelectedOptimizer
    from promptpotter.domain.cycle_listing import CycleIndex
    from promptpotter.domain.opt_search_point import OptSearchPoint
    from promptpotter.domain.round_audit import RoundAudit
    from promptpotter.domain.run_records import CycleFinal, WallClock

__all__ = ["render_review_md"]


def render_review_md(
    index: CycleIndex,
    rounds: list[RoundResult],
    *,
    round_audits: list[RoundAudit | None],
    context_object: list[str] | None = None,
    accuracy_ceiling: float | None,
    optimizer: SelectedOptimizer,
    bench: BenchScore | None,
    spend: SpendRollup,
) -> str:
    ctx_items = [c for c in (context_object or []) if isinstance(c, str) and c.strip()]

    final = index.final
    review = optimizer.runtime.review(
        optimizer,
        list(rounds),
        round_audits,
        context_object=ctx_items,
    )
    clock = final.wall_clock if final else None
    stats = review.stats if review is not None else None

    repairs_per_round = [_schema_repair_count(a) for a in round_audits]
    calls_per_round = [_optimizer_call_count(a) for a in round_audits]
    halt = _halt_info(index, rounds)
    parts: list[str] = []
    parts += _render_header(index, final, review.verdict if review is not None else None, halt)
    parts += _render_bench(final, bench)
    # Counted here, never read off `final`: this renders at every round close, before one is banked.
    clocks = round_clocks(rounds, accuracy_ceiling=accuracy_ceiling)
    parts += _render_stats_block(
        clocks,
        clock.round_ended_s if clock else {},
        stats,
        repairs_per_round,
        calls_per_round,
        halt,
        optimizer.name,
    )
    parts += _render_wall_clock(clock)
    parts += _render_spend(spend)
    parts += _render_behavior_summary(review)
    parts += ["## Rounds", ""]

    last_idx = len(rounds) - 1
    for i, round_data in enumerate(rounds):
        is_peek = i == last_idx and round_data.generation_only
        parts += _render_round(
            round_data,
            review,
            i,
            is_peek=is_peek,
            schema_repair_retries=repairs_per_round[i],
        )

    return "\n".join(parts).rstrip() + "\n"


def _schema_repair_count(audit: RoundAudit | None) -> int:
    if audit is None:
        return 0
    return sum(len(block.schema_repair_errors) for block in audit.nodes.values())


def _optimizer_call_count(audit: RoundAudit | None) -> int:
    return 0 if audit is None else len(audit.nodes)


def _stat(stat: ReviewStat) -> str:
    return "—" if stat.value is None else format(stat.value, stat.spec)


def _halt_info(index: CycleIndex, rounds: list[RoundResult]) -> dict[str, str] | None:
    """Gated on the cycle's TERMINAL state, never on a critical round L2 then healed away."""
    terminated = "yes" if index.stop_reason is StopReason.OPTIMIZER_ABORT else ""
    last_health: DegradationHealth | None = None
    last_critical: DegradationHealth | None = None
    for r in rounds:
        if r.health is not None:
            last_health = r.health
            if r.health.grade == "critical":
                last_critical = r.health
    ended_critical = last_health is not None and last_health.grade == "critical"
    if not ended_critical and not terminated:
        return None
    if last_critical is not None:
        tag = last_critical.cause or "critical"
        return {
            "tag": tag,
            "node": last_critical.dominant_node or "",
            "action": (last_critical.suggested_action or "").strip(),
            "terminated": terminated,
        }
    return {"tag": StopReason.OPTIMIZER_ABORT, "node": "", "action": "", "terminated": terminated}


def _render_header(
    index: CycleIndex,
    final: CycleFinal | None,
    verdict: ReviewStat | None,
    halt: dict[str, str] | None,
) -> list[str]:
    # The mode is banked when the cycle stops; a review rendered before then has none to name.
    said = [
        *([f"mode: **{final.mode}**"] if final else []),
        *([f"{verdict.name}: **{_stat(verdict)}**"] if verdict else []),
    ]
    parts: list[str] = [
        f"# Review — {index.cycle_id}",
        "",
        *([f"_{' · '.join(said)}_", ""] if said else []),
    ]
    if halt is not None:
        where = f" — node `{halt['node']}`" if halt["node"] else ""
        parts.append(f"> **HALTED — {halt['tag']}**{where}")
        if halt["action"]:
            parts.append(">")
            parts.append(f"> {halt['action']}")
        parts.append("")
    # Beside the halt block, not inside it: the endings most needing a next step are ones it calls clean.
    if next_step := stop_next_step(index.stop_reason):
        parts.append(f"> **NEXT** — {next_step}")
        parts.append("")
    if final and final.prompt_hashes:
        parts.append("**Prompt hashes**")
        parts.append("")
        parts += [
            f"- `{name}`: `{digest[:8]}`" for name, digest in sorted(final.prompt_hashes.items())
        ]
        parts.append("")
    return parts


def _bench_line(name: str, reading: BenchReading, bench: BenchScore) -> str:
    return (
        f"- {name} (round {reading.round}): {_bench_columns(reading.of, bench.headline, '{:.3f}')} · "
        f"{reading.n}/{bench.bench_size} rows"
    )


def _bench_columns(
    of: Callable[[BenchColumn], BandedValue | LiftEstimate | None],
    headline: BenchColumn,
    spec: str,
) -> str:
    def cell(column: BenchColumn) -> str:
        banded = of(column)
        if banded is None:
            return f"{column} —"
        value = spec.format(banded.value)
        band = fmt_ci(banded.ci_lo, banded.ci_hi, spec=spec)
        return f"{column} {f'**{value}**' if column == headline else value} (95% {band})"

    return " · ".join(cell(c) for c in sorted(get_args(BenchColumn), key=lambda c: c != headline))


def _render_bench(final: CycleFinal | None, bench: BenchScore | None) -> list[str]:
    if final is None or bench is None:
        return []
    graded = bench.graded
    if graded is None:
        return ["## Bench score — the headline", "", f"Not graded. {bench.status.sentence}", ""]

    def lift(column: BenchColumn) -> LiftEstimate | None:
        measured = bench.lift(column)
        return None if measured is None else measured.estimate

    return [
        "## Bench score — the headline",
        "",
        f"On {bench.bench_size} held-out rows no optimizer node read, graded by "
        f"`{bench.scorer_id}`. Every number below this section is the optimizer's own, read on the "
        "rows that chose its winner.",
        "",
        _bench_line("selected", graded.selected, bench),
        _bench_line("origin", graded.origin, bench),
        f"- lift, paired per row: {_bench_columns(lift, bench.headline, '{:+.3f}')}",
        "",
    ]


def _render_spend(spend: SpendRollup) -> list[str]:
    columns = f"billed $ | {RATE_PRICED_LABEL} $ | incurred $ |"
    lines = [
        "## Spend",
        "",
        f"- **{spend.billed_beside_incurred()}** (this cycle's own ledger)",
        "",
        f"| kind | {columns}",
        "|---|---:|---:|---:|",
    ]
    for kind, bucket in spend.by_kind.items():
        lines.append(f"| {kind} | {_usd_columns(bucket)} | {bucket.incurred_usd:.4f} |")
    if spend.by_role:
        lines += ["", f"| scoring pass | {columns}", "|---|---:|---:|---:|"]
    for role, bucket in sorted(spend.by_role.items(), key=lambda kv: -kv[1].incurred_usd):
        name = "outside every pass" if role is None else role
        lines.append(f"| {name} | {_usd_columns(bucket)} | {bucket.incurred_usd:.4f} |")
    lines += _node_spend("node", spend.by_node)
    lines += _node_spend("nested run's node", spend.by_nested_node)
    return [*lines, ""]


def _usd_columns(bucket: SpendBucket) -> str:
    return f"{bucket.used_usd:.4f} | {bucket.rate_priced_usd:.4f}"


def _node_spend(header: str, by_node: Mapping[str, SpendBucket]) -> list[str]:
    sent = sorted(
        ((node, b) for node, b in by_node.items() if b.input_tokens), key=lambda kv: -kv[1].sent_usd
    )
    if not sent:
        return []
    lines = [
        "",
        f"| {header} | billed $ | {RATE_PRICED_LABEL} $ | input tokens | prefix cache |",
        "|---|---:|---:|---:|---:|",
    ]
    for node, bucket in sent:
        badge = bucket.prefix.badge
        lines.append(f"| {node} | {_usd_columns(bucket)} | {bucket.input_tokens} | {badge} |")
    return lines


def _render_stats_block(
    clocks: RoundClocks,
    round_ended_s: Mapping[str, float],
    stats: tuple[ReviewStat, ...] | None,
    repairs_per_round: list[int],
    calls_per_round: list[int],
    halt: dict[str, str] | None,
    optimizer_name: str,
) -> list[str]:
    def _clock(rounds: int | None) -> str:
        if rounds is None:
            return "— (—)"
        return f"{rounds} ({_minutes(round_ended_s.get(str(rounds)))})"

    # No ceiling declared and a declared one never reached are rendered apart.
    basis = (
        "no accuracy_ceiling declared"
        if clocks.accuracy_ceiling is None
        else f"{CEILING_FRACTION:.0%} of {clocks.accuracy_ceiling:.2f}"
    )
    lines = [
        "## Round statistics",
        "",
        f"- **rounds_to_separable**: {_clock(clocks.rounds_to_separable)}",
        f"- rounds_to_improved (a new best selected, no interval): "
        f"{_clock(clocks.rounds_to_improved)}",
        f"- rounds_to_ceiling ({basis}): {_clock(clocks.rounds_to_ceiling)}",
    ]
    if stats is None:
        lines.append(f"- the optimizer's own stats: N/A — `{optimizer_name}` keeps none")
    else:
        lines += [f"- {s.name}: {_stat(s)}" for s in stats]
    # An abort closes no round of its own, so no statistic above counts it.
    if halt is not None and halt["terminated"]:
        node = f" ({halt['node']})" if halt["node"] else ""
        lines.append(f"- optimizer_abort: {halt['tag']}{node}")
    repairs_total = sum(repairs_per_round)
    calls_total = sum(calls_per_round)
    if calls_total:
        rate_pct = 100.0 * repairs_total / calls_total
        lines.append(
            f"- schema_repair_retries: {repairs_total}/{calls_total} optimizer calls "
            f"({rate_pct:.0f}% paid a second round-trip)"
        )
    lines.append("")
    return lines


def _minutes(seconds: float | None) -> str:
    return "—" if seconds is None else f"{seconds / 60.0:.1f} min"


def _render_wall_clock(clock: WallClock | None) -> list[str]:
    """The two denominators render APART: ``docs/operations/observability.md`` § The wall clock."""
    if clock is None:
        return []
    lines = [
        "## Wall clock",
        "",
        "_From the ledger's first record, never from a clean machine: install, image pull and row"
        " materialization are observed by nothing, so `init` below is preflight, not setup._",
        "",
        f"- elapsed (ledger open → finish): {_minutes(clock.elapsed_s)}",
    ]
    for phase, seconds in sorted(clock.phase_s.items(), key=lambda kv: -kv[1]):
        lines.append(f"- {phase}: {_minutes(seconds)}")
    lines.append(f"- origin gate (a human waiting): {_minutes(clock.gate_s)}")
    for bucket, node, seconds in _node_rows(clock.unbracketed_call_s):
        lines.append(f"- `{node}` calls outside every phase ({bucket}): {_minutes(seconds)}")
    lines.append(
        f"- unattributed — no phase, gate or fresh call held it: {_minutes(clock.unattributed_s)}"
    )
    # Never suppressed on truthiness: `None` (no envelope observed a wait) and 0.0 are opposite readings.
    lines.append(
        f"- cells not ALLOWED to spend: "
        f"{'no envelope observed one' if clock.unworked_s is None else _minutes(clock.unworked_s)}"
    )
    worked = _node_rows(clock.worked_s)
    if worked:
        lines += [
            "",
            "_Summed CALL time per node, not a share of the clock above: concurrent cells"
            " overshoot it, and replayed calls are excluded._",
            "",
        ]
        for bucket, node, seconds in worked:
            lines.append(f"- `{node}` ({bucket}): {_minutes(seconds)}")
    lines.append("")
    return lines


def _node_rows(by_kind: Mapping[str, Mapping[str, float]]) -> list[tuple[str, str, float]]:
    rows = [
        (kind, node, seconds)
        for kind, by_node in by_kind.items()
        for node, seconds in by_node.items()
    ]
    return sorted(rows, key=lambda row: -row[2])


def _render_behavior_summary(review: ReviewReading | None) -> list[str]:
    behavior_per_round = review.checks if review is not None else []
    if not behavior_per_round or not any(behavior_per_round):
        return []
    assert review is not None
    parts: list[str] = ["## Behaviour-check summary", ""]
    for check_id in review.check_ids:
        fails = sum(
            1
            for round_res in behavior_per_round
            for c in round_res
            if c.check_id == check_id and not c.passed
        )
        runs = sum(
            1 for round_res in behavior_per_round for c in round_res if c.check_id == check_id
        )
        marker = "✗" if fails else "✓"
        parts.append(f"- {marker} `{check_id}` — {runs - fails}/{runs} rounds passed")
    parts.append("")
    return parts


def _render_round(
    round_data: RoundResult,
    review: ReviewReading | None,
    index: int,
    *,
    is_peek: bool,
    schema_repair_retries: int = 0,
) -> list[str]:
    suffix = " (next-gen peek)" if is_peek else ""
    parts: list[str] = [
        f"### Round {round_data.round}{suffix}",
        "",
    ]
    if not is_peek:
        parts += [
            f"- accuracy: {fmt_pct(round_data.accuracy)}",
            f"- composite_fitness: `{fmt_fitness(round_data.composite_fitness)}`",
            f"- improved: **{'yes' if round_data.improved else 'no'}**",
        ]
        if series := overlap_series(round_data.overlap):
            parts.append(f"- overlap: {series}")
        if round_data.verdict_reason:
            parts.append(f"- verdict: {round_data.verdict_reason}")
    if schema_repair_retries:
        parts.append(f"- schema_repair_retries: {schema_repair_retries}")
    parts += _render_lineage(round_data.opt_sp)
    if review is None:
        return parts
    parts += _render_check_checklist(review.checks[index])
    parts += _render_variants_table(review.variants[index], round_data, scored=not is_peek)
    parts += _render_critique(review.feedback[index])
    return parts


def _render_lineage(opt_sp: OptSearchPoint | None) -> list[str]:
    parts: list[str] = ["", "**Lineage**", ""]
    if opt_sp is not None:
        parts.append(f"- lineage source: `{opt_sp.lineage.source}`")
        parts.extend(
            f"- variation: `{v.node}` ({v.mode}) wrote {', '.join(v.loci) or 'nothing'}"
            for v in opt_sp.lineage.variations
        )
        if changes := opt_sp.lineage.changes_description.strip():
            parts.append(f"- parent changes: {changes}")
    parts.append("")
    return parts


def _render_check_checklist(checks: list[CheckResult]) -> list[str]:
    if not checks:
        return ["**Behaviour checks:** _(no audit available)_", ""]
    parts: list[str] = ["**Behaviour checks**", ""]
    for c in checks:
        marker = "✓" if c.passed else "✗"
        parts.append(f"- {marker} `{c.check_id}` — {c.evidence}")
    parts.append("")
    return parts


def _render_variants_table(
    variants: list[dict[str, Any]],
    round_data: RoundResult,
    *,
    scored: bool,
) -> list[str]:
    """Joined on :func:`candidate_label`: on anything else every score column prints ``—``."""
    if not variants:
        return []
    parts: list[str] = ["**Variants**", ""]
    if scored:
        by_label = {c.label: c for c in round_data.candidate_scores}
        parts.append(
            "| variant | composite_fitness | acc | θ | lift vs parent | won | evidence | changes |"
        )
        parts.append("|---|---|---|---|---|---|---|---|")
        for i, v in enumerate(variants):
            changes = (v.get("changes_description") or "").replace("|", "\\|").strip()[:80]
            evidence = _fmt_evidence_cell(v.get("evidence_grounding"))
            label = candidate_label(round_data.round, i)
            cells = _score_cells(by_label.get(label), round_data.selected_labels)
            parts.append(f"| `{label}` | {cells} | {evidence} | {changes} |")
    else:
        parts.append("| cand_id | changes | derived_axes | evidence |")
        parts.append("|---|---|---|---|")
        for i, v in enumerate(variants):
            changes = (v.get("changes_description") or "").replace("|", "\\|").strip()[:80]
            axes = ", ".join(sorted((v.get("pipeline_overlay") or {}).keys()))
            evidence = _fmt_evidence_cell(v.get("evidence_grounding"))
            parts.append(f"| `C{i + 1}` | {changes} | {axes} | {evidence} |")
    parts.append("")
    return parts


def _score_cells(c: ScoredCandidate | None, selected_labels: Sequence[str]) -> str:
    if c is None:
        return "— | — | — | — | —"
    theta = "—" if c.theta is None else f"{c.theta:+.3f}"
    read = c.vs_reference.headline if c.vs_reference else None
    lift = (
        "—"
        if read is None
        else f"{read.estimate.value:+.3f} [{read.estimate.ci_lo:+.3f}, {read.estimate.ci_hi:+.3f}]"
    )
    won = "✓" if c.label in selected_labels else "·"
    fitness = fmt_fitness(c.composite_fitness)
    return f"`{fitness}` | {fmt_pct(c.accuracy)} | {theta} | {lift} | {won}"


def _fmt_evidence_cell(raw: object) -> str:
    if not isinstance(raw, dict):
        return "—"
    field_name = str(raw.get("field") or "").strip()
    citation = str(raw.get("citation") or "").replace("|", "\\|").strip()
    if not field_name:
        return "—"
    if citation:
        return f"`{field_name}` — {citation[:60]}"
    return f"`{field_name}` _(no citation)_"


def _render_critique(critique: str) -> list[str]:
    if not critique:
        return []
    quoted = critique.replace("\n", "\n> ")
    return ["**Critique**", "", f"> {quoted}", ""]
