from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise, zip_longest
from typing import Any, NamedTuple

from promptpotter.application.optimizers.potter.dispatch.bundle import (
    ANSWER_LABEL_STEM,
    ANSWER_TALLY_ROWS,
    INNER_NARRATIVE_CAP,
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
    NEAR_MISS_RENDER_CAP,
    NODE_FAILURE_RENDER_CAP,
    PRECISION_ARM_ROWS,
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
from promptpotter.application.scoring.classification import scoreable_rows
from promptpotter.application.scoring.evaluators import DEFAULT_CELL_FORMULA, compute_accuracy
from promptpotter.application.scoring.paired import FlippedCells, flipped_keys
from promptpotter.application.scoring.row_diagnostics import judge_readings
from promptpotter.application.views.render.primitives import fmt_pct
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
from promptpotter.domain.paired_reading import ArmKey, PairedReading
from promptpotter.domain.results import (
    Concluded,
    Cut,
    Measured,
    RoundResult,
    ScoreSummary,
    arm_elimination,
)
from promptpotter.domain.results_health import evidence_starved_node
from promptpotter.domain.ruler import ThetaCaveat
from promptpotter.domain.scoring import (
    ROW_GRADES,
    CellSheet,
    GradedCell,
    all_verifier_graded,
    enumerable_truth_labels,
    is_hit,
)
from promptpotter.domain.search_point import SHOTS_KEY
from promptpotter.domain.spend import TokenAccount
from promptpotter.shared.composite import render_composite_fitness_block
from promptpotter.shared.statistics import min_detectable_effect, paired_mean_t


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
    # No accuracy pair here: it would be a second copy of the EVOLUTION column.
    status: list[str] = [f"STATUS: round {cs.round_num}"]
    if cs.l1_stall_depth > 0:
        status.append(f"  L1 stall: {cs.l1_stall_depth} rounds")
    for tag, layer in (("L2", cs.ladder.l2), ("L3", cs.ladder.l3)):
        if layer.fires > 0:
            status.append(f"  {tag} fired: {layer.fires}x (stall: {layer.stall_count})")
    sections.append(Item("\n".join(status)))

    d = b.digest.diagnostics
    if d is None:
        return sections
    if d.anomalies:
        sections.append(Item("ANOMALIES:\n  " + "\n  ".join(d.anomalies)))
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
                f"  MISS [{s.terminal_node}] {s.query[:70]} → {s.predicted[:60]}"
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

    if d.cross_candidate_diff:
        parts.append(
            "MISSED OPPORTUNITIES (queries other candidates solved but winner missed):\n"
            + "\n".join(d.cross_candidate_diff)
        )

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

    if len(d.evolution_rows) > 1:
        line = f"TREND: {d.trend}"
        if d.trend_description:
            line += f" — {d.trend_description}"
        sections.append(Item(line))
        tbl = [
            "EVOLUTION (last rounds — acc is on THAT round's own cells, so its Δ is not a change;",
            "elected is the column that compares):",
            "  round  elected  acc      Δ       degraded",
        ]
        for row in d.evolution_rows[-5:]:
            tbl.append(
                f"  {row.round:>5}  {'yes' if row.elected else 'no':>7}  "
                f"{fmt_pct(row.accuracy):>6}  {fmt_pct(row.delta, '{:+.1%}'):>6}  "
                f"{row.degraded:>5}"
            )
        sections.append(Item("\n".join(tbl)))

    return sections


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


@signal(
    "sample_transcripts",
    kind=InjectionKind.MEASUREMENT,
    char_cap=None,
    citable=True,
)
def _r_sample_transcripts(b: InjectionBundle) -> list[Item]:
    if _inner_narrated(b):
        return []
    lost = _repeatedly_lost(_edits(b))
    lost_keys = {key for key, _ in lost}
    rows = [r for r in _misses(b) if r.key not in lost_keys]
    if not rows and not lost:
        return []
    # Rotated by round: the pool's order froze at round 0, so freshness alone serves one head forever.
    latest = b.digest.latest_sample_keys
    fresh = [r for r in rows if r.key in latest]
    stale = [r for r in rows if r.key not in latest]
    if fresh:
        off = (b.cycle_slice.round_num * TRANSCRIPT_RENDER_CAP) % len(fresh)
        fresh = fresh[off:] + fresh[:off]
    losses = dict(lost)
    misses = fresh + stale
    losing = [loss.run for _, loss in lost if loss.run is not None]
    pool = [r for pair in zip_longest(misses, losing) for r in pair if r is not None]
    unit = unit_plural(b.measured_unit)
    pools = [
        text
        for n, text in (
            (len(lost), f"{len(lost)} {unit} the parent's run hit that edits keep missing"),
            (len(rows), f"{len(rows)} {unit} the parent still misses"),
        )
        if n
    ]
    header = (
        f"SAMPLE TRANSCRIPTS ({', and '.join(pools)} — each trace is the LAST run to miss that "
        "row, so read it as a live failure mode rather than this round's score. Quote the broken "
        "reasoning step, not just the label):"
    )
    sections = [Item(header)]
    for r in pool[:TRANSCRIPT_RENDER_CAP]:
        parts = [
            f"[#{b.slot_of[r.key]}] QUERY:\n{_head_at_line(r.facts.query, TRANSCRIPT_QUERY_CAP)}"
        ]
        if (loss := losses.get(r.key)) is not None:
            parts.append(
                f"THE PARENT'S RUN HIT THIS — {loss.lost} of {loss.tried} edits missed it; "
                f"this is {loss.by}'s run."
            )
        if trace := r.facts.pipeline.reasoning_trace:
            parts.append(f"MODEL REASONING:\n{_edges_at_line(trace, TRANSCRIPT_REASONING_CAP)}")
        if r.facts.verifier_graded:
            parts.append(f"VERIFIER SCORE: {r.grade.fitness}")
        else:
            predicted = _head_at_line(r.facts.predicted, TRANSCRIPT_PREDICTED_CAP)
            gt = r.facts.ground_truth[:60]
            parts.append(f"PREDICTED: {predicted}\nGROUND TRUTH: {gt}")
        if verdict := _judge_verdict(r):
            parts.append(verdict)
        sections.append(Item("\n".join(parts), trusted=False))
    return sections


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
    return Counter(c.facts.predicted for c in cells if c.facts.predicted)


def _tally(counts: Counter[str], total: int, *, rows: int | None = None) -> str:
    ordered = counts.most_common(rows) if rows is not None else counts.most_common()
    parts = [f"{lbl[:ANSWER_LABEL_STEM]} {n} ({100 * n / total:.0f}%)" for lbl, n in ordered]
    if len(counts) > len(ordered):
        parts.append(f"(+{len(counts) - len(ordered)} other answers)")
    return " | ".join(parts)


def _labelled(cells: Iterable[GradedCell]) -> list[GradedCell]:
    return [c for c in cells if c.facts.ground_truth]


def _truth_labels(cells: Iterable[GradedCell]) -> Counter[str] | None:
    return enumerable_truth_labels(_labelled(cells))


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


def _shown_misses(b: InjectionBundle) -> list[tuple[float | None, GradedCell]]:
    """Unclustered, the ``MISS_ROWS_CAP`` easiest; once a critique clustered them, the rows its clusters span."""
    ordered = _ordered_misses(b)
    if clustered := _cluster_rows(b):
        return [(d, r) for d, r in ordered if b.slot_of[r.key] in clustered]
    return ordered[:MISS_ROWS_CAP]


def _last_step(trace: str, cap: int) -> str:
    """The tail: a trace's wrong step is usually its conclusion, and the transcript panel shows the head."""
    text = " ".join(trace.split())
    return text if len(text) <= cap else "…" + text[-cap:]


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
    graded = [d for d, _ in _ordered_misses(b) if d is not None]
    # A δ shared by a run of cells is the ruler's prior, not a reading: a tie carries no order.
    tied = {d for d, n in Counter(graded).items() if n > 1}
    shown = _shown_misses(b)
    # One row per miss, never a tally by label: a cluster is a MECHANISM, and only the stem and
    # the last step can show one.
    rows_out = [
        Item(
            f"  [#{b.slot_of[r.key]}] "
            + ("δ=?" if delta is None else ("δ=tied" if delta in tied else f"δ={delta:+.2f}"))
            + f" | {_query_stem(r, MISS_QUERY_CAP)}"
            + (
                _verifier_outcome(r)
                if verifier
                else f" | said: {r.facts.predicted[:MISS_PREDICTED_CAP]}"
                f" | true: {r.facts.ground_truth[:MISS_GT_CAP]}"
            )
            + (
                f" | last step: {_last_step(trace, MISS_STEP_CAP)}"
                if (trace := r.facts.pipeline.reasoning_trace)
                else ""
            ),
            trusted=False,
        )
        for delta, r in shown
    ]
    held = (
        ""
        if len(shown) == len(rows)
        else f", the {len(shown)} the critique clustered shown"
        if _cluster_rows(b)
        else f", the {len(shown)} easiest shown"
    )
    header = (
        f"FAILING SAMPLES ({len(rows)} still unsolved{held} — latest outcome per {b.measured_unit} "
        "across the configurations tried so far, not one round's score; "
        + (
            "difficulty δ from the cycle's fixed ruler; easiest first — the top rows are the "
            "winnable ones"
            if graded
            else "the difficulty ruler is still cold, so these are unordered"
        )
        + (
            ". `δ=tied` is the ruler's prior, not a reading of that cell — those rows carry no "
            "order among themselves and no claim that any is winnable"
            if tied
            else ""
        )
        + "):"
    )
    out = [Item(header), *rows_out]
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


@signal(
    "origin_strengths",
    kind=InjectionKind.MEASUREMENT,
    char_cap=None,
    citable=True,
)
def _r_origin_strengths(b: InjectionBundle) -> list[Item]:
    rows = b.origin_per_sample.cells
    if not rows:
        return []
    acc = compute_accuracy(results=rows)
    if acc is None:
        return []
    if (truth := _truth_labels(rows)) is not None:
        fits = [ROW_GRADES["fitness"].read(r) for r in scoreable_rows(rows)]
        _, floor = _constant_answer(truth)
        ci_lo = paired_mean_t(fits, [floor] * len(fits))[1]
        if ci_lo is None or ci_lo <= 0.0:
            return []
    return [
        Item(
            f"ORIGIN STRENGTHS: origin scores {acc:.0%} across "
            f"{unit_count(len(rows), b.measured_unit)} "
            "— preserve the parent scaffolding earning that."
        )
    ]


@signal(
    "archive_top_runs",
    kind=InjectionKind.MEASUREMENT,
    char_cap=None,
    citable=True,
)
def _r_archive_top_runs(b: InjectionBundle) -> list[Item]:
    if b.axes is None:
        return []
    runs = b.axes.top_runs(3)
    if not runs:
        return []
    lines = [f"HISTORICAL BEST (top {len(runs)} runs across the dataset's archive):"]
    for i, r in enumerate(runs, 1):
        lines.append(
            f"  #{i}  acc={r.accuracy:.1%}  comp={r.composite:.3f}  n={r.total}  run={r.individual}"
        )
    return [Item("\n".join(lines))]


@signal(
    "rare_hit_samples",
    kind=InjectionKind.MEASUREMENT,
    char_cap=None,
    citable=True,
)
def _r_rare_hit_samples(b: InjectionBundle) -> list[Item]:
    if b.axes is None:
        return []
    rare = b.axes.sample_index.rare_hit_samples(max_hits=3, min_observations=10)
    cracked = [row for row in rare if row[2] > 0]
    if not cracked:
        return []
    header = "RARE-HIT SAMPLES (cracked by ≤3 of ≥10 attempts — replicate the unlock pattern):"
    rows = [
        Item(
            # No `[#id]` mark: an archive intractable is no row of this round's clusters.
            f"  sample {sid}: {query}… → {hits}/{total} hit by "
            + ", ".join(pointer[:24] for pointer in cracked_by[:2]),
            trusted=False,
        )
        for sid, query, hits, total, cracked_by in cracked
    ]
    return [Item(header), *rows]


@signal("measurand", kind=InjectionKind.DERIVED, char_cap=None, citable=True)
def _r_measurand(b: InjectionBundle) -> list[Item]:
    fitness = b.digest.composite_fitness
    if fitness is None:
        return []
    body = render_composite_fitness_block(
        fitness, b.digest.evaluators, b.cycle_slice.composite_formula
    )
    lines = [
        "ELECTION — a round is won on θ lift over the parent, and θ is fit on each cell's "
        "composite under the formula below, not on accuracy."
        if _theta_fit_on_formula(b)
        else "ELECTION — a round is won on θ lift over the parent, and on nothing else."
    ]
    if (a := b.digest.ability) is not None:
        lines.append(f"  this round: θ {a.theta:+.3f}  ({a.scale(named=False)})")
    lines.append("REPORTED FITNESS — the headline number and the degradation scale:")
    lines.extend(f"  {ln}" for ln in body)
    return [Item("\n".join(lines))]


@signal("precision", kind=InjectionKind.DERIVED, char_cap=None, citable=True)
def _r_precision(b: InjectionBundle) -> list[Item]:
    d = b.digest
    rows: list[str] = []
    if (a := d.ability) is not None and a.se is not None:
        rows.append(f"ability θ {a.theta:+.3f} ±{a.se:.3f}  ({a.scale(named=False)})")
    for arm in d.arms[:PRECISION_ARM_ROWS]:
        if arm.mean_fitness_ci_lo is None or arm.mean_fitness_ci_hi is None:
            continue
        rows.append(
            f"{arm.label} accuracy in [{arm.mean_fitness_ci_lo:.3f}, {arm.mean_fitness_ci_hi:.3f}]"
            f" on {arm.scored_samples}/{arm.expected_samples} {unit_plural(b.measured_unit)}"
        )
    if not rows:
        return []
    return [
        Item(
            "PRECISION — what those numbers are known to within:\n"
            + "\n".join(f"  {r}" for r in rows)
        )
    ]


@signal("detectable_move", kind=InjectionKind.DERIVED, char_cap=None, citable=True)
def _r_detectable_move(b: InjectionBundle) -> list[Item]:
    se = b.digest.ability.se if b.digest.ability is not None else None
    if se is None or se <= 0.0:
        return []
    # A contrast against the parent, so both arms' error enters; equal precision is assumed.
    contrast_se = se * (2.0**0.5)
    mde = min_detectable_effect(contrast_se)
    return [
        Item(
            "DETECTABLE MOVE — the bar this round can actually clear:\n"
            f"  an edit must move ability by at least {mde:+.3f} logits to separate from the parent "
            f"(contrast se {contrast_se:.3f}, 95%/80%). Anything smaller is inside the noise however "
            "the column reads, so prefer one edit with a large expected effect to several small ones."
        )
    ]


@signal("sample_provenance", kind=InjectionKind.DERIVED, char_cap=None, citable=True)
def _r_sample_provenance(b: InjectionBundle) -> list[Item]:
    d, cs = b.digest, b.cycle_slice
    n = len(d.latest_sample_keys)
    if not n:
        return []
    unit = b.measured_unit
    budget = f" of a {cs.sp_budget_round}-{unit} budget" if cs.sp_budget_round else ""
    rows = [f"{unit_count(n, unit)} graded this round{budget}"]
    if cs.subset_mode == "adaptive":
        rows.append(
            "chosen ADAPTIVELY — the acquisition re-picks each round by information gain, so two "
            "consecutive rounds are two different exams and their raw rates are not a series"
        )
    elif cs.subset_mode == "frozen":
        rows.append("FROZEN — every round is graded on the same campaign-start prefix")
    if d.prev_sample_keys:
        rows.append(f"{len(d.latest_sample_keys & d.prev_sample_keys)} also graded last round")
    # A collapse has no rate to read, so "graded on a harder prefix" is false of it.
    stopped = ("eliminated", "broken")
    if cut := [a for a in d.arms if a.cut in stopped and a.gate != EliminationGate.COLLAPSED]:
        stops = ", ".join(f"{a.label} at {a.scored_samples}/{a.expected_samples}" for a in cut[:4])
        rows.append(
            f"stopped early: {stops} — a cut arm was graded on a harder prefix, so its rate says "
            "where it stopped, not how good it is"
        )
    if gone := [a for a in d.arms if a.gate == EliminationGate.COLLAPSED]:
        stops = ", ".join(f"{a.label} at {a.scored_samples}/{a.expected_samples}" for a in gone[:4])
        rows.append(
            f"stopped answering: {stops} — one label for every {unit}, so there is no rate to read"
        )
    return [Item("SAMPLE — what chose these rows:\n" + "\n".join(f"  {r}" for r in rows))]


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
    parts: list[str] = []
    if cs.budget_rounds:
        parts.append(f"round {cs.round_num} of {cs.budget_rounds}")
    if cs.spend_budget_usd:
        used = f"${cs.spend_used_usd:.2f}" if cs.spend_used_usd is not None else "?"
        parts.append(f"cap {used} of ${cs.spend_budget_usd:.2f}")
    if not parts:
        return []
    return [Item("BUDGET: " + " · ".join(parts))]
