"""``review.md`` — the per-cycle conformance report, rendered from an ``index.json`` blob plus its
rounds and audits. Pure: no ``Session``, no ``Cycle``, no disk. That is what lets ``scripts/render_review.py``
re-render a finished cycle from what is already on disk, and it is why this is not in ``output.py``:
sharing a module with the session-scoped writers left ``render_review_md`` under a second ``__all__``
that silently shadowed the first, so the file's one externally-called function was never exported."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from promptpotter.application.optimizers.nodes import CheckResult, ReviewReading, ReviewStats
from promptpotter.application.views.render.optimizer_prompt_text import fmt_pct
from promptpotter.domain.bench import BenchReading, BenchScore
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

if TYPE_CHECKING:
    from promptpotter.application.optimizer_manifest import SelectedOptimizer

__all__ = ["render_review_md"]


def render_review_md(
    index: dict[str, Any],
    rounds: list[RoundResult],
    *,
    round_audits: list[dict[str, Any] | None] | None = None,
    context_object: list[str] | None = None,
    accuracy_ceiling: float | None,
    optimizer: SelectedOptimizer,
) -> str:
    audits = list(round_audits or [None] * len(rounds))
    if len(audits) < len(rounds):
        audits.extend([None] * (len(rounds) - len(audits)))
    ctx_items = [c for c in (context_object or []) if isinstance(c, str) and c.strip()]

    final = index.get("final") or {}
    # Absent means the origin was never scored, which is not the same as scoring 0.0 — `_top_lifts`
    # drops round 0's lift rather than measuring it against a bar nothing established.
    origin_cf = final.get("origin_composite_fitness")
    # An optimizer's own readings — its statistics, behaviour scorers and feedback — exist only
    # where its runtime keeps them; any other optimizer's review says so rather than printing 0%.
    review = optimizer.runtime.review(
        optimizer,
        list(rounds),
        audits,
        context_object=ctx_items,
        origin_composite_fitness=float(origin_cf) if isinstance(origin_cf, int | float) else None,
    )
    clock = final.get("wall_clock") or {}
    stats = review.stats if review is not None else None

    repairs_per_round = [_schema_repair_count(a) for a in audits]
    calls_per_round = [_optimizer_call_count(a) for a in audits]
    halt = _halt_info(index, rounds)
    parts: list[str] = []
    parts += _render_header(index, final, stats, halt)
    parts += _render_bench(final)
    # Counted here and not read off `final`: this renders at every round close, long before
    # finalize banks a `final` block. Only the minutes need the banked clock.
    clocks = round_clocks(rounds, accuracy_ceiling=accuracy_ceiling)
    round_ended_s = _float_map(clock.get("round_ended_s"))
    parts += _render_stats_block(
        clocks, round_ended_s, stats, repairs_per_round, calls_per_round, halt, optimizer.name
    )
    parts += _render_wall_clock(clock)
    parts += _render_behavior_summary(review)
    parts += ["## Rounds", ""]

    last_idx = len(rounds) - 1
    for i, round_data in enumerate(rounds):
        is_peek = i == last_idx and _is_generation_only(round_data)
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


def _halt_info(index: dict[str, Any], rounds: list[RoundResult]) -> dict[str, str] | None:
    """The cycle's terminal health story, or ``None`` when it ended cleanly. Gated on the cycle's
    TERMINAL state, never on a critical round in history that L2 then self-healed away."""
    stop_reason = (index.get("stop_reason") or "").strip()
    terminated = "yes" if stop_reason == StopReason.OPTIMIZER_ABORT else ""
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
    return {"tag": "terminate_proposal", "node": "", "action": "", "terminated": terminated}


def _stop_next_step(index: dict[str, Any]) -> str:
    """What the cycle's own stop reason says to do now, off the one table. ``""`` where the cycle
    is still running, the reason is unknown, or the reason states that nothing is owed."""
    try:
        return STOP_REASON_INFO[StopReason((index.get("stop_reason") or "").strip())].next_step
    except ValueError:
        return ""


def _render_header(
    index: dict[str, Any],
    final: dict[str, Any],
    stats: ReviewStats | None,
    halt: dict[str, str] | None,
) -> list[str]:
    cycle_id = index.get("cycle_id") or "(unknown cycle)"
    mode = (final.get("mode") or "full").strip() or "full"
    conformance = "" if stats is None else f" · round-1 conformance: **{stats.round_1_verdict}**"
    parts: list[str] = [
        f"# Review — {cycle_id}",
        "",
        f"_mode: **{mode}**{conformance}_",
        "",
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
    hashes = final.get("prompt_hashes") or {}
    if hashes:
        parts.append("**Prompt hashes**")
        parts.append("")
        # Walk what the stamp CONTAINS, never a hand-listed subset: a name missing from the list
        # renders two cycles under different optimizer prompts as identical hash blocks.
        for name in sorted(hashes):
            short = (hashes.get(name) or "")[:8]
            if short:
                parts.append(f"- `{name}`: `{short}`")
        parts.append("")
    return parts


def _bench_line(name: str, reading: BenchReading, bench_size: int) -> str:
    band = (
        ""
        if reading.ci_lo is None or reading.ci_hi is None
        else f" (95% {reading.ci_lo:.3f} to {reading.ci_hi:.3f})"
    )
    value = "—" if reading.composite_fitness is None else f"{reading.composite_fitness:.3f}"
    stopped = f", stopped: {reading.stopped}" if reading.stopped else ""
    return (
        f"- {name} (round {reading.round}): **{value}**{band} · accuracy "
        f"{fmt_pct(reading.accuracy)} · {reading.n_scored}/{bench_size} rows{stopped}"
    )


def _render_bench(final: dict[str, Any]) -> list[str]:
    """The headline, above everything the optimizer measured on the rows that chose its winner.
    Silent while the cycle runs; once it ends, an absent score is said rather than left blank."""
    if not final:
        return []
    if final.get("bench") is None:
        return [
            "## Bench score — the headline",
            "",
            "None: the campaign's `dataset_split` holds nothing out, or the cycle stopped before "
            "its selection could be graded.",
            "",
        ]
    bench = BenchScore.model_validate(final["bench"])
    lift = (
        "—"
        if bench.lift is None
        else f"{bench.lift:+.3f}"
        + (
            ""
            if bench.lift_ci_lo is None or bench.lift_ci_hi is None
            else f" (95% {bench.lift_ci_lo:+.3f} to {bench.lift_ci_hi:+.3f})"
        )
    )
    return [
        "## Bench score — the headline",
        "",
        f"On {bench.bench_size} held-out rows no optimizer node read, under the campaign's "
        "formula. Every number below this section is the optimizer's own, read on the rows that "
        "chose its winner.",
        "",
        _bench_line("selected", bench.selected, bench.bench_size),
        _bench_line("origin", bench.origin, bench.bench_size),
        f"- lift, paired per row: **{lift}**",
        "",
    ]


def _render_stats_block(
    clocks: RoundClocks,
    round_ended_s: dict[str, float],
    stats: ReviewStats | None,
    repairs_per_round: list[int],
    calls_per_round: list[int],
    halt: dict[str, str] | None,
    optimizer_name: str,
) -> list[str]:
    def _rate(value: float | None, spec: str = ".2f") -> str:
        """An unmeasured rate renders as ``—``, never as a number the cycle never produced."""
        return "—" if value is None else format(value, spec)

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
        lines += [
            f"- yield_rate: {_rate(stats.yield_rate)}",
            f"- top_lift_mean: {_rate(stats.top_lift_mean, '+.4f')}",
            f"- behavior_pass_rate: {_rate(stats.behavior_pass_rate)}",
            f"- l2_behavior_pass_rate: {_rate(stats.l2_behavior_pass_rate)}",
            f"- stagnation_max: {stats.stagnation_max}",
            f"- l2_fires: {stats.l2_fires}",
        ]
    # A terminate is an L2 fire that produces no l2-sourced round, so `l2_fires`
    # alone reads 0 — name it explicitly so an L2 halt isn't invisible.
    if halt is not None and halt["terminated"]:
        node = f" ({halt['node']})" if halt["node"] else ""
        lines.append(f"- l2_terminated: {halt['tag']}{node}")
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


def _float_map(raw: object) -> dict[str, float]:
    if not isinstance(raw, dict):
        return {}
    return {
        str(k): float(v)
        for k, v in raw.items()
        if isinstance(v, int | float) and not isinstance(v, bool)
    }


def _minutes(seconds: object) -> str:
    """``—`` where the number is absent, which is a different fact from zero minutes."""
    if not isinstance(seconds, int | float) or isinstance(seconds, bool):
        return "—"
    return f"{float(seconds) / 60.0:.1f} min"


def _render_wall_clock(clock: dict[str, Any]) -> list[str]:
    """**Where this block's claim stops — owned by** ``docs/operations/observability.md`` § The wall
    clock, and where the claim stops. Render the two denominators APART; they are not one number."""
    if not clock:
        return []
    lines = [
        "## Wall clock",
        "",
        "_From the ledger's first record, never from a clean machine: install, image pull and row"
        " materialization are observed by nothing, so `init` below is preflight, not setup._",
        "",
        f"- elapsed (ledger open → finish): {_minutes(clock['elapsed_s'])}",
    ]
    for phase, seconds in sorted(_float_map(clock.get("phase_s")).items(), key=lambda kv: -kv[1]):
        lines.append(f"- {phase}: {_minutes(seconds)}")
    lines.append(f"- origin gate (a human waiting): {_minutes(clock['gate_s'])}")
    for bucket, node, seconds in _node_rows(clock.get("unbracketed_call_s")):
        lines.append(f"- `{node}` calls outside every phase ({bucket}): {_minutes(seconds)}")
    lines.append(
        f"- unattributed — no phase, gate or fresh call held it: "
        f"{_minutes(clock['unattributed_s'])}"
    )
    # Rendered as a state, never suppressed on truthiness: no envelope observed a wait and every
    # enveloped cell waited for nothing are opposite readings, and only one of them is 0.0.
    unworked = clock.get("unworked_s")
    lines.append(
        f"- cells not ALLOWED to spend: "
        f"{'no envelope observed one' if unworked is None else _minutes(unworked)}"
    )
    worked = _node_rows(clock.get("worked_s"))
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


def _node_rows(raw: object) -> list[tuple[str, str, float]]:
    if not isinstance(raw, dict):
        return []
    rows = [
        (str(bucket), node, seconds)
        for bucket, by_node in raw.items()
        for node, seconds in _float_map(by_node).items()
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
    opt_sp = round_data.opt_sp.model_dump() if round_data.opt_sp else {}
    lineage = opt_sp.get("lineage") or {}
    suffix = " (next-gen peek)" if is_peek else ""
    parts: list[str] = [
        f"### Round {round_data.round}{suffix}",
        "",
    ]
    if not is_peek:
        parts += [
            f"- accuracy: {fmt_pct(round_data.accuracy)}",
            f"- composite_fitness: `{round_data.composite_fitness:.4f}`",
            f"- improved: **{'yes' if round_data.improved else 'no'}**",
        ]
        if series := overlap_series(round_data.overlap):
            parts.append(f"- overlap: {series}")
        if round_data.verdict_reason:
            # `improved` above names the outcome; this is the selector's own reading behind it.
            parts.append(f"- verdict: {round_data.verdict_reason}")
    if schema_repair_retries:
        parts.append(f"- schema_repair_retries: {schema_repair_retries}")
    parts += _render_lineage(lineage)
    if review is None:
        return parts
    parts += _render_check_checklist(review.checks[index])
    parts += _render_variants_table(review.variants[index], round_data, scored=not is_peek)
    parts += _render_critique(review.feedback[index])
    return parts


def _render_lineage(lineage: dict[str, Any]) -> list[str]:
    parts: list[str] = ["", "**Lineage**", ""]
    src = (lineage.get("source") or "").strip()
    if src:
        parts.append(f"- lineage source: `{src}`")
    changes = (lineage.get("changes_description") or "").strip()
    if changes:
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

    The ``won`` column is the round's SELECTION, and the margin beside it is the θ-lift with its
    interval. Both replace a composite Δ and a ``✓`` derived from it — which is not the election
    rule and carried no interval, so the glyph read as a verdict the round had not made."""
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
    return f"`{c.composite_fitness:.4f}` | {fmt_pct(c.accuracy)} | {theta} | {lift} | {won}"


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


def _is_generation_only(round_data: RoundResult) -> bool:
    return round_data.status == "generation_only"
