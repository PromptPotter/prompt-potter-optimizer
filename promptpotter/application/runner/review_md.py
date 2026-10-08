"""``review.md`` — the per-cycle conformance report, rendered from an ``index.json`` blob plus its
rounds and audits. Pure: no ``Session``, no ``Cycle``, no disk. That is what lets ``scripts/render_review.py``
re-render a finished cycle from what is already on disk, and it is why this is not in ``output.py``:
sharing a module with the session-scoped writers left ``render_review_md`` under a second ``__all__``
that silently shadowed the first, so the file's one externally-called function was never exported."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, get_args

from promptpotter.application.optimizers.nodes import CheckResult, ReviewReading, ReviewStat
from promptpotter.application.views.render.optimizer_prompt_text import fmt_pct
from promptpotter.application.views.render.prefix_reading import prefix_reading
from promptpotter.application.views.render.primitives import fmt_fitness
from promptpotter.domain.bench import BenchColumn, BenchColumns, BenchReading, BenchScore
from promptpotter.domain.phases import STOP_REASON_INFO, StopReason
from promptpotter.domain.results import (
    CEILING_FRACTION,
    DegradationHealth,
    RoundClocks,
    RoundResult,
    ScoredCandidate,
    candidate_label,
    overlap_series,
    round_clocks,
)
from promptpotter.domain.spend import SpendBucket, SpendRollup
from promptpotter.infrastructure.store.campaign_store.store import cycle_ending, cycle_final

if TYPE_CHECKING:
    from promptpotter.application.optimizer_manifest import SelectedOptimizer
    from promptpotter.domain.opt_search_point import OptSearchPoint
    from promptpotter.domain.run_records import CycleFinal, WallClock

__all__ = ["render_review_md"]


def render_review_md(
    index: dict[str, Any],
    rounds: list[RoundResult],
    *,
    round_audits: list[dict[str, Any] | None] | None = None,
    context_object: list[str] | None = None,
    accuracy_ceiling: float | None,
    optimizer: SelectedOptimizer,
    bench: BenchScore | None,
    spend: SpendRollup,
) -> str:
    """*bench* is the campaign's headline where this cycle answers for its result; *spend* is the
    cycle's history folded (``ledger.py::ledger_chain``), the view its dashboard serves."""
    audits = list(round_audits or [None] * len(rounds))
    if len(audits) < len(rounds):
        audits.extend([None] * (len(rounds) - len(audits)))
    ctx_items = [c for c in (context_object or []) if isinstance(c, str) and c.strip()]

    final = cycle_final(index)
    # An optimizer's own readings — its statistics, behaviour scorers and feedback — exist only
    # where its runtime keeps them; any other optimizer's review says so rather than printing 0%.
    review = optimizer.runtime.review(
        optimizer,
        list(rounds),
        audits,
        context_object=ctx_items,
    )
    clock = final.wall_clock if final else None
    stats = review.stats if review is not None else None

    repairs_per_round = [_schema_repair_count(a) for a in audits]
    calls_per_round = [_optimizer_call_count(a) for a in audits]
    halt = _halt_info(index, rounds)
    parts: list[str] = []
    parts += _render_header(index, final, review.verdict if review is not None else None, halt)
    parts += _render_bench(final, bench)
    # Counted here and not read off `final`: this renders at every round close, long before
    # finalize banks a `final` block. Only the minutes need the banked clock.
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


def _schema_repair_count(audit: dict[str, Any] | None) -> int:
    """Count ``schema_repair_errors`` across optimizer nodes; non-zero ⇒ a second round-trip was paid.
    Cycle-wide rate is the cleanest single-number quality signal for an L1 optimizer prompt."""
    if not audit:
        return 0
    nodes = audit.get("nodes") or {}
    if not isinstance(nodes, dict):
        return 0
    return sum(
        len(block.get("schema_repair_errors") or ())
        for block in nodes.values()
        if isinstance(block, dict)
    )


def _optimizer_call_count(audit: dict[str, Any] | None) -> int:
    if not audit:
        return 0
    nodes = audit.get("nodes") or {}
    if not isinstance(nodes, dict):
        return 0
    return sum(1 for v in nodes.values() if isinstance(v, dict))


# --- rendering helpers ----------------------------------------------------


def _stat(stat: ReviewStat) -> str:
    """An unmeasured reading renders as ``—``, never as a number the cycle never produced."""
    return "—" if stat.value is None else format(stat.value, stat.spec)


def _halt_info(index: dict[str, Any], rounds: list[RoundResult]) -> dict[str, str] | None:
    """The cycle's terminal health story, or ``None`` when it ended cleanly. Gated on the cycle's
    TERMINAL state, never on a critical round in history that L2 then self-healed away."""
    terminated = "yes" if cycle_ending(index) is StopReason.OPTIMIZER_ABORT else ""
    last_health: DegradationHealth | None = None
    last_critical: DegradationHealth | None = None
    for r in rounds:
        if r.health is not None:
            last_health = r.health  # ends as the last GRADED round (probes carry None)
            if r.health.grade == "critical":
                last_critical = r.health
    ended_critical = last_health is not None and last_health.grade == "critical"
    if not ended_critical and not terminated:
        return None
    # The terminate-triggering round is the last completed (critical) round; reuse it
    # to name the dead node (the ended-critical path uses the same round).
    if last_critical is not None:
        tag = last_critical.cause or "critical"
        return {
            "tag": tag,
            "node": last_critical.dominant_node or "",
            "action": (last_critical.suggested_action or "").strip(),
            "terminated": terminated,
        }
    return {"tag": StopReason.OPTIMIZER_ABORT, "node": "", "action": "", "terminated": terminated}


def _stop_next_step(index: dict[str, Any]) -> str:
    """What the cycle's own stop reason says to do now, off the one table. ``""`` where the cycle
    is still running or the reason states that nothing is owed."""
    reason = cycle_ending(index)
    return "" if reason is None else STOP_REASON_INFO[reason].next_step


def _render_header(
    index: dict[str, Any],
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
        f"# Review — {index['cycle_id']}",
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
    # Beside the halt block, not inside it: `_halt_info` answers only for a cycle that ended BADLY,
    # and the endings that most need a next step are the ones it calls clean.
    if next_step := _stop_next_step(index):
        parts.append(f"> **NEXT** — {next_step}")
        parts.append("")
    if final and final.prompt_hashes:
        parts.append("**Prompt hashes**")
        parts.append("")
        # Walk what the stamp CONTAINS, never a hand-listed subset: a name missing from the list
        # renders two cycles under different optimizer prompts as identical hash blocks.
        parts += [
            f"- `{name}`: `{digest[:8]}`" for name, digest in sorted(final.prompt_hashes.items())
        ]
        parts.append("")
    return parts


def _bench_line(name: str, reading: BenchReading | None, bench: BenchScore) -> str:
    if reading is None:
        return f"- {name}: no reading — {bench.missing_reason}"
    return (
        f"- {name} (round {reading.round}): {_bench_columns(reading, bench.headline, '{:.3f}')} · "
        f"{reading.n_scored}/{bench.bench_size} rows"
    )


def _bench_columns(columns: BenchColumns, headline: BenchColumn, spec: str) -> str:
    """Both columns by name, the headline first and bold."""

    def cell(column: BenchColumn) -> str:
        banded = columns.of(column)
        if banded is None:
            return f"{column} —"
        value = spec.format(banded.value)
        band = (
            ""
            if banded.ci_lo is None or banded.ci_hi is None
            else f" (95% {spec.format(banded.ci_lo)} to {spec.format(banded.ci_hi)})"
        )
        return f"{column} {f'**{value}**' if column == headline else value}{band}"

    return " · ".join(cell(c) for c in sorted(get_args(BenchColumn), key=lambda c: c != headline))


def _render_bench(final: CycleFinal | None, bench: BenchScore | None) -> list[str]:
    """The headline, above everything the optimizer measured on the rows that chose its winner.
    Silent while the cycle runs; once it ends, an absent score is said rather than left blank."""
    if final is None:
        return []
    if bench is None:
        return [
            "## Bench score — the headline",
            "",
            "None: the cycle stopped before its selection was graded, or it ran beside the "
            "campaign's line, whose result is the one graded.",
            "",
        ]
    return [
        "## Bench score — the headline",
        "",
        f"On {bench.bench_size} held-out rows no optimizer node read, graded by "
        f"`{bench.scorer_id}`. Every number below this section is the optimizer's own, read on the "
        "rows that chose its winner.",
        "",
        _bench_line("selected", bench.selected, bench),
        _bench_line("origin", bench.origin, bench),
        f"- lift, paired per row: {_bench_columns(bench.lift, bench.headline, '{:+.3f}')}",
        "",
    ]


def _render_spend(spend: SpendRollup) -> list[str]:
    """Billed beside incurred, total then per kind and per scoring pass — a replay is priced in the
    second column and charged in neither."""
    lines = [
        "## Spend",
        "",
        f"- **{spend.billed_beside_incurred()}** (this cycle's own ledger)",
        "",
        "| kind | billed $ | incurred $ |",
        "|---|---:|---:|",
    ]
    for kind, bucket in spend.by_kind.items():
        lines.append(f"| {kind} | {bucket.used_usd:.4f} | {bucket.incurred_usd:.4f} |")
    if spend.by_role:
        lines += ["", "| scoring pass | billed $ | incurred $ |", "|---|---:|---:|"]
    for role, bucket in sorted(spend.by_role.items(), key=lambda kv: -kv[1].incurred_usd):
        name = "outside every pass" if role is None else role
        lines.append(f"| {name} | {bucket.used_usd:.4f} | {bucket.incurred_usd:.4f} |")
    lines += _node_spend("node", spend.by_node)
    lines += _node_spend("nested run's node", spend.by_nested_node)
    return [*lines, ""]


def _node_spend(header: str, by_node: Mapping[str, SpendBucket]) -> list[str]:
    """Each node beside its provider's prefix-cache share: a cold share on a node that re-sends
    one head is a route spread over hosts, priced in the column beside it."""
    sent = sorted(
        ((node, b) for node, b in by_node.items() if b.input_tokens), key=lambda kv: -kv[1].used_usd
    )
    if not sent:
        return []
    lines = ["", f"| {header} | billed $ | input tokens | prefix cache |", "|---|---:|---:|---:|"]
    for node, bucket in sent:
        badge = prefix_reading(bucket.cache_share, replayed=False).badge
        lines.append(f"| {node} | {bucket.used_usd:.4f} | {bucket.input_tokens} | {badge} |")
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
        """A round count and the minute it landed at, joined on the round number. The rounds are
        what a peer reports; the minutes are what a reader outside this project can price, because
        a round is whatever the budget made it."""
        if rounds is None:
            return "— (—)"
        return f"{rounds} ({_minutes(round_ended_s.get(str(rounds)))})"

    # Two silences, rendered apart: no ceiling declared is a different fact from a declared one
    # the cycle never reached, and one glyph for both is the reading that gets passed on.
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
    """``—`` where the number is absent, which is a different fact from zero minutes."""
    return "—" if seconds is None else f"{seconds / 60.0:.1f} min"


def _render_wall_clock(clock: WallClock | None) -> list[str]:
    """**Where this block's claim stops — owned by** ``docs/operations/observability.md`` § The wall
    clock, and where the claim stops. Render the two denominators APART; they are not one number."""
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
    # Rendered as a state, never suppressed on truthiness: no envelope observed a wait and every
    # enveloped cell waited for nothing are opposite readings, and only one of them is 0.0.
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
    """``review`` is ``None`` on a cycle whose optimizer keeps no reading of its own: its behaviour
    checks, variant table and critique do not exist."""
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
            # `improved` above names the outcome; this is the selector's own reading behind it.
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
    """Where the individual the round ended on came from; a round that banked none names nothing."""
    parts: list[str] = ["", "**Lineage**", ""]
    if opt_sp is not None:
        parts.append(f"- lineage source: `{opt_sp.lineage.source}`")
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
    """Per-variant row: the audit dict carries what L1 PROPOSED, ``round_data`` what it MEASURED,
    joined on :func:`candidate_label`. Join on anything else and every score column prints ``—``."""
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
    """``—`` only where there genuinely is no number — a variant the round never scored, one
    outside the election fit, or one sharing under two cells with its parent, where ``None`` is
    deliberate: a 0.0 there reads as a measurement.

    The ``won`` column is the round's SELECTION, and the margin beside it is the paired accuracy
    lift over the parent with its interval."""
    if c is None:
        return "— | — | — | — | —"
    theta = "—" if c.theta is None else f"{c.theta:+.3f}"
    lo, hi = c.reference_lift_ci_lo, c.reference_lift_ci_hi
    lift = (
        f"{c.reference_lift:+.3f} [{lo:+.3f}, {hi:+.3f}]"
        if c.reference_lift is not None and lo is not None and hi is not None
        else "—"
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
