"""A run's markdown artifacts and its registry export: the one ``RunArtifacts`` projection."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, ClassVar, get_args

from promptpotter.application.bench.task_context import campaign_framing
from promptpotter.application.intelligence.hard_sample_sorter import rank_hard_samples
from promptpotter.application.optimizer_manifest import select_optimizer
from promptpotter.application.optimizers.nodes import CheckResult, ReviewReading, ReviewStat
from promptpotter.application.runner.bench import own_level, read_cycle_bench
from promptpotter.application.scoring.closed_rounds import closed_rounds
from promptpotter.application.scoring.measurement_log import cycle_hard_samples
from promptpotter.application.views.render.markdown import to_markdown
from promptpotter.application.views.render.phase import stop_next_lines
from promptpotter.application.views.render.primitives import fmt_ci, fmt_fitness, overlap_series
from promptpotter.application.views.view_models import (
    DigestStatusView,
    ForkSummaryView,
    HardSamplesView,
    LogMdView,
    RoundDigestView,
)
from promptpotter.config.settings import APP_VERSION
from promptpotter.domain.arm_reading import fitness_terms_text, fold_fitness_text, lift_band_text
from promptpotter.domain.bench import BandedValue, BenchColumn, BenchReading, BenchScore
from promptpotter.domain.cells import HardSampleOrder
from promptpotter.domain.cycle_index import CycleIndex
from promptpotter.domain.cycle_paths import CycleDir, CycleHop
from promptpotter.domain.export import build_prompt_export
from promptpotter.domain.paired_reading import READING_STATE_INFO, ArmKey, lift_versus
from promptpotter.domain.phases import StopReason
from promptpotter.domain.results import RoundResult
from promptpotter.domain.results_health import health_reading
from promptpotter.domain.round_health import DegradationHealth
from promptpotter.domain.ruler import LevelSeries
from promptpotter.domain.run_records import RaceStandingRecord
from promptpotter.domain.run_standing import (
    CEILING_FRACTION,
    RoundClocks,
    declared_selection,
    round_clocks,
)
from promptpotter.domain.spend import RATE_PRICED_LABEL, SpendBucket, SpendRollup
from promptpotter.infrastructure.ledger import ledger_chain
from promptpotter.infrastructure.projections.audit_trail import round_audits
from promptpotter.infrastructure.projections.base import Projection
from promptpotter.infrastructure.store.campaign_store.ledger_scan import (
    LedgerSpend,
    scan_closed_bench,
    scan_ledger_spend,
    scan_ledger_spend_by_round,
)
from promptpotter.infrastructure.store.io import write_text
from promptpotter.infrastructure.store.layout import CampaignLayout, CycleLayout, sibling_kind
from promptpotter.infrastructure.store.read_model import LedgerIndex, RecordClasses

if TYPE_CHECKING:
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.optimizer_manifest import SelectedOptimizer
    from promptpotter.domain.campaign_config import CampaignConfig
    from promptpotter.domain.cells import HardSamples
    from promptpotter.domain.opt_search_point import EvidenceGrounding, OptSearchPoint
    from promptpotter.domain.round_audit import RoundAudit
    from promptpotter.domain.run_records import CycleFinal, RoundClosedRecord, WallClock
    from promptpotter.infrastructure.store.campaign_store.store import CampaignStore


def render_review_md(
    index: CycleIndex,
    rounds: list[RoundResult],
    *,
    round_audits: list[RoundAudit | None],
    context_object: list[str] | None = None,
    fitness_ceiling: float | None,
    optimizer: SelectedOptimizer,
    bench: BenchScore | None,
    spend: SpendRollup,
) -> str:
    ctx_items = [c for c in (context_object or []) if c.strip()]

    final = index.final
    review = optimizer.review(list(rounds), round_audits, context_object=ctx_items)
    clock = final.wall_clock if final else None
    stats = review.stats if review is not None else None

    repairs_per_round = [_schema_repair_count(a) for a in round_audits]
    calls_per_round = [_optimizer_call_count(a) for a in round_audits]
    halt = _halt_info(index, rounds)
    parts: list[str] = []
    parts += _render_header(index, final, review.verdict if review is not None else None, halt)
    parts += _render_bench(final, bench)
    # Counted here, never read off `final`: this renders at every round close, before one is banked.
    clocks = round_clocks(rounds, fitness_ceiling=fitness_ceiling)
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
    last_critical: RoundResult | None = None
    for r in rounds:
        if r.health.grade != "unmeasured":
            last_health = r.health
            if r.health.grade == "critical":
                last_critical = r
    ended_critical = last_health is not None and last_health.grade == "critical"
    if not ended_critical and not terminated:
        return None
    if last_critical is not None:
        health = last_critical.health
        reading = health_reading(last_critical.round, health)
        return {
            "tag": health.cause or "critical",
            "node": health.dominant_node or "",
            "action": "" if reading is None else reading.advice,
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
    if index.stop_reason is not None and (said := stop_next_lines(index.stop_reason)):
        parts += ["\n>\n".join(f"> {line}" for line in said), ""]
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
    of: Callable[[BenchColumn], BandedValue | None],
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
        sentence = READING_STATE_INFO[bench.status.state].sentence
        return ["## Bench score — the headline", "", f"Not graded. {sentence}", ""]

    def lift(column: BenchColumn) -> str:
        if (measured := bench.lift(column)) is None:
            return f"{column} —"
        text = lift_band_text(measured.estimate, measured.measurand.unit)
        return f"{column} {f'**{text}**' if column == bench.headline else text}"

    columns = sorted(get_args(BenchColumn), key=lambda c: c != bench.headline)
    return [
        "## Bench score — the headline",
        "",
        f"On {bench.bench_size} held-out rows no optimizer node read, graded by "
        f"`{bench.scorer_id}`. Every number below this section is the optimizer's own, read on the "
        "rows that chose its winner.",
        "",
        _bench_line("selected", graded.selected, bench),
        _bench_line("origin", graded.origin, bench),
        f"- lift, paired per row: {' · '.join(lift(c) for c in columns)}",
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
        "no fitness_ceiling declared"
        if clocks.fitness_ceiling is None
        else f"{CEILING_FRACTION:.0%} of {clocks.fitness_ceiling:.2f}"
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
    lines.append(f"- a gate (a human waiting): {_minutes(clock.gate_s)}")
    for bucket, node, seconds in _node_rows(clock.unbracketed_call_s):
        lines.append(f"- `{node}` calls outside every phase ({bucket}): {_minutes(seconds)}")
    lines.append(
        f"- unattributed — no phase, gate or fresh call held it: {_minutes(clock.unattributed_s)}"
    )
    # Never suppressed on truthiness: `None` (no fresh cell measured) and 0.0 are opposite readings.
    lines.append(
        f"- cells not ALLOWED to spend: "
        f"{'no fresh cell measured' if clock.unworked_s is None else _minutes(clock.unworked_s)}"
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
    head, fold, election = round_data.head, round_data.fold, round_data.election
    if not is_peek:
        parts += [
            f"- fitness: {fold_fitness_text(fold)}",
            f"- composite_fitness: `{fmt_fitness(None if fold is None else fold.composite_fitness)}`",
            f"- improved: **{'yes' if round_data.improved else 'no'}**",
        ]
        if fold is not None and (terms := fitness_terms_text(fold.term_means)):
            parts.append(f"- formula terms: `{terms}`")
        if series := overlap_series(round_data.overlap):
            parts.append(f"- overlap: {series}")
        if election is not None and election.verdict_reason:
            parts.append(f"- verdict: {election.verdict_reason}")
    if schema_repair_retries:
        parts.append(f"- schema_repair_retries: {schema_repair_retries}")
    parts += _render_lineage(None if head is None else head.individual)
    if review is None:
        return parts
    parts += _render_check_checklist(review.checks[index])
    parts += _render_variants_table(round_data, scored=not is_peek)
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


def _render_variants_table(round_data: RoundResult, *, scored: bool) -> list[str]:
    panel = round_data.panel
    if not panel:
        return []
    parts: list[str] = ["**Variants**", ""]
    if scored:
        # The column's sheet, as its readings name it: every arm's is the round's one.
        sheet = next(
            (
                fit.vs_reference.cell_set
                for key in panel
                if (fit := round_data.fit(key)) is not None and fit.vs_reference is not None
            ),
            None,
        )
        parts.append(
            f"| variant | composite_fitness | fit | θ | lift {lift_versus('parent', sheet)} | won "
            "| evidence | changes |"
        )
        parts.append("|---|---|---|---|---|---|---|---|")
    else:
        parts.append("| cand_id | changes | derived_axes | evidence |")
        parts.append("|---|---|---|---|")
    for key, arm in panel.items():
        lineage = arm.individual.lineage
        changes = lineage.changes_description.replace("|", "\\|").strip()[:80]
        evidence = _fmt_evidence_cell(lineage.evidence_grounding)
        if scored:
            cells = _score_cells(round_data, key)
            parts.append(f"| `{arm.label}` | {cells} | {evidence} | {changes} |")
        else:
            axes = ", ".join(sorted(arm.overlay))
            parts.append(f"| `{arm.label}` | {changes} | {axes} | {evidence} |")
    parts.append("")
    return parts


def _score_cells(round_data: RoundResult, key: ArmKey) -> str:
    if (fold := round_data.folds.get(key)) is None:
        return "— | — | — | — | —"
    fit = round_data.fit(key)
    theta = "—" if fit is None or fit.theta is None else f"{fit.theta:+.3f}"
    read = fit.vs_reference.headline if fit is not None and fit.vs_reference else None
    lift = "—" if read is None else lift_band_text(read.estimate, read.measurand.unit)
    election = round_data.election
    won = "✓" if election is not None and key == election.selected else "·"
    composite = fmt_fitness(fold.composite_fitness)
    return f"`{composite}` | {fold_fitness_text(fold)} | {theta} | {lift} | {won}"


def _fmt_evidence_cell(grounding: EvidenceGrounding | None) -> str:
    if grounding is None:
        return "—"
    field_name = grounding.field.strip()
    citation = grounding.citation.replace("|", "\\|").strip()
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


class _PBestTrajectories:
    """One standing is ONE candidate's reading (``current``), never fanned across its map."""

    records: ClassVar[RecordClasses] = (RaceStandingRecord,)

    def __init__(self) -> None:
        self._by_round: dict[int, dict[str, list[float]]] = {}

    def feed(self, record: RaceStandingRecord) -> None:
        held = self._by_round.setdefault(record.round, {})
        held.setdefault(record.current, []).append(record.p_best)

    def value(self) -> dict[int, dict[str, list[float]]]:
        return self._by_round


def _sample_queries(rounds: list[RoundResult]) -> dict[str, str]:
    out: dict[str, str] = {}
    for rr in rounds:
        for cell in rr.results:
            out.setdefault(cell.key, cell.facts.query)
    return out


def from_disk_log(
    index: CycleIndex,
    rounds: list[RoundResult],
    *,
    hop: CycleHop,
    spend_by_round: dict[int, SpendRollup],
    spent: LedgerSpend,
    level_series: LevelSeries | None,
    hard_samples: HardSamples | None = None,
    p_best: dict[int, dict[str, list[float]]] | None = None,
    fork_indices: Sequence[CycleIndex] = (),
    hard_sample_order: HardSampleOrder = "info_gain",
) -> LogMdView:
    final = index.final
    status = DigestStatusView(
        campaign_id=index.cycle_id,
        optimizer=next((t.closed_state.manifest for t in rounds), None),
        stop_reason=index.stop_reason,
        standing=index.standing,
        spent=spent,
        rounds_completed=len(index.rounds),
        started_at=final.started_at if final else None,
        finished_at=index.finished_at,
        gen_only_rounds=sum(1 for t in rounds if t.generation_only),
    )

    round_views: list[RoundDigestView] = []
    for t in rounds:
        traj = {} if p_best is None else p_best.get(t.round, {})
        head, fold, election = t.head, t.fold, t.election
        selected = None if election is None else election.selected
        fit = None if selected is None else t.fit(selected)
        round_views.append(
            RoundDigestView(
                round=t.round,
                label="" if head is None else head.label,
                fold=fold,
                improved=t.improved,
                changes_description="" if head is None else head.changes_description.strip(),
                facts=tuple(t.optimizer_facts),
                composite_floor=(
                    fit.vs_reference.reference_level("objective")
                    if fit and fit.vs_reference
                    else None
                ),
                level=None if level_series is None else level_series.reading(t.round),
                verdict_reason=None if election is None else election.verdict_reason,
                overlap=t.overlap,
                p_best_trajectory=traj,
                candidate_labels={key: arm.label for key, arm in t.panel.items()},
                winner_id=selected or "",
                spend=spend_by_round.get(t.round),
            )
        )

    hard: HardSamplesView | None = None
    if hard_samples is not None:
        measured = hard_samples.sample_order
        resolved, ranked = rank_hard_samples(
            hard_samples,
            {key: hard_samples.positions[key] for key in measured},
            measured=set(measured),
            order=hard_sample_order,
        )
        hard = HardSamplesView(
            artifact=hard_samples,
            sample_query_lookup=_sample_queries(rounds),
            order=resolved,
            ranked=tuple(ranked),
        )
    fork_views = tuple(_fork_summary_from_index(fi) for fi in fork_indices if fi.rounds)
    return LogMdView(
        status=status,
        rounds=tuple(round_views),
        # Declared at run init, so a RUNNING cycle's digest names the formula its numbers carry.
        formula=index.scorer_cell_formula,
        hard_samples=hard,
        # The winner is the package's to state, so an ended cycle's digest names where it is.
        exported=hop if final else None,
        forks=fork_views,
    )


def _fork_summary_from_index(fork_index: CycleIndex) -> ForkSummaryView:
    return ForkSummaryView(
        cycle_id=fork_index.cycle_id,
        kind=sibling_kind(fork_index.cycle_id),
        standing=fork_index.standing,
        n_rounds=len(fork_index.rounds),
        stop_reason=fork_index.stop_reason,
    )


class RunArtifacts(Projection):
    """The ONE writer of a cycle's ``log.md``, ``review.md`` and ``export.json`` and its campaign's ``log.md``."""

    def __init__(self, session: Session, config: CampaignConfig) -> None:
        self._session, self._config = session, config

    def _handle_round_closed(self, record: RoundClosedRecord) -> None:  # noqa: ARG002 -- the close is the cue
        self._render()

    def drain(self) -> None:
        _render_export(self._session, self._config)
        self._render()

    def _render(self) -> None:
        _render_cycle_log_md(self._session, self._config)
        _render_campaign_log_md(self._session)
        _render_review_md(self._session, self._config)


def _spend_by_round(layout: CycleLayout) -> dict[int, SpendRollup]:
    return scan_ledger_spend_by_round(ledger_chain(CycleDir(layout.cycle_dir), moment=None))


def _p_best(layout: CycleLayout) -> dict[int, dict[str, list[float]]]:
    """The cycle's own standings: a fork's prefix is its parent's to report."""
    return LedgerIndex.of(layout.ledger).view(_PBestTrajectories)


def _spent(layout: CycleLayout) -> LedgerSpend:
    return scan_ledger_spend(ledger_chain(CycleDir(layout.cycle_dir), moment=None))


def _render_cycle_log_md(session: Session, config: CampaignConfig) -> None:
    store, hop = session.store.campaigns, session.hop
    index = store.load(hop, moment=None)
    if not index:
        return
    rounds = _banked_rounds(session, hop)
    layout = CycleLayout(store.cycle_dir(hop))
    content = to_markdown(
        from_disk_log(
            index,
            rounds,
            hop=hop,
            hard_samples=cycle_hard_samples(session.store, hop) if rounds else None,
            p_best=_p_best(layout),
            hard_sample_order=config.hard_sample_order,
            spend_by_round=_spend_by_round(layout),
            spent=_spent(layout),
            level_series=store.standing_rounds(hop, moment=None).level_series,
        )
    )
    write_text(layout.log_md, content)


def _render_campaign_log_md(session: Session) -> None:
    store, campaign_id = session.store.campaigns, session.campaign_id
    campaign = store.load_campaign(campaign_id)
    if campaign is None:
        return
    root = campaign.root_hop
    index = store.load(root, moment=None)
    if not index:
        return
    root_layout = CycleLayout(store.cycle_dir(root))
    content = to_markdown(
        from_disk_log(
            index,
            _banked_rounds(session, root),
            hop=root,
            p_best=_p_best(root_layout),
            fork_indices=_load_sibling_indices(store, campaign_id, exclude=root.cycle_id),
            spend_by_round=_spend_by_round(root_layout),
            spent=_spent(root_layout),
            level_series=store.standing_rounds(root, moment=None).level_series,
        )
    )
    write_text(CampaignLayout(store.campaign_root_dir(campaign_id)).log_md, content)


def _load_sibling_indices(
    store: CampaignStore, campaign_id: str, *, exclude: str
) -> list[CycleIndex]:
    hops = (
        CycleHop(campaign_id=campaign_id, cycle_id=cycle_dir.name)
        for cycle_dir in store.campaign_cycle_dirs(campaign_id)
        if cycle_dir.name != exclude
    )
    return [index for hop in hops if (index := store.load(hop, moment=None)) is not None]


def _banked_rounds(session: Session, hop: CycleHop) -> list[RoundResult]:
    scoring = session.state.scoring
    # A run stopped before its origin closed has no round and may have no scorer either.
    if not session.store.campaigns.standing_rounds(hop, moment=None).rounds:
        return []
    return closed_rounds(session.store, hop, scoring.scorer, moment=None)


def _render_export(session: Session, config: CampaignConfig) -> None:
    """An ended cycle's winner as its ledger states it; a pause or a cycle no round closed has none."""
    store, hop = session.store.campaigns, session.hop
    index = store.load(hop, moment=None)
    series = store.standing_rounds(hop, moment=None).level_series
    rounds = _banked_rounds(session, hop)
    campaign = store.load_campaign(session.campaign_id)
    bench = scan_closed_bench(store.ledger(hop))
    if (
        index is None
        or index.final is None
        or index.stop_reason is None
        or index.finished_at is None
        or series is None
        or not rounds
        or bench is None
    ):
        return
    winner = declared_selection(rounds)
    export = build_prompt_export(
        winner,
        rounds,
        tool_version=APP_VERSION,
        campaign_id=session.campaign_id,
        cycle_id=session.cycle_id,
        dataset_name=session.measured_dataset,
        dataset_hash=session.bank.dataset_hash,
        treatment=None if campaign is None else campaign.treatment,
        stop_reason=index.stop_reason,
        finished_at=index.finished_at,
        formula=index.scorer_cell_formula,
        own=own_level(winner.results, session.state.scoring.scorer.fitness_unit),
        level=series.reading(winner.round),
        schema=session.pipeline_schema,
        framing=campaign_framing(session.store, config, session.dataset),
        demo=session.state.scoring.partition.demo,
        bench=bench,
    )
    store.write_export(hop, export)


def _render_review_md(session: Session, config: CampaignConfig) -> None:
    store = session.store.campaigns
    index = store.load(session.hop, moment=None)
    rounds = _banked_rounds(session, session.hop)
    if not index or not rounds:
        return
    cycle_dir = store.cycle_dir(session.hop)
    campaign = store.load_campaign(session.campaign_id)
    framing = campaign_framing(session.store, config, session.dataset)
    content = render_review_md(
        index,
        rounds,
        round_audits=round_audits(cycle_dir, [r.round for r in rounds]),
        context_object=[
            framing.pipeline_purpose,
            framing.optimization_goals,
            framing.key_challenges,
        ],
        fitness_ceiling=config.fitness_ceiling,
        optimizer=select_optimizer(config.optimization),
        # Read only once the cycle has ended: nothing renders the headline before then.
        bench=read_cycle_bench(session.store, campaign, session.hop)
        if index.final is not None and campaign is not None
        else None,
        spend=scan_ledger_spend(ledger_chain(CycleDir(cycle_dir), moment=None)).spend,
    )
    write_text(CycleLayout(cycle_dir).review_md, content)
