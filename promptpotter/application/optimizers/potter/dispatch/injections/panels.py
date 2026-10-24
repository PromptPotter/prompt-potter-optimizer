from __future__ import annotations

import math
import re
import zlib
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise, zip_longest
from typing import Any, NamedTuple

from promptpotter.application.intelligence.rasch import expected_score_gain
from promptpotter.application.optimizers.potter.dispatch.bundle import (
    ANSWER_LABEL_STEM,
    ANSWER_TALLY_ROWS,
    DEGENERATE_TRACE_CAP,
    INNER_NARRATIVE_CAP,
    LOOP_NOVELTY,
    LOST_CELL_MIN,
    MEMORY_FIELD_CAP,
    MEMORY_ROUND_CAP,
    MEMORY_VALUE_CAP,
    MISS_GT_CAP,
    MISS_NOTE_CAP,
    MISS_PREDICTED_CAP,
    MISS_QUERY_CAP,
    MISS_ROWS_CAP,
    MISS_STEP_CAP,
    MISS_TIED_CAP,
    NEAR_MISS_RENDER_CAP,
    NODE_FAILURE_RENDER_CAP,
    SAMPLE_RENDER_CAP,
    TRANSCRIPT_PREDICTED_CAP,
    TRANSCRIPT_QUERY_CAP,
    TRANSCRIPT_REASONING_CAP,
    TRANSCRIPT_RENDER_CAP,
    InjectionBundle,
    InjectionKind,
    Item,
    signal,
)
from promptpotter.application.optimizers.potter.escalation.state import ExplorationBudget
from promptpotter.application.optimizers.potter.pobb.checks import EliminationGate
from promptpotter.application.scoring.evaluators import DEFAULT_CELL_FORMULA, compute_accuracy
from promptpotter.application.scoring.paired import FlippedCells, flipped_keys
from promptpotter.application.scoring.row_diagnostics import judge_readings
from promptpotter.domain.candidate_diff import (
    IDEA_MATCH_MARK,
    candidate_delta,
    candidate_idea,
    changed_words,
    flatten_sp_summary,
    same_idea,
)
from promptpotter.domain.connector import MeasuredUnit, unit_count, unit_plural
from promptpotter.domain.l4.inner_facts import InnerCellFacts
from promptpotter.domain.opt_search_point import Locus, NodePrompt
from promptpotter.domain.paired_reading import ArmKey, LiftSide, PairedReading
from promptpotter.domain.results import (
    ROUND_ADVANCE_INFO,
    Concluded,
    Cut,
    Measured,
    RoundResult,
    ScoreSummary,
    arm_elimination,
    overlap_line,
    triggered_rates,
)
from promptpotter.domain.results_health import evidence_starved_node, ran_to_output_ceiling
from promptpotter.domain.ruler import ThetaCaveat
from promptpotter.domain.scoring import (
    CellSheet,
    GradedCell,
    all_verifier_graded,
    enumerable_truth_labels,
    is_hit,
)
from promptpotter.domain.search_point import SHOTS_KEY
from promptpotter.domain.spend import TokenAccount
from promptpotter.shared.answer_text import tail
from promptpotter.shared.composite import render_composite_fitness_block
from promptpotter.shared.statistics import min_detectable_effect


@signal(
    "escalation_panel",
    kind=InjectionKind.DERIVED,
    char_cap=None,
    citable=True,
)
def _r_escalation_panel(b: InjectionBundle) -> list[Item]:
    cs = b.cycle_slice
    budget = cs.exploration_budget
    guidance = {
        ExplorationBudget.TIGHT: "build on the parent, its method open to rewrite — "
        "stall_exploration citations are rejected",
        ExplorationBudget.NORMAL: "stalling — stall_exploration citations are permitted",
        ExplorationBudget.WIDE: "explore freely — a PEAKED axis may be mutated with an "
        "exploration_budget=wide rebut",
    }[ExplorationBudget(budget)]
    return [
        Item(
            f"ESCALATION: exploration_budget={budget} "
            f"(L1 stall: {cs.l1_stall_depth} rounds) — {guidance}"
        )
    ]


@signal(
    "evidence_health",
    kind=InjectionKind.DERIVED,
    char_cap=None,
    citable=True,
)
def _r_evidence_health(b: InjectionBundle) -> list[Item]:
    rates = b.digest.node_failure_rates
    if not rates:
        return []
    ranked = sorted(rates.items(), key=lambda kv: kv[1], reverse=True)
    unit = unit_plural(b.measured_unit)
    lines = [
        f"  {node}: failed on {rate:.0%} of {unit}"
        for node, rate in ranked[:NODE_FAILURE_RENDER_CAP]
    ]
    body = "PIPELINE NODE FAILURES (round-level):\n" + "\n".join(lines)
    if starved := evidence_starved_node(rates):
        worst_rate = rates[starved]
        body += (
            f"\nEVIDENCE STARVED — '{starved}' failed on {worst_rate:.0%} of {unit} this "
            "round. That is an upstream/backend fault (e.g. quota / rate-limit exhausted), NOT a "
            "prompt problem L1 can fix. Do not propose a param change to chase it: name the dead "
            "node in priority_fix and flag that this round's measurement is unreliable."
        )
    return [Item(body)]


@signal(
    "diagnostics",
    kind=InjectionKind.DERIVED,
    char_cap=None,
    citable=True,
)
def _r_diagnostics(b: InjectionBundle) -> list[Item]:
    sections: list[Item] = []
    cs = b.cycle_slice
    # The round number is `budget_state`'s, the L1 stall EVOLUTION's: each said once.
    if fires := [
        f"{tag} fired {layer.fires}x (stall {layer.stall_count})"
        for tag, layer in (("L2", cs.ladder.l2), ("L3", cs.ladder.l3))
        if layer.fires > 0
    ]:
        sections.append(Item("STATUS: " + " · ".join(fires)))

    d = b.digest.diagnostics
    if d is None:
        return [*sections, *_evolution(b)]
    parts: list[str] = []

    miss_samples = (
        []
        if _no_labels(b)
        else [
            s
            for s in d.samples
            if not is_hit(s.fitness)
            and (s.rank is not None or s.gt_in_source is not None or s.gt_in_ranked is not None)
        ][:SAMPLE_RENDER_CAP]
    )
    if miss_samples:
        s_lines = [f"SAMPLE DIAGNOSTICS ({len(miss_samples)}/{len(d.samples)} misses shown):"]
        for s in miss_samples:
            rank_str = f"r={s.rank}" if s.rank is not None else "no rank"
            extras = []
            if s.gt_in_source is not None:
                extras.append(f"gt_in_source={s.gt_in_source}")
            if s.gt_in_ranked is not None:
                extras.append(f"gt_in_ranked={s.gt_in_ranked}")
            extra_str = f" | {', '.join(extras)}" if extras else ""
            s_lines.append(
                f"  MISS [{s.terminal_node}] {s.query[:70]} → {s.predicted}"
                f" (GT: {s.ground_truth[:60]}, {rank_str}{extra_str})"
            )
        parts.append("\n".join(s_lines))

    if d.near_misses:
        nm_lines = [f"NEAR MISSES ({len(d.near_misses)} — GT in candidates but not r=1):"]
        for nm in d.near_misses[:NEAR_MISS_RENDER_CAP]:
            nm_lines.append(
                f"  [r={nm.rank}] {nm.query} → predicted: {nm.predicted} (GT: {nm.ground_truth})"
            )
        parts.append("\n".join(nm_lines))

    if parts:
        sections.extend(Item(p, trusted=False) for p in parts)

    if d.n_valid:
        rb = d.rank_buckets
        # A rank below 1, never "has a ranker?": `llm_only` maps its one output onto a width-1 list.
        discriminated = any(rb.get(k, 0) for k in ("2-5", "6-10", "11-20"))
        if discriminated:
            rank_line = (
                f"RANK DISTRIBUTION ({d.n_valid} queries): "
                f"r=1: {rb.get('1', 0)} | r=2-5: {rb.get('2-5', 0)} | "
                f"r=6-10: {rb.get('6-10', 0)} | r=11-20: {rb.get('11-20', 0)} | "
                f"not_found: {rb.get('not_found', 0)}"
            )
            if d.top_k_accuracy:
                rank_line += "\n  " + " | ".join(
                    f"top-{k}: {v:.0%}" for k, v in sorted(d.top_k_accuracy.items())
                )
            sections.append(Item(rank_line))

    # Never a `terminal_node` tally: it counts the node each sample REACHED, a healthy round included.
    if d.error_rate > 0 or d.warning_rate > 0:
        sections.append(
            Item(
                "PIPELINE HEALTH:\n"
                f"  error_rate: {d.error_rate:.0%} | warning_rate: {d.warning_rate:.0%}"
            )
        )

    if (yielded := b.digest.l1_yield) is not None and yielded != 1.0:
        sections.append(Item(f"POPULATION: yield={yielded:.2f}"))

    return [*sections, *_evolution(b)]


_SIDE_VERDICT: dict[LiftSide, str] = {
    "above": "separated above",
    "below": "separated BELOW",
    "spans": "NOT separated",
}


def _evolution(b: InjectionBundle) -> list[Item]:
    """On the origin panel's fixed rows, never a round's own accuracy: its cells move every round."""
    rounds = [rr for rr in b.measured_rounds if rr.round > 0]
    if not rounds:
        return []
    labels = " · ".join(
        f"r{rr.round} {ROUND_ADVANCE_INFO[rr.overlap.advance].label}" for rr in rounds[-5:]
    )
    lines = [
        f"EVOLUTION — L1 stall: {b.cycle_slice.l1_stall_depth} rounds since the best-so-far line "
        f"last advanced on the origin panel.\n  {labels}"
    ]
    line, overlap = next(
        (
            (line, rr.overlap)
            for rr in reversed(b.measured_rounds)
            if (line := overlap_line(rr.overlap))
        ),
        ([], None),
    )
    if overlap is None:
        return [Item("\n".join(lines))]
    n = len(overlap.positions)
    lines.append(
        f"  origin panel, {n} fixed rows: "
        + " → ".join(f"{m.arm.label} {m.rate:.0%}" for m in line)
    )
    if (headline := overlap.lead.headline) is not None:
        est = headline.estimate
        lines.append(
            f"  standing pick vs C0: lift [{est.ci_lo:+.2f}, {est.ci_hi:+.2f}], "
            f"{_SIDE_VERDICT[est.side]}"
        )
    if (parent_line := _vs_parent(overlap.vs_parent)) is not None:
        lines.append(parent_line)
    if triggered := triggered_rates(overlap):
        lines.append(
            "  read, never adopted: " + ", ".join(f"{m.arm.label} {m.rate:.0%}" for m in triggered)
        )
    return [Item("\n".join(lines))]


def _vs_parent(reading: PairedReading | None) -> str | None:
    lift = None if reading is None else reading.on_whole_set
    if reading is None or lift is None or reading.a is None or reading.b is None:
        return None
    pick, parent = reading.b.address.arm, reading.a.address.arm
    if pick is None or parent is None:
        return None
    est, flips = lift.estimate, lift.flips
    moved = (
        f"lift [{est.ci_lo:+.2f}, {est.ci_hi:+.2f}]"
        if flips is None
        else f"of {flips.gained + flips.lost + flips.unchanged} rows, wins {flips.gained} the "
        f"parent missed and misses {flips.lost} it won"
    )
    return f"  {pick.label} vs its parent {parent.label}: {moved}, {_SIDE_VERDICT[est.side]}"


_AXIS_MEMORY_LABEL_ORDER: tuple[str, ...] = (
    "axis_rankings",
    "top_values",
    "exhausted_axes",
    "persistent_failures",
    "failure_clusters",
)


def _critique_is_all_prompt_field(b: InjectionBundle) -> bool:
    critique = b.digest.critique
    sa = (critique.get("suggested_axes") if critique else None) or []
    if not sa:
        return False
    prompt_axes = {Locus(n.name, f).name for n in b.prompt_nodes for f in n.prompt_fields}
    return all(a in prompt_axes for a in sa)


def _filter_axis_rankings_to_prompt(value: str) -> str:
    if not value:
        return value
    kept: list[str] = []
    for entry in value.split("; "):
        name = entry.split(" (", 1)[0].strip()
        tail = name.rsplit(".", 1)[-1]
        if tail == "prompt":
            kept.append(entry)
    return "; ".join(kept)


@signal(
    "axis_memory",
    kind=InjectionKind.DERIVED,
    char_cap=None,
    citable=True,
)
def _r_axis_memory(b: InjectionBundle) -> list[Item]:
    if b.axes is None:
        return []
    digest = b.axes.digest()
    if not digest:
        return []
    semantic = _critique_is_all_prompt_field(b)
    header = "AXIS MEMORY (cross-cycle observations from MeasurementArchive):"
    if semantic:
        header = (
            "AXIS MEMORY (cross-cycle observations from MeasurementArchive — "
            "CRITIQUE IS SEMANTIC: param-axis rankings hidden, target a prompt-field axis):"
        )
    lines = [header]
    for key in _AXIS_MEMORY_LABEL_ORDER:
        val = digest.get(key)
        if val is None:
            continue
        if semantic:
            if key == "top_values":
                continue
            if key == "axis_rankings":
                val = _filter_axis_rankings_to_prompt(val)
                if not val:
                    continue
        label = key.replace("_", " ")
        lines.append(f"  {label}: {val}")
    return [Item("\n".join(lines) if len(lines) > 1 else "")]


def _query_stem(cell: GradedCell, n: int = 70) -> str:
    q = cell.facts.query.replace("\n", " ").strip()
    return q[:n]


def _head_at_line(text: str, cap: int) -> str:
    """Collapses blank lines: the façade truncates on them, so quoted content must never mint one."""
    text = re.sub(r"\n\s*\n+", "\n", text.strip())
    if len(text) <= cap:
        return text
    head = text[:cap]
    nl = head.rfind("\n")
    if nl > 0:
        head = head[:nl]
    return head + "\n[…truncated]"


def _edges_at_line(text: str, cap: int, head_frac: float = 0.55) -> str:
    """Head+tail: a trace's quotable wrong step is usually its conclusion, which a head-keep drops."""
    text = re.sub(r"\n\s*\n+", "\n", text.strip())
    if len(text) <= cap:
        return text
    head = text[: int(cap * head_frac)]
    nl = head.rfind("\n")
    if nl > 0:
        head = head[:nl]
    tail = text[len(text) - (cap - len(head)) :]
    nl = tail.find("\n")
    if 0 <= nl < len(tail) - 1:
        tail = tail[nl + 1 :]
    return f"{head}\n[…middle elided]\n{tail}"


class _TranscriptPlan(NamedTuple):
    shown: list[GradedCell]
    losses: dict[str, _Loss]
    misses: int
    lost: int


def _transcript_plan(b: InjectionBundle) -> _TranscriptPlan | None:
    if _inner_narrated(b):
        return None
    lost = _repeatedly_lost(_edits(b))
    lost_keys = {key for key, _ in lost}
    rows = [r for r in _misses(b) if r.key not in lost_keys]
    if not rows and not lost:
        return None
    # Rotated by round: the pool's order froze at round 0, so freshness alone serves one head forever.
    latest = b.digest.latest_sample_keys
    fresh = [r for r in rows if r.key in latest]
    stale = [r for r in rows if r.key not in latest]
    if fresh:
        off = (b.cycle_slice.round_num * TRANSCRIPT_RENDER_CAP) % len(fresh)
        fresh = fresh[off:] + fresh[:off]
    losing = [loss.run for _, loss in lost if loss.run is not None]
    pool = [r for pair in zip_longest(fresh + stale, losing) for r in pair if r is not None]
    return _TranscriptPlan(pool[:TRANSCRIPT_RENDER_CAP], dict(lost), len(rows), len(lost))


def _looping(trace: str) -> bool:
    if len(trace) <= DEGENERATE_TRACE_CAP:
        return False
    half = len(trace) // 2
    added = len(zlib.compress(trace.encode())) - len(zlib.compress(trace[:half].encode()))
    return added / (len(trace) - half) < LOOP_NOVELTY


def _trace_excerpt(trace: str) -> str:
    if _looping(trace):
        return "[looping — head and tail only]\n" + _edges_at_line(trace, DEGENERATE_TRACE_CAP)
    return _edges_at_line(trace, TRANSCRIPT_REASONING_CAP)


@signal(
    "sample_transcripts",
    kind=InjectionKind.MEASUREMENT,
    char_cap=None,
    citable=True,
)
def _r_sample_transcripts(b: InjectionBundle) -> list[Item]:
    if (plan := _transcript_plan(b)) is None:
        return []
    unit = unit_plural(b.measured_unit)
    pools = [
        text
        for n, text in (
            (plan.lost, f"{plan.lost} {unit} the parent's run hit that edits keep missing"),
            (plan.misses, f"{plan.misses} {unit} the parent still misses"),
        )
        if n
    ]
    header = (
        f"SAMPLE TRANSCRIPTS ({', and '.join(pools)} — each trace is the LAST run to miss that "
        "row, so read it as a live failure mode rather than this round's score. Quote the broken "
        "reasoning step, not just the label):"
    )
    sections = [Item(header)]
    for r in plan.shown:
        parts = [
            f"[#{b.slot_of[r.key]}] QUERY:\n{_head_at_line(r.facts.query, TRANSCRIPT_QUERY_CAP)}"
        ]
        if (loss := plan.losses.get(r.key)) is not None:
            parts.append(
                f"THE PARENT'S RUN HIT THIS — {loss.lost} of {loss.tried} edits missed it; "
                f"this is {loss.by}'s run."
            )
        if trace := r.facts.pipeline.reasoning_trace:
            parts.append(f"MODEL REASONING:\n{_trace_excerpt(trace)}")
        if r.facts.verifier_graded:
            parts.append(f"VERIFIER SCORE: {r.grade.fitness}")
        else:
            scored, reply = _answer_read(r, TRANSCRIPT_PREDICTED_CAP)
            lines = [] if scored is None else [f"SCORED: {scored}"]
            lines.append(f"GROUND TRUTH: {r.facts.ground_truth[:60]}")
            if reply is not None:
                lines.append(f"REPLY ENDS: {reply}")
            parts.append("\n".join(lines))
        if ran_to_output_ceiling(r.facts):
            parts.append(
                "RAN TO THE OUTPUT CEILING — the reply hit its token limit before it finished."
            )
        if verdict := _judge_verdict(r):
            parts.append(verdict)
        sections.append(Item("\n".join(parts), trusted=False, quotes=frozenset({r.key})))
    return sections


def _answer_read(cell: GradedCell, cap: int) -> tuple[str | None, str | None]:
    span, reply = cell.grade.span, tail(cell.facts.predicted, cap)
    if span is None:
        return None, reply
    return span[:cap] or "(no answer span)", None if reply == " ".join(span.split()) else reply


def _judge_verdict(cell: GradedCell) -> str:
    readings = judge_readings(cell.facts)
    if not readings:
        return ""
    name, label, why = readings[0]
    return f"JUDGE ({name}): {label} — {why[:200]}"


# Normal two-sided ~95% over one cell's two level SEs, which carry no n to take a t from.
_CELL_SEPARATION_SIGMAS = 2.0


def _inner_narrated(b: InjectionBundle) -> list[tuple[GradedCell, InnerCellFacts]]:
    """A series caveat withholds a seed's θ, never its narrative."""
    return [
        (r, inner)
        for r in b.trajectory_results
        if (inner := r.facts.pipeline.inner) is not None and r.facts.pipeline.reasoning_trace
    ]


def _paired_cell(
    inner: InnerCellFacts, origin: InnerCellFacts | None
) -> tuple[float | None, float | None]:
    lift = inner.mean_round_delta
    base = None if origin is None else origin.mean_round_delta
    if lift is None or origin is None or base is None:
        return (lift, None)
    se, base_se = inner.mean_parent_level_se, origin.mean_parent_level_se
    # No bar where an arm went unpriced: a floored cell adopts no levels to have an SE over.
    bar = (se**2 + base_se**2) ** 0.5 if se is not None and base_se is not None else None
    return (lift - base, bar)


def _paired_bench(r: GradedCell, origin: GradedCell | None) -> float | None:
    """The scored term, paired the same way; it carries no SE, so the θ pair keeps the ranking."""
    own = r.facts.pipeline.mean_bench_lift
    base = origin.facts.pipeline.mean_bench_lift if origin is not None else None
    if own is None or base is None:
        return None
    return own - base


class _NarratedCell(NamedTuple):
    upper: float | None
    theta: float | None
    bar: float | None
    bench: float | None
    paired: bool
    cell: GradedCell


def _narrated_cells(b: InjectionBundle) -> list[_NarratedCell]:
    """Ranked on the upper bound, since a point-estimate rank leads with noise; a seed with no θ follows."""
    origin = {r.key: r for r in b.origin_per_sample}
    cells = []
    for r, inner in _inner_narrated(b):
        o = origin.get(r.key)
        # The origin's own measurement pairs with nothing: differenced with itself it reads `+0.000`, no null.
        paired = o is not None and o is not r and o.facts.answer != r.facts.answer
        base = None if o is None else o.facts.pipeline.inner
        d, bar = (
            _paired_cell(inner, base)
            if paired
            else (inner.mean_round_delta, inner.mean_parent_level_se)
        )
        bench = _paired_bench(r, o) if paired else r.facts.pipeline.mean_bench_lift
        upper = None if d is None else d + _CELL_SEPARATION_SIGMAS * (bar or 0.0)
        cells.append(_NarratedCell(upper, d, bar, bench, paired, r))
    cells.sort(key=lambda c: (c.upper is None, 0.0 if c.upper is None else c.upper))
    return cells


def clustered_sample_ids(b: InjectionBundle) -> list[int]:
    """The rows a critique cluster may span, and the only ones a panel marks ``[#id]``: the misses shown and the cells edits keep losing, or one level up the narrated cells."""
    if narrated := _narrated_cells(b):
        return [b.slot_of[c.cell.key] for c in narrated]
    return [
        b.slot_of[key]
        for key in (
            *(r.key for _, r in _shown_misses(b)),
            *(key for key, _ in _repeatedly_lost(_edits(b))),
        )
    ]


@signal(
    "inner_narratives",
    kind=InjectionKind.MEASUREMENT,
    char_cap=None,
    citable=True,
)
def _r_inner_narratives(b: InjectionBundle) -> list[Item]:
    cells = _narrated_cells(b)
    if not cells:
        return []
    unit = b.measured_unit
    edited = [c for c in cells if c.paired and c.upper is not None]
    n_worse = sum(1 for c in edited if c.upper is not None and c.upper < 0.0)
    header = (
        f"INNER RUN NARRATIVES ({len(cells)} inner campaigns, one per outer {unit}, weakest first "
        "— those are the ones with something to learn from, so ground every candidate in a "
        "specific observation from one of them. `bench` is that seed's held-out lift under the "
        "optimizer prompts that ran it (the scored term), `th` the search pool's with ± its error "
        "bar (— where its series differences nothing). A line marked `vs origin` ran under an "
        "EDITED optimizer prompt and reads as the "
        "DIFFERENCE from the same seed under the unedited prompts, so the seed's own difficulty "
        "is already subtracted"
    )
    if edited and n_worse:
        header += f"; {n_worse} of those {len(edited)} are worse than the origin beyond their own error bar"
    elif edited:
        header += (
            f"; none of those {len(edited)} separates from the origin beyond its own error bar, "
            "so their ORDER carries no information"
        )
    sections = [Item(header + ":")]
    for c in cells:
        r = c.cell
        label = str(r.facts.query or "inner")[:80]
        bench = f"bench {c.bench:+.3f} · " if c.bench is not None else ""
        theta = (
            "th —"
            if c.theta is None
            else f"th {c.theta:+.3f}" + (f" ±{c.bar:.3f}" if c.bar is not None else " (unpriced)")
        )
        mark = f"{bench}{theta}" + (" vs origin" if c.paired else "")
        trace = r.facts.pipeline.reasoning_trace or ""
        sections.append(
            Item(
                f"[#{b.slot_of[r.key]}] {label} {mark}\n{_head_at_line(trace, INNER_NARRATIVE_CAP)}",
                trusted=False,
            )
        )
    return sections


def _misses(b: InjectionBundle) -> list[GradedCell]:
    """An errored sample is not a miss: the measurement never happened, so no mutation wins it back."""
    return [r for r in b.trajectory_results if not r.hit and not r.facts.errored]


def _no_labels(b: InjectionBundle) -> bool:
    return all_verifier_graded(r.facts.ground_truth for r in b.trajectory_results)


def _errored(b: InjectionBundle) -> list[GradedCell]:
    return [r for r in b.trajectory_results if r.facts.errored]


def _predicted_counts(cells: Sequence[GradedCell]) -> Counter[str]:
    # A formula reading no answer text compares the reply itself.
    said = (c.facts.predicted if c.grade.span is None else c.grade.span for c in cells)
    return Counter(answer for answer in said if answer)


def _tally(counts: Counter[str], total: int, *, rows: int | None = None) -> str:
    ordered = counts.most_common(rows) if rows is not None else counts.most_common()
    parts = [f"{lbl[:ANSWER_LABEL_STEM]} {n} ({100 * n / total:.0f}%)" for lbl, n in ordered]
    if len(counts) > len(ordered):
        parts.append(f"(+{len(counts) - len(ordered)} other answers)")
    return " | ".join(parts)


def _labelled(cells: Iterable[GradedCell]) -> list[GradedCell]:
    return [c for c in cells if c.facts.ground_truth]


def _constant_answer(truth: Counter[str]) -> tuple[str, float]:
    label, n = truth.most_common(1)[0]
    return label, n / sum(truth.values())


@signal(
    "answer_distribution",
    kind=InjectionKind.MEASUREMENT,
    char_cap=None,
    citable=True,
)
def _r_answer_distribution(b: InjectionBundle) -> list[Item]:
    rows = _labelled(b.trajectory_results)
    # `None`: no repeated label to be constant about (free text, or identity-keyed answers).
    truth = enumerable_truth_labels(rows)
    if truth is None:
        return []
    said = _predicted_counts(rows)
    n = len(rows)

    top_label, constant = _constant_answer(truth)
    # Mean fitness, never a `fitness >= 1.0` count, which reads 0.00 on every graded scorer.
    scored = compute_accuracy(results=rows)
    if scored is None:
        return [Item("")]

    tally = (
        f"  you answer : "
        f"{_tally(said, n, rows=ANSWER_TALLY_ROWS) if said else '(nothing parsed)'}"
        + chr(10)
        + f"  the truth  : {_tally(truth, n)}"
    )
    return [
        Item(f"ANSWER DISTRIBUTION (over the {unit_count(n, b.measured_unit)} scored so far):"),
        Item(tally, trusted=False),
        Item(
            f'  Answering "{top_label}" to EVERY {b.measured_unit} would score {constant:.2f}. '
            f"You score {scored:.2f}."
        ),
    ]


def _miss_difficulty(b: InjectionBundle, cell: GradedCell) -> float | None:
    ruler = b.ruler
    if ruler is None:
        return None
    return ruler.delta.get(cell.key)


def _verifier_outcome(cell: GradedCell) -> str:
    pd = cell.facts.pipeline
    parts = [f"score {cell.grade.fitness}"]
    if note := pd.outcome_note:
        parts.append(note[:MISS_NOTE_CAP])
    spent = [f"{len(turns)} turns"] if (turns := pd.turns) else []
    if (account := TokenAccount.from_step_tokens(pd.step_tokens)) is not None:
        spent.append(f"{account.total / 1000:.0f}k tokens")
    if spent:
        parts.append(", ".join(spent))
    return "".join(f" | {p}" for p in parts)


def _ordered_misses(b: InjectionBundle) -> list[tuple[float | None, GradedCell]]:
    """Easiest first on the cycle's ruler; a miss the ruler does not carry trails, unordered."""
    scored = [(_miss_difficulty(b, r), r) for r in _misses(b)]
    graded = sorted(((d, r) for d, r in scored if d is not None), key=lambda dr: dr[0])
    return [*graded, *((None, r) for d, r in scored if d is None)]


def _cluster_rows(b: InjectionBundle) -> frozenset[int]:
    critique = b.digest.critique or {}
    return frozenset(i for c in critique.get("failure_highlights") or [] for i in c["sample_ids"])


def _words(text: str) -> tuple[str, ...]:
    return tuple(re.findall(r"\w+", text.lower()))


def _contains(outer: tuple[str, ...], inner: tuple[str, ...]) -> bool:
    n = len(inner)
    return any(outer[i : i + n] == inner for i in range(len(outer) - n + 1))


def _extent_can_miss(b: InjectionBundle) -> bool:
    """A label answer has no extent to overlap."""
    return not _no_labels(b) and not enumerable_truth_labels(b.trajectory_results)


def _span_near_miss(cell: GradedCell) -> bool:
    """The answer and the truth share a contiguous word span: the reasoning landed, the extent did not."""
    said, true = _words(cell.grade.span or ""), _words(cell.facts.ground_truth)
    if not said or not true:
        return False
    return _contains(said, true) if len(said) >= len(true) else _contains(true, said)


def _tied_deltas(ordered: Sequence[tuple[float | None, GradedCell]]) -> frozenset[float]:
    """A continuous fit produces no ties, so a δ shared by several cells is the ruler's prior."""
    return frozenset(d for d, n in Counter(d for d, _ in ordered if d is not None).items() if n > 1)


def _shown_misses(b: InjectionBundle) -> list[tuple[float | None, GradedCell]]:
    """Tied and uncarried rows are capped: with no order among them, more repeat the claim."""
    ordered = _ordered_misses(b)
    if clustered := _cluster_rows(b):
        return [(d, r) for d, r in ordered if b.slot_of[r.key] in clustered]
    extent = _extent_can_miss(b)
    near = [(d, r) for d, r in ordered if extent and _span_near_miss(r)]
    near_keys = {r.key for _, r in near}
    tied = _tied_deltas(ordered)
    rest = [(d, r) for d, r in ordered if r.key not in near_keys]
    read = [(d, r) for d, r in rest if d is not None and d not in tied]
    pinned = [(d, r) for d, r in rest if d is not None and d in tied][:MISS_TIED_CAP]
    uncarried = [(d, r) for d, r in rest if d is None]
    if b.ruler is not None:
        uncarried = uncarried[:MISS_TIED_CAP]
    return [*near, *read, *pinned, *uncarried][:MISS_ROWS_CAP]


def _miss_outcome(r: GradedCell) -> str:
    scored, reply = _answer_read(r, MISS_PREDICTED_CAP)
    return (
        ("" if scored is None else f" | scored: {scored}")
        + f" | true: {r.facts.ground_truth[:MISS_GT_CAP]}"
        + ("" if reply is None else f" | reply ends: {reply}")
    )


def _miss_row(
    b: InjectionBundle,
    r: GradedCell,
    delta: float | None,
    tied: frozenset[float],
    *,
    verifier: bool,
) -> Item:
    row = (
        f"  [#{b.slot_of[r.key]}] "
        + ("δ=?" if delta is None else ("δ=tied" if delta in tied else f"δ={delta:+.2f}"))
        + f" | {_query_stem(r, MISS_QUERY_CAP)}"
        + (_verifier_outcome(r) if verifier else _miss_outcome(r))
        + (" | RAN TO THE OUTPUT CEILING" if ran_to_output_ceiling(r.facts) else "")
    )
    if not (trace := r.facts.pipeline.reasoning_trace):
        return Item(row, trusted=False)
    # A row whose transcript is PLACED is not stepped twice; one the budget dropped keeps its step.
    stepped = f"{row} | last step: {tail(trace, MISS_STEP_CAP)}"
    return Item(stepped, trusted=False, if_quoted=(r.key, row))


@signal(
    "failing_samples",
    kind=InjectionKind.MEASUREMENT,
    char_cap=None,
    citable=True,
)
def _r_failing_samples(b: InjectionBundle) -> list[Item]:
    if _inner_narrated(b):
        return []
    verifier = _no_labels(b)
    rows = _misses(b)
    errored = _errored(b)
    if not rows:
        if not errored:
            return []
        return [
            Item(
                f"NOT MEASURED — {len(errored)}/{len(b.trajectory_results)} "
                f"{unit_plural(b.measured_unit)} errored before "
                "producing an answer. The measurement never happened: there is no miss here to win "
                "back and no prompt edit that reaches it. Do not propose a mutation to chase this "
                "round — treat its score as ABSENT, not as a failure."
            )
        ]
    ordered = _ordered_misses(b)
    tied = _tied_deltas(ordered)
    shown = _shown_misses(b)
    clustered = bool(_cluster_rows(b))
    extent = not clustered and _extent_can_miss(b)
    near = sum(1 for _, r in shown if extent and _span_near_miss(r))
    # One row per miss, never a tally by label: a cluster is a MECHANISM, and only the stem and
    # the last step can show one.
    rows_out = [_miss_row(b, r, delta, tied, verifier=verifier) for delta, r in shown]
    held = (
        ""
        if len(shown) == len(rows)
        else f", the {len(shown)} the critique clustered shown"
        if clustered
        else f", {len(shown)} shown"
    )
    order = [f"first the {near} whose answer overlaps the truth's span"] if near else []
    if any(d is not None for d, _ in ordered):
        order.append("easiest first on the cycle's fixed δ")
        if tied:
            order.append("δ=tied rows share the ruler's prior, no order")
    else:
        order.append("the difficulty ruler is still cold, so unordered")
    header = (
        f"FAILING SAMPLES ({len(rows)} still unsolved{held} — latest outcome per "
        f"{b.measured_unit} across every configuration tried; {'; '.join(order)}):"
    )
    out = [Item(header)]
    if ceiling := sum(1 for r in rows if ran_to_output_ceiling(r.facts)):
        out.append(
            Item(
                f"{ceiling} of the {len(rows)} RAN TO THE OUTPUT CEILING: each reply hit its token "
                "limit before it finished, so what was scored is wherever it stopped."
            )
        )
    out.extend(rows_out)
    if errored:
        out.append(
            Item(
                f"({unit_count(len(errored), b.measured_unit)} further errored before answering "
                "— not misses, and "
                "not reachable by a prompt edit; the measurement never happened there.)"
            )
        )
    return out


def _candidate_mutation(
    cand: Concluded,
    parent: Mapping[str, NodePrompt],
    parent_pp: dict[str, Any] | None,
    slot_of: Mapping[str, int],
) -> list[tuple[str, str]]:
    delta = candidate_delta(cand.individual.prompts, parent, cand.overlay, parent_pp)
    pp_nested: dict[str, Any] = {}
    for (node, param), value in delta.params.items():
        pp_nested.setdefault(node, {})[param] = value
    pairs = [(key, str(value)) for key, value in flatten_sp_summary(pp_nested).items()]
    pairs += [
        (
            locus.name,
            changed_words(parent.get(locus.node, NodePrompt()).fields.get(locus.key, ""), value),
        )
        for locus, value in delta.prompt.items()
    ]
    # In the DEMO POOL panel's ids, the only shot vocabulary L1 is shown.
    pairs += [
        (Locus(node, SHOTS_KEY).name, ", ".join(f"#{slot_of[k]}" for k in shots) or "none")
        for node, shots in delta.shots.items()
    ]
    return pairs[:MEMORY_FIELD_CAP]


@dataclass(frozen=True)
class _Edit:
    """Diffed against the round BEFORE its own: its own round's ``prompts`` is the winner's."""

    round: RoundResult
    key: ArmKey
    candidate: Concluded
    changed: list[tuple[str, str]]
    idea: frozenset[str]

    @property
    def reading(self) -> PairedReading | None:
        fit = self.round.fit(self.key)
        return None if fit is None else fit.vs_reference

    def flipped(self) -> FlippedCells[str]:
        reference = self.round.reference_rows(self.key)
        return flipped_keys(
            {cell.key: cell for cell in reference}, {cell.key: cell for cell in self.cells()}
        )

    def cells(self) -> CellSheet:
        """A concluded arm need not have measured rows: a refused one has none."""
        return self.round.rows(self.key)


def _edits(b: InjectionBundle) -> list[_Edit]:
    """Windowed on rounds that EDITED: C0 and a no-op variant carry a candidate that changed nothing."""
    by_round: list[list[_Edit]] = []
    for parent_round, rr in pairwise(b.measured_rounds):
        if (head := parent_round.head) is None:
            continue
        parent = head.individual
        edits = [
            _Edit(
                rr,
                key,
                cand,
                changed,
                candidate_idea(
                    cand.individual.prompts,
                    parent.prompts,
                    cand.overlay,
                    parent.pipeline_params,
                ),
            )
            for key, cand in rr.panel.items()
            if isinstance(cand, Cut | Measured)
            and (
                changed := _candidate_mutation(
                    cand, parent.prompts, parent.pipeline_params, b.slot_of
                )
            )
        ]
        if edits:
            by_round.append(edits)
    return [edit for edits in by_round[-MEMORY_ROUND_CAP:] for edit in edits]


@dataclass
class _Loss:
    tried: int = 0
    lost: int = 0
    run: GradedCell | None = None
    by: str = ""


def _repeatedly_lost(edits: list[_Edit]) -> list[tuple[str, _Loss]]:
    cells: dict[str, _Loss] = {}
    for edit in edits:
        delta = edit.flipped()
        for key in (*delta.kept, *delta.lost):
            cells.setdefault(key, _Loss()).tried += 1
        for key in delta.lost:
            cell = cells[key]
            cell.lost += 1
            cell.run = next(r for r in edit.cells() if r.key == key)
            cell.by = edit.candidate.label
    return sorted(
        ((key, c) for key, c in cells.items() if c.lost >= LOST_CELL_MIN),
        key=lambda item: (-item[1].lost, item[0]),
    )


def _candidate_fate(cand: Concluded, fold: ScoreSummary | None, unit: MeasuredUnit) -> str:
    kind = cand.kind if isinstance(cand, Cut) else None
    if kind == "invalid" or fold is None:
        return f"invalid — rejected before it cost a {unit}"
    if fold.total == 0:
        return f"never measured — no {unit_plural(unit)} scored, its 0% is absence of evidence"
    cut = f"cut at {fold.walked}/{cand.expected_samples} {unit_plural(unit)}"
    if kind == "broken":
        return f"{cut} — BROKEN: its measurements kept failing; charged to this candidate"
    if kind == "eliminated":
        elimination = arm_elimination(cand)
        if elimination is not None and elimination.context.get("gate") == EliminationGate.COLLAPSED:
            return f"{cut} — answered ONE label to every {unit}; a VERDICT on this idea, not a stopped measurement"
        return f"{cut} — P(best) fell below ε; measurement stopped, NOT a verdict"
    if kind == "skipped":
        return "skipped mid-run by the operator"
    return "scored in full"


def _theta_fit_on_formula(b: InjectionBundle) -> bool:
    formula = b.cycle_slice.composite_formula
    return formula is not None and formula != DEFAULT_CELL_FORMULA


@signal(
    "mutation_memory",
    kind=InjectionKind.DERIVED,
    char_cap=None,
    citable=True,
)
def _r_mutation_memory(b: InjectionBundle) -> list[Item]:
    # This cycle alone under either purpose; an arm's own search is what is compared.
    edits = _edits(b)
    if not edits:
        return []
    header = (
        "ALREADY TRIED (this cycle, newest round first — each edit, its score against the parent, "
        "and the parent's cells it gained and lost; a mutation measured and lost here does not "
        "improve by being proposed again; ↺ marks an idea already tried in an earlier round, in "
        "whatever field it was written into):"
    )
    # First match wins: ↺ names the EARLIEST round, true whatever rows the composition affords.
    lines: list[str] = []
    seen: list[tuple[int, frozenset[str]]] = []
    charged = _theta_fit_on_formula(b)
    for edit in edits:
        echoes = [r for r, prev in seen if same_idea(edit.idea, prev, threshold=IDEA_MATCH_MARK)]
        mark = f"  ↺ same idea as r{echoes[0]} (x{len(echoes) + 1})" if echoes else ""
        seen.append((edit.round.round, edit.idea))
        row = _edit_row(edit, b.measured_unit, b.slot_of, charged=charged)
        lines.append(f"  r{edit.round.round} {row}{mark}")
    lines.reverse()
    items = [Item(header), *(Item(ln, trusted=False) for ln in lines)]
    if lost := _repeatedly_lost(edits):
        cells = " · ".join(f"#{b.slot_of[key]} in {c.lost} of {c.tried}" for key, c in lost)
        items.append(Item(f"CELLS THE PARENT'S RUN HIT THAT EDITS KEEP MISSING: {cells} edits"))
    return items


def _edit_row(edit: _Edit, unit: MeasuredUnit, slot_of: Mapping[str, int], *, charged: bool) -> str:
    cand = edit.candidate
    mutation = "; ".join(f"{field}: {value[:MEMORY_VALUE_CAP]}" for field, value in edit.changed)
    fold = edit.round.folds.get(edit.key)
    # Quoted only where the arm covered its reference; short of that it is two levels over different cells.
    reading = edit.reading if fold is not None and fold.total else None
    parent = reading.reference_level("fitness") if reading else None
    scored = (
        f"{fold.accuracy:.0%} vs parent {parent:.0%}"
        if fold is not None and fold.accuracy is not None and parent is not None
        else _candidate_fate(cand, fold, unit)
    )
    composite = reading.reference_level("objective") if reading and charged else None
    if fold is not None and fold.composite_fitness is not None and composite is not None:
        scored += f", composite {fold.composite_fitness:.3f} vs {composite:.3f}"
    delta = edit.flipped()
    cells = "".join(
        f" · {verb} {', '.join(f'#{slot_of[key]}' for key in keys)}"
        for verb, keys in (("gained", delta.gained), ("lost", delta.lost))
        if keys
    )
    return f"{cand.label} {scored} · {mutation}{cells}"


@signal("measurand", kind=InjectionKind.DERIVED, char_cap=None, citable=True)
def _r_measurand(b: InjectionBundle) -> list[Item]:
    fitness = b.digest.composite_fitness
    if fitness is None:
        return []
    theta = ""
    if (a := b.digest.ability) is not None:
        se = f" ±{a.se:.3f}" if a.se is not None else ""
        theta = (
            f"; frontier θ {a.theta:+.3f}{se} ({a.scale(named=False)} — each "
            f"{b.measured_unit}'s latest cell, whoever measured it)"
        )
    if not _theta_fit_on_formula(b):
        # The cell formula IS the grade, so a fitness block would restate the θ line's input.
        return [
            Item(
                f"ELECTION — a round is won on θ lift over the parent by an arm winning more "
                f"shared cells than it loses{theta}. Round fitness {fitness:.3f}."
            )
        ]
    body = render_composite_fitness_block(
        fitness, b.digest.evaluators, b.cycle_slice.composite_formula
    )
    lines = [
        f"ELECTION — a round is won on θ lift over the parent by an arm winning more shared cells "
        f"than it loses, θ fit on each cell's composite under the formula below, not on "
        f"accuracy{theta}.",
        "REPORTED FITNESS — the headline number and the degradation scale:",
        *(f"  {ln}" for ln in body),
    ]
    return [Item("\n".join(lines))]


@signal("detectable_move", kind=InjectionKind.DERIVED, char_cap=None, citable=True)
def _r_detectable_move(b: InjectionBundle) -> list[Item]:
    a = b.digest.ability
    n = len(b.digest.latest_sample_keys)
    if a is None or a.se is None or a.se <= 0.0 or not n:
        return []
    # A contrast against the parent, so both arms' error enters; equal precision is assumed.
    mde = min_detectable_effect(a.se * math.sqrt(2.0))
    ruler = None if b.ruler is None else b.ruler.entries()
    rows = max(1, round(expected_score_gain(a.theta, mde, b.digest.latest_sample_keys, ruler)))
    return [
        Item(
            f"DETECTABLE MOVE — an edit must win ≈{rows} more of this round's {n} "
            f"{unit_plural(b.measured_unit)} than the parent to separate from it (95%/80%); "
            "smaller is noise, so prefer one large-effect edit to several small ones."
        )
    ]


@signal("confounds", kind=InjectionKind.DERIVED, char_cap=None, citable=True)
def _r_confounds(b: InjectionBundle) -> list[Item]:
    d = b.digest
    rows: list[str] = []
    a = d.ability
    caveat = a.caveat if a is not None else None
    if caveat is ThetaCaveat.DISJOINT_SCOPES:
        rows.append(
            f"DISJOINT DATASETS — this round's {unit_plural(b.measured_unit)} measure more than "
            "one dataset, and a difficulty is fitted within one only, so θ averages scales that "
            "never shared a cell. Read the lift paired on shared cells, never the level."
        )
    elif caveat is ThetaCaveat.COLD_RULER:
        rows.append(
            "COLD RULER — no difficulty scale yet, so θ is logit-accuracy, each arm read beside "
            "the parent on the cells both answered. Read each arm's lift, never a level."
        )
    elif a is not None and caveat in (ThetaCaveat.FLAT_RULER, ThetaCaveat.COLLAPSED_BAND):
        cause = (
            "the ruler itself spans almost nothing, so no draw could have been wider"
            if caveat is ThetaCaveat.FLAT_RULER
            else "the draw took a thin slice of a wider ruler"
        )
        rows.append(
            f"COLLAPSED BAND — the {unit_plural(b.measured_unit)} this round's θ was read on span "
            f"{a.round_span:.2f} logits on a ruler spanning {a.ruler_span:.2f}; {cause}. Inside a "
            f"band that narrow every {b.measured_unit} is equally hard, "
            "so θ is logit-accuracy plus a constant and ranking on it ranks on accuracy."
        )
    elif a is not None and caveat is ThetaCaveat.UNMEASURED_DELTA:
        rows.append(
            f"UNMEASURED DIFFICULTY — {a.unlinked} of this round's "
            f"{unit_plural(b.measured_unit)} carry no δ: no arm already on the ruler answered "
            "them, so θ leaves them out and each arm's θ is read on the rest. Read the lift, "
            "never the level."
        )
    elif a is not None and caveat is ThetaCaveat.PRIOR_PINNED and a.pinned_share is not None:
        rows.append(
            f"PRIOR-PINNED DIFFICULTY — {a.pinned_share:.0%} of this round's "
            f"{unit_plural(b.measured_unit)} sit on a δ the ruler hands to several cells at "
            "once. That is the prior, not a reading: every arm that ever saw them answered "
            "the same way, so nothing measured how hard they are. θ still counts them, and "
            "the value they are pinned to moves as the ruler grows — so a θ that rose since "
            "last round may be the scale shifting under an unchanged prompt rather than an "
            "arm improving. Read the lift, never the level."
        )
    if d.prev_sample_keys and not (d.latest_sample_keys & d.prev_sample_keys):
        rows.append(
            f"SUBSET MOVED WHOLE — this round shares no {b.measured_unit} with the one before it, "
            "so the two rounds' raw rates are two different exams."
        )
    if not rows:
        return []
    return [
        Item(
            "LIVE CAVEATS — states where the numbers above are not what they look like:\n"
            + "\n".join(f"  {r}" for r in rows)
        )
    ]


@signal("budget_state", kind=InjectionKind.DERIVED, char_cap=None, citable=False)
def _r_budget_state(b: InjectionBundle) -> list[Item]:
    cs = b.cycle_slice
    parts = [f"ROUND {cs.round_num}" + (f" of {cs.budget_rounds}" if cs.budget_rounds else "")]
    if cs.spend_budget_usd:
        used = f"${cs.spend_used_usd:.2f}" if cs.spend_used_usd is not None else "?"
        parts.append(f"spend {used} of ${cs.spend_budget_usd:.2f}")
    return [Item(" · ".join(parts))]
