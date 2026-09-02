from __future__ import annotations

import re
from collections import Counter
from typing import Any, cast

from promptpotter.application.optimization.dispatch.bundle import (
    ANSWER_LABEL_STEM,
    ANSWER_TALLY_ROWS,
    INNER_NARRATIVE_CAP,
    INNER_NARRATIVE_FULL_CELLS,
    INNER_NARRATIVE_RENDER_CAP,
    INNER_NARRATIVE_SUMMARY_CAP,
    MEMORY_FIELD_CAP,
    MEMORY_ROUND_CAP,
    MEMORY_VALUE_CAP,
    MISS_GT_CAP,
    MISS_PREDICTED_CAP,
    MISS_QUERY_CAP,
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
from promptpotter.application.scoring.evaluators import compute_accuracy
from promptpotter.connectors.protocol import MeasuredUnit, unit_count, unit_plural
from promptpotter.domain.candidate_diff import (
    IDEA_MATCH_MARK,
    candidate_delta,
    candidate_idea,
    flatten_sp_summary,
    same_idea,
)
from promptpotter.domain.escalation_signals import ExplorationBudget
from promptpotter.domain.l4.proxies import OUTER_PROXY_KEYS, PARENT_LEVEL_SE_KEY
from promptpotter.domain.results import CritiqueReadout, EliminationGate, ScoredCandidate
from promptpotter.domain.results_health import evidence_starved_node
from promptpotter.domain.ruler import ThetaCaveat, theta_caveat
from promptpotter.domain.scoring import QueryMeasurement, enumerable_truth_labels, is_hit
from promptpotter.shared.composite import render_composite_fitness_block
from promptpotter.shared.errors import is_error_result
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
        ExplorationBudget.TIGHT: "exploit the parent — stall_exploration citations are rejected",
        ExplorationBudget.NORMAL: "stalling — stall_exploration citations are permitted",
        ExplorationBudget.WIDE: "explore freely — a PEAKED axis may be mutated with an "
        "exploration_budget=wide rebut",
    }[ExplorationBudget(budget)]
    return [
        Item(
            f"ESCALATION: exploration_budget={budget} (L1 stall: {cs.l1_stall_count} rounds) — {guidance}"
        )
    ]


@signal(
    "evidence_health",
    kind=InjectionKind.DERIVED,
    char_cap=None,
    citable=True,
)
def _r_evidence_health(b: InjectionBundle) -> list[Item]:
    """A node failing on ≥ ``EVIDENCE_STARVED_RATE`` of samples is an upstream fault no param
    mutation can fix; the degradation grade reads the SAME helper. Empty on a healthy round."""
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
    # The typed predicate, never a second comparison against the same constant: the health
    # grade, the L2 router and `terminate_capability` all ask this one function.
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
    """Actionability-first, because the composition places items in order — least-actionable last
    is the first the ceiling refuses. Each part is its OWN item, and only the three that echo
    dataset text are untrusted; they sit contiguously, so the composition fences them once."""
    sections: list[Item] = []
    cs = b.cycle_slice
    status: list[str] = [
        f"STATUS: round {cs.round_num} | current {cs.current_accuracy:.1%} | "
        f"best {cs.best_accuracy:.1%} @ round {cs.best_round}"
    ]
    if cs.l1_stall_count > 0:
        status.append(f"  L1 stall: {cs.l1_stall_count} rounds")
    if cs.l2_round > 0:
        status.append(f"  L2 fired: {cs.l2_round}x (stall: {cs.l2_stall_count})")
    if cs.l3_round > 0:
        status.append(f"  L3 fired: {cs.l3_round}x (stall: {cs.l3_stall_count})")
    sections.append(Item("\n".join(status)))

    d = b.digest.diagnostics
    if d is None:
        return sections
    # Our OWN plateau classification, not dataset text — unfenced, like every numeric part below.
    if d.anomalies:
        sections.append(Item("ANOMALIES:\n  " + "\n  ".join(d.anomalies)))
    # The three that echo raw queries, predictions and ground truths. Contiguous, so one fence
    # covers them and the numeric parts after them need none.
    parts: list[str] = []

    # Same recursion split `_misses` applies, on the typed per-sample view: one level up a MISS
    # is an artifact of scoring a graded proxy against a placeholder label, and rank /
    # gt_in_source are empty there too, so every row is noise wearing a diagnosis.
    miss_samples = (
        []
        if _miss_is_placeholder(b)
        else [s for s in d.samples if not is_hit(s.fitness)][:SAMPLE_RENDER_CAP]
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
        # Renders only where ranking DISCRIMINATED — some ground truth below r=1. With every
        # rank at 1-or-absent the buckets are the hit/miss split wearing ranking vocabulary,
        # and that is the COMMON case: `llm_only` maps its one output onto `final_ranking`, so
        # the schema-level "has a ranker?" test reads True on a width-1 list.
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

    # PIPELINE HEALTH: rates only, and silent on a clean round — never a `terminal_node` tally,
    # which reports the node each sample REACHED and so reads as a mass failure on a healthy one.
    if d.error_rate > 0 or d.warning_rate > 0:
        sections.append(
            Item(
                "PIPELINE HEALTH:\n"
                f"  error_rate: {d.error_rate:.0%} | warning_rate: {d.warning_rate:.0%}"
            )
        )

    if d.l1_diversity != 1.0:
        sections.append(Item(f"POPULATION: diversity={d.l1_diversity:.2f}"))

    # TREND + EVOLUTION last (least actionable: historical narrative, first to be tail-cut).
    # Skipped at R1 — "too few rounds to classify" is dead weight.
    if len(d.evolution_rows) > 1:
        line = f"TREND: {d.trend}"
        if d.trend_description:
            line += f" — {d.trend_description}"
        sections.append(Item(line))
        tbl = ["EVOLUTION (last rounds):", "  round  acc      Δ       degraded"]
        for row in d.evolution_rows[-5:]:
            tbl.append(
                f"  {row.round:>5}  {row.accuracy:>6.1%}  {row.delta:>+6.1%}  {row.degraded:>5}"
            )
        sections.append(Item("\n".join(tbl)))

    return sections


# Effect-driven items first so attention lands on what to mutate; sample-side findings second;
# narrative tail last.
_AXIS_MEMORY_LABEL_ORDER: tuple[str, ...] = (
    "axis_rankings",
    "top_values",
    "exhausted_axes",
    "persistent_failures",
    "failure_clusters",
)


def _critique_is_all_prompt_field(critique: CritiqueReadout | None) -> bool:
    """All `critique.suggested_axes` are prompt-field axes? Drives axis-memory filter — when L1_CRITIQUE
    flagged only semantic failures, param-axis rankings are noise the critique already vetoed.
    """
    from promptpotter.config.settings import PROMPT_STRING_FIELDS

    sa = (critique.get("suggested_axes") if critique else None) or []
    if not sa:
        return False
    prompt_axes = set(PROMPT_STRING_FIELDS)
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
    char_cap=None,  # digest() already caps to top-5 axes; this is the hard backstop.
    citable=True,
)
def _r_axis_memory(b: InjectionBundle) -> list[Item]:
    """Critique-aware: when L1_CRITIQUE flagged only semantic failures, param rankings are noise it
    already vetoed. Sample-side rows stay — they suggest no axis mutation."""
    if b.axes is None:
        return []
    digest = b.axes.digest()
    if not digest:
        return []
    semantic = _critique_is_all_prompt_field(b.digest.critique)
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


def _query_stem(row: dict[str, Any], n: int = 70) -> str:
    q = (row.get("query") or "").replace("\n", " ").strip()
    return q[:n]


def _head_at_line(text: str, cap: int) -> str:
    """Cuts at a line boundary so a premise is never sliced mid-sentence, and collapses blank lines —
    the façade truncates on blank-line boundaries, so quoted content must never mint one."""
    text = re.sub(r"\n\s*\n+", "\n", text.strip())
    if len(text) <= cap:
        return text
    head = text[:cap]
    nl = head.rfind("\n")
    if nl > 0:
        head = head[:nl]
    return head + "\n[…truncated]"


def _edges_at_line(text: str, cap: int, head_frac: float = 0.55) -> str:
    """Head+tail: for a reasoning trace the decisive wrong step is usually the CONCLUSION, which a
    pure head-keep drops first — starving the critique of the step it must quote."""
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
    # Sized for TRANSCRIPT_RENDER_CAP typical transcripts; a worst-case overrun degrades by
    # section-dropping the whole last transcript, never by severing a fence.
    char_cap=None,
    citable=True,
)
def _r_sample_transcripts(b: InjectionBundle) -> list[Item]:
    """Silent on the recursion — the mirror of ``inner_narratives``, which is silent off it."""
    if _miss_is_placeholder(b):
        return []
    rows = _misses(b)
    if not rows:
        return []
    # Freshest first, then ROTATE. Freshness alone is a boolean over a pool whose insertion order
    # froze at round 0, so a stable sort served the same head for a cycle's whole life: measured
    # over the banked corpus, three of four runs of one cell handed their critique the identical
    # transcript triple three rounds running — one of them the same 2,001 characters each time.
    # A critique cannot name a cluster it is never shown, so the repeated diagnosis was the
    # correct answer to a question asked three times. Rotating the eligible group by round spends
    # the cap on a different slice each round; the cumulative-pool contract the header states is
    # unchanged, only which of its still-unsolved rows get the deep read.
    latest = b.digest.latest_sample_ids
    fresh = [r for r in rows if r.get("sample_id") in latest]
    stale = [r for r in rows if r.get("sample_id") not in latest]
    if fresh:
        off = (b.cycle_slice.round_num * TRANSCRIPT_RENDER_CAP) % len(fresh)
        fresh = fresh[off:] + fresh[:off]
    shown = (fresh + stale)[:TRANSCRIPT_RENDER_CAP]
    header = (
        f"SAMPLE TRANSCRIPTS ({len(shown)}/{len(rows)} still-unsolved "
        f"{unit_plural(b.measured_unit)}, shown complete — "
        "each trace is the LAST configuration to miss that row, not necessarily the parent, so "
        "read it as a live failure mode rather than this round's score. Quote the broken reasoning "
        "step, not just the label):"
    )
    sections = [Item(header)]
    for r in shown:
        sid = r.get("sample_id")
        parts = [
            f"[#{sid}] QUERY:\n{_head_at_line(str(r.get('query') or ''), TRANSCRIPT_QUERY_CAP)}"
        ]
        trace = (r.get("pipeline_data") or {}).get("reasoning_trace") or ""
        if trace:
            # head+tail, not head-keep: the wrong CONCLUSION is the quotable step.
            parts.append(
                f"MODEL REASONING:\n{_edges_at_line(str(trace), TRANSCRIPT_REASONING_CAP)}"
            )
        predicted = _head_at_line(str(r.get("predicted") or ""), TRANSCRIPT_PREDICTED_CAP)
        gt = str(r.get("ground_truth") or "")[:60]
        parts.append(f"PREDICTED: {predicted}\nGROUND TRUTH: {gt}")
        sections.append(Item("\n".join(parts), trusted=False))
    return sections


# A cell is called WORSE only when its paired difference clears this many of its own SEs. Two —
# the two-sided ~95% bound, and the same bar `round_not_separable` applies one level up, so this
# panel cannot rank a cell as beaten that the loop itself would refuse to call separable.
_CELL_SEPARATION_SIGMAS = 2.0


def _pd_number(r: dict[str, Any], key: str) -> float | None:
    """A real number off ``pipeline_data``. A zero-lift seed — the one an edit most needs to see —
    arrives as int 0, so accept any real number but not bool."""
    v = (r.get("pipeline_data") or {}).get(key)
    return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else None


def _inner_narrated(b: InjectionBundle) -> list[tuple[float, dict[str, Any]]]:
    """Rows carrying an inner run's narrative. ``inner_narratives`` renders exactly these,
    ``sample_transcripts`` renders only when there are none — so neither node is left with no panel.
    Which world we are in is DECLARED (``b.measured_unit``); this only picks the rows."""
    return [
        (lift, r)
        for r in b.trajectory_results
        if (lift := _pd_number(r, OUTER_PROXY_KEYS[0])) is not None
        and (r.get("pipeline_data") or {}).get("reasoning_trace")
    ]


def _paired_cell(
    lift: float, r: dict[str, Any], origin: dict[str, Any] | None
) -> tuple[float, float | None]:
    """This cell's ``(candidate − origin)`` difference and the error bar on it."""
    # No bar when either arm went unpriced — a FLOORED cell adopts no levels to have an SE over,
    # and it is the one cell whose badness is not in question, so it ranks on its value alone.
    base = _pd_number(origin, OUTER_PROXY_KEYS[0]) if origin else None
    if origin is None or base is None:
        return (lift, None)
    se, base_se = _pd_number(r, PARENT_LEVEL_SE_KEY), _pd_number(origin, PARENT_LEVEL_SE_KEY)
    # Quadrature, for the reason `panel_precision` gives: each arm carries its OWN half, the
    # shared origin LEVEL already cancelled by the subtraction beside it.
    bar = (se**2 + base_se**2) ** 0.5 if se is not None and base_se is not None else None
    return (lift - base, bar)


@signal(
    "inner_narratives",
    kind=InjectionKind.MEASUREMENT,
    # Bounded by the render cap and the two per-section caps rather than by a total, so the
    # panel's size follows its evidence instead of the seed count.
    char_cap=None,
    citable=True,
)
def _r_inner_narratives(b: InjectionBundle) -> list[Item]:
    """Each row as its PAIRED difference from the origin on the same seed, ranked worst-CONFIDENT
    first."""
    # The rank decides which cells spend their FULL narrative, so a rank read off the point
    # estimate alone spends the budget on whichever cell noise put first — the measured gaps are
    # routinely narrower than either bar. Ranking on the upper bound keeps a wide cell out of the
    # lead. Where nothing clears its own bar the header says the order carries no information, and
    # the leading cells still narrate: the panel's job is to hand over what the inner loop DID,
    # which does not stop being evidence because the cells cannot be ranked against each other.
    origin = {sid: r for r in b.origin_per_sample if (sid := r.get("sample_id")) is not None}
    scored = _inner_narrated(b)
    if not scored:
        return []
    # `upper` — how bad the cell is AFTER its own uncertainty — is computed once and carried, so
    # the rank key and the separation test cannot come apart. It decides the ORDER and nothing
    # else — which cells are worth reading is a separate question from which cell is worst.
    cells = []
    for lift, r in scored:
        # At the ORIGIN both arms are the same rows, so the paired difference is a cell against
        # itself — `+0.000` on every seed, which reads as a measured null and is not one. The
        # seed's own lift is the measurement there, carrying its own SE rather than a difference's.
        d, bar = (
            (lift, _pd_number(r, PARENT_LEVEL_SE_KEY))
            if b.is_origin_round
            else _paired_cell(lift, r, origin.get(r.get("sample_id")))
        )
        cells.append((d + _CELL_SEPARATION_SIGMAS * (bar or 0.0), d, bar, r))
    cells.sort(key=lambda c: c[0])
    n_worse = sum(1 for c in cells if c[0] < 0.0)
    unit = b.measured_unit
    # The weakest earn their section; the tail is named, never silently dropped — a panel that
    # says "24 inner campaigns" and renders six has told the generator something false.
    shown = cells[:INNER_NARRATIVE_RENDER_CAP]
    held = (
        f", the {len(shown)} weakest shown and {len(cells) - len(shown)} not rendered"
        if len(shown) < len(cells)
        else ""
    )
    if b.is_origin_round:
        header = (
            f"INNER RUN NARRATIVES ({len(cells)} inner campaigns, one per outer {unit}{held}. This is "
            "the ORIGIN round: the optimizer prompts are UNEDITED, so each number is that seed's "
            "own lift under them and ± is its error bar. There is no edit to compare against yet, "
            "so do not read these as gains over anything. Weakest first — those are the ones with "
            "something to learn from; ground every candidate in a specific observation from one "
            "of them:"
        )
        full_cells = INNER_NARRATIVE_FULL_CELLS
    else:
        header = (
            f"INNER RUN NARRATIVES ({len(cells)} inner campaigns this round, one per outer "
            f"{unit}{held}. "
            "Each number is that seed's lift MINUS what the same seed scored under the unedited "
            "optimizer prompts, so the seed's own difficulty is already subtracted; ± is the error "
            "bar on that difference)"
        ) + (
            f". The first {n_worse} are worse than the origin by more than their own error bar — "
            "those are the ones with something to learn from; ground every candidate in a specific "
            "observation from one of them:"
            if n_worse
            else f". NOT ONE {unit} separates from the origin beyond its own error bar, so their "
            f"ORDER here carries no information — do not ground an edit in a {unit}'s rank. Read "
            "the narratives for what the inner loop actually did:"
        )
        # Separated cells earn the depth — they are the ones with something to learn from. Where
        # NONE separates, no cell being privileged is a reason to spend the depth evenly rather
        # than withhold it: both consumers are instructed to ground an edit in a narrative
        # observation, so a panel of bare stat lines leaves them citing the critique instead.
        full_cells = (
            min(n_worse, INNER_NARRATIVE_FULL_CELLS) if n_worse else INNER_NARRATIVE_FULL_CELLS
        )
    sections = [Item(header)]
    for i, (_upper, d, bar, r) in enumerate(shown):
        label = str(r.get("query") or r.get("sample_id") or "inner")[:80]
        mark = f"{d:+.3f}" + (f" ±{bar:.3f}" if bar is not None else " (unpriced)")
        trace = str((r.get("pipeline_data") or {}).get("reasoning_trace") or "")
        cap = INNER_NARRATIVE_CAP if i < full_cells else INNER_NARRATIVE_SUMMARY_CAP
        sections.append(Item(f"[{label}] {mark}\n{_head_at_line(trace, cap)}", trusted=False))
    return sections


def _misses(b: InjectionBundle) -> list[dict[str, Any]]:
    """An errored sample is NOT a miss — the measurement never happened (``is_error_result``), so no
    mutation can win it back, and rendered as one it invents a task deficiency to attack."""
    return [
        r for r in b.trajectory_results if not is_hit(r.get("fitness")) and not is_error_result(r)
    ]


def _miss_is_placeholder(b: InjectionBundle) -> bool:
    """Whether a MISS here means anything. One level up the outer "answer" is the seed's own identity
    token plus a proxy suffix, so ``predicted`` can never equal ``ground_truth`` and EVERY sample reads
    as a miss — a split that partitions nothing. Panels contrasting misses against hits ask this and
    stay silent; ``inner_narratives`` renders the same rows as paired lifts, which is what they are."""
    return bool(_inner_narrated(b))


def _errored(b: InjectionBundle) -> list[dict[str, Any]]:
    return [r for r in b.trajectory_results if is_error_result(r)]


def _label_counts(rows: list[dict[str, Any]], key: str) -> Counter[str]:
    return Counter(str(v) for r in rows if (v := r.get(key)) not in (None, ""))


def _tally(counts: Counter[str], total: int, *, rows: int | None = None) -> str:
    """Bounded HERE, at the production site. ``rows=None`` renders the whole tally — that is the
    TRUTH line, a value space already bounded to ``ANSWER_SPACE_CAP`` distinct labels, and
    clipping a value space would hide part of what the reader is being asked to answer within.
    The PREDICTED line passes a row count, because it is an OBSERVATION and was bounded by
    nothing: a hedging pipeline emits long, near-unique strings, one bucket each, and the panel
    then overran a cap sized for a label set. Either way the label is a STEM — collapse is
    visible in which labels dominate, not in every spelling of a run-on answer."""
    ordered = counts.most_common(rows) if rows is not None else counts.most_common()
    parts = [f"{lbl[:ANSWER_LABEL_STEM]} {n} ({100 * n / total:.0f}%)" for lbl, n in ordered]
    if len(counts) > len(ordered):
        parts.append(f"(+{len(counts) - len(ordered)} other answers)")
    return " | ".join(parts)


@signal(
    "answer_distribution",
    kind=InjectionKind.MEASUREMENT,
    char_cap=None,
    citable=True,
)
def _r_answer_distribution(b: InjectionBundle) -> list[Item]:
    """The collapse detector: accuracy alone cannot tell a pipeline that reasons from one emitting the
    same label every time, and nothing else in the prompt carries that fact. Empty on free text."""
    rows = [r for r in b.trajectory_results if r.get("ground_truth") not in (None, "")]
    # `None` = no repeated label to be constant about (free-text, or identity-keyed answers such
    # as an L4 outer round's per-seed tokens). Same rule the scorer's collapse gate reads.
    truth = enumerable_truth_labels(rows)
    if truth is None:
        return []
    said = _label_counts(rows, "predicted")
    n = len(rows)

    top_label, top_n = truth.most_common(1)[0]
    constant = top_n / n
    # Mean fitness — the SAME quantity `accuracy` reports, and the only one comparable to the
    # constant-answer floor beside it. A `fitness >= 1.0` count reads 0.00 on every graded
    # scorer, telling the generator a constant answer beat it while the run is climbing.
    scored = compute_accuracy(results=cast("list[QueryMeasurement]", rows))
    if scored is None:
        return [Item("")]  # nothing scoreable — no score to compare the floor against

    # Header and the constant-answer verdict are OUR statements about the run; only the tally
    # lines echo dataset labels, so they are the one untrusted item and the composition fences
    # exactly them.
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


def _miss_difficulty(b: InjectionBundle, row: dict[str, Any]) -> float | None:
    """This miss's δ on the cycle's locked ruler — ``None`` while the ruler is cold or the
    sample is off it. A 2PL entry carries ``(δ, a)``; only δ is a difficulty."""
    ruler = b.ruler
    sid = row.get("sample_id")
    if ruler is None or sid is None:
        return None
    return ruler.delta.get(int(sid))


@signal(
    "failing_samples",
    kind=InjectionKind.MEASUREMENT,
    char_cap=None,
    citable=True,
)
def _r_failing_samples(b: InjectionBundle) -> list[Item]:
    """Ordered easiest-first — the one thing here L1 cannot compute for itself. A cold ruler renders
    the misses unordered rather than quoting a difficulty that would move next round."""
    if _miss_is_placeholder(b):
        return []
    rows = _misses(b)
    errored = _errored(b)
    if not rows:
        # Silence would leave the generator with no account of the round, so the panel reports
        # the non-measurement in its own voice — unfenced, being a statement about
        # PromptPotter's state rather than dataset content the fence tells L1 to distrust.
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
    scored = [(_miss_difficulty(b, r), r) for r in rows]
    graded = [(d, r) for d, r in scored if d is not None]
    ungraded = [r for d, r in scored if d is None]
    graded.sort(key=lambda dr: dr[0])
    ordered: list[tuple[float | None, dict[str, Any]]] = [
        *graded,
        *((None, r) for r in ungraded),
    ]
    ruled = "difficulty δ from the cycle's fixed ruler; easiest first — the top rows are the "
    cold = "the difficulty ruler is still cold, so these are unordered"
    # One item per miss, ordered easiest-first — how many fit is the composition's call. A row
    # budget here would pre-decide a size against a ceiling this panel cannot see, and would hand
    # back one block the composition can only starve whole rather than thin.
    rows_out = [
        Item(
            f"  [#{r.get('sample_id')}] "
            + (f"δ={delta:+.2f}" if delta is not None else "δ=?")
            + f" | {_query_stem(r, MISS_QUERY_CAP)}"
            + f" | said: {str(r.get('predicted') or '')[:MISS_PREDICTED_CAP]}"
            + f" | true: {str(r.get('ground_truth') or '')[:MISS_GT_CAP]}",
            trusted=False,
        )
        for delta, r in ordered
    ]
    # States what the panel HAS; what it SHOWED is the composition's line, written after the
    # selection this cannot see.
    header = (
        f"FAILING SAMPLES ({len(rows)} still unsolved — latest outcome per {b.measured_unit} "
        "across the configurations tried so far, not one round's score; "
        + (f"{ruled}winnable ones" if graded else cold)
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
    cand: ScoredCandidate, parent: dict[str, Any], parent_pp: dict[str, Any] | None
) -> list[tuple[str, str]]:
    """Values are returned UNCLIPPED — the render clips for the eye, and clipping here starves any
    reader needing the whole value. The delta rule is the shared ``candidate_delta`` dedup hashes."""
    pf, pp = candidate_delta(cand.prompt_fields, parent, cand.pipeline_params_override, parent_pp)
    pp_nested: dict[str, Any] = {}
    for (node, param), value in pp.items():
        pp_nested.setdefault(node, {})[param] = value
    pairs = [(key, str(value)) for key, value in flatten_sp_summary(pp_nested).items()]
    pairs += [(field, str(value)) for field, value in pf.items() if value]
    return pairs[:MEMORY_FIELD_CAP]


def _candidate_fate(cand: ScoredCandidate, unit: MeasuredUnit) -> str:
    """Not keyed on ``partial_reason``: its ``pobb`` arm is dead, so every eliminated candidate on
    disk carries the empty string.

    ``elimination_stopped`` covers gates that mean OPPOSITE things to a generator, so it asks which
    fired. ε stops BUYING — the idea may still be good and deserves re-proposing; a collapse is a
    VERDICT, the arm having answered one label to everything. Rendered as the ε sentence, the
    strongest rejection the loop has read as "not a verdict" and the dead idea stayed live in this
    very panel — the failure ``answer_distribution`` exists to prevent, one panel over."""
    if cand.invalid:
        return f"invalid — rejected before it cost a {unit}"
    if cand.total == 0:
        # `accuracy` defaults to 0.0, so an unmeasured candidate is byte-identical to one that
        # got everything wrong and must never be quoted as an outcome.
        return f"never measured — no {unit_plural(unit)} scored, its 0% is absence of evidence"
    if cand.elimination_stopped:
        cut = f"cut at {cand.scored_samples}/{cand.expected_samples} {unit_plural(unit)}"
        if cand.elimination_context.get("gate") == EliminationGate.COLLAPSED:
            return f"{cut} — answered ONE label to every {unit}; a VERDICT on this idea, not a stopped measurement"
        return f"{cut} — P(best) fell below ε; measurement stopped, NOT a verdict"
    if cand.escalation_aborted:
        return "aborted mid-run"
    return "scored in full"


@signal(
    "mutation_memory",
    kind=InjectionKind.DERIVED,
    char_cap=None,
    citable=True,
)
def _r_mutation_memory(b: InjectionBundle) -> list[Item]:
    """ONE compact line per prior candidate, NEWEST round first, bounded by the composition
    here so the cap downstream never has to cut — recognition, not reproduction. A candidate that
    changed nothing is not an attempt."""
    prior = list(b.prior_rounds)
    # Oldest first, and built for EVERY prior round BEFORE the retained window is taken: a
    # round's row count is not knowable from the round (C0 and a no-op variant both carry a
    # candidate that changed nothing), so windowing on rounds that merely HAVE candidates
    # spends a retained slot rendering nothing.
    by_round: list[tuple[int, list[tuple[str, frozenset[str]]]]] = []
    for i, rr in enumerate(prior):
        parent = rr.prompt_fields
        # The PRIOR round's resolved params — the parent this round mutated from.
        parent_pp = prior[i - 1].pipeline_params if i > 0 else None
        attempts: list[tuple[str, frozenset[str]]] = []
        for cand in rr.candidate_scores:
            changed = _candidate_mutation(cand, parent, parent_pp)
            if not changed:
                continue
            mutation = [f'{field}: "{value[:MEMORY_VALUE_CAP]}"' for field, value in changed]
            # `total == 0` is checked BEFORE the paired quote: a never-measured candidate can
            # still carry a `matched_parent_accuracy` (the parent was scored even though the
            # candidate was not), and would otherwise render a comparison out of nothing.
            scored = (
                f"{cand.accuracy:.0%} vs parent {cand.matched_parent_accuracy:.0%}"
                if cand.total and cand.matched_parent_accuracy is not None
                else _candidate_fate(cand, b.measured_unit)
            )
            attempts.append(
                (
                    f"{scored} · {'; '.join(mutation)}",
                    candidate_idea(
                        cand.prompt_fields, parent, cand.pipeline_params_override, parent_pp
                    ),
                )
            )
        if attempts:
            by_round.append((rr.round, attempts))
    if not by_round:
        return []
    header = (
        "ALREADY TRIED (this cycle, most recent round first — a mutation measured and lost here "
        "does not improve by being proposed again; ↺ marks an idea already tried in an earlier "
        "round, in whatever field it was written into):"
    )
    rows = [
        (round_num, body, fp)
        for round_num, attempts in by_round[-MEMORY_ROUND_CAP:]
        for body, fp in attempts
    ]
    # Every attempt is offered, newest-first below, so what the ceiling drops is the OLDEST — the
    # row L1 is least likely to re-propose. The ↺ marker names a ROUND rather than a rendered row,
    # so it stays true whether or not that row was afforded.
    kept = list(rows)
    # (round, fingerprint) per rendered row, oldest first — the pool each later row is matched
    # against. First match wins, so a marker points at the EARLIEST occurrence rather than the
    # previous link. Matched only within what SURVIVED, so it never names a round the panel hides.
    lines: list[str] = []
    seen: list[tuple[int, frozenset[str]]] = []
    for round_num, body, fp in kept:
        echoes = [r for r, prev in seen if same_idea(fp, prev, threshold=IDEA_MATCH_MARK)]
        mark = f"  ↺ same idea as r{echoes[0]} (x{len(echoes) + 1})" if echoes else ""
        seen.append((round_num, fp))
        lines.append(f"  r{round_num} {body}{mark}")
    lines.reverse()
    return [Item(header), *(Item(ln, trusted=False) for ln in lines)]


@signal(
    "origin_strengths",
    kind=InjectionKind.MEASUREMENT,
    char_cap=None,
    citable=True,
)
def _r_origin_strengths(b: InjectionBundle) -> list[Item]:
    """Reports the origin's MEAN fitness, never a count of maxed-out samples: the count silenced the
    panel on every graded scorer, which is the one thing a don't-strip-this warning must never do."""
    rows = b.origin_per_sample
    if not rows:
        return []
    acc = compute_accuracy(results=cast("list[QueryMeasurement]", rows))
    if acc is None:
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
        label = r.name or r.run_id
        lines.append(
            f"  #{i}  acc={r.accuracy:.1%}  comp={r.composite:.3f}  n={r.total}  run={label}"
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
    # A row that was never cracked carries no unlock pattern to replicate, which is this panel's
    # whole offer. All-zero it rendered as a header promising one over six rows saying there is
    # none — the reader is told to engineer for nothing, at full token price. Show the cracked
    # rows and let the never-cracked ones be silent; with none cracked the panel says nothing.
    cracked = [row for row in rare if row[2] > 0]
    if not cracked:
        return []
    # No row cap: how many of these fit is the composition's question, not this panel's, and
    # the rows are ordered so the ones it keeps are the ones worth keeping.
    header = "RARE-HIT SAMPLES (cracked by ≤3 of ≥10 attempts — replicate the unlock pattern):"
    rows = [
        Item(
            f"  [#{sid}] {query}… → {hits}/{total} hit by "
            + ", ".join(rid[:24] for rid in hit_run_ids[:2]),
            trusted=False,
        )
        for sid, query, hits, total, hit_run_ids in cracked
    ]
    return [Item(header), *rows]


# --- The decision frame -------------------------------------------------------------------
# Six short panels answering what the evidence panels cannot: what is being optimized, how well it
# is known, what would count as a move, what chose the rows, and when the numbers are not ability.
# Each self-suppresses where it has nothing to say, and together they cost less than a tenth of one
# transcript — so the question of whether to serve them is never a budget question.


@signal("measurand", kind=InjectionKind.DERIVED, char_cap=None, citable=True)
def _r_measurand(b: InjectionBundle) -> list[Item]:
    """What ELECTS, then what is reported. ``elect_round_winner`` admits and ranks on θ lift over
    the parent alone; composite fitness grades degradation and headlines the round. Named as one
    objective, a formula carrying a term the election does not read steers at nothing."""
    fitness = b.digest.composite_fitness
    if fitness is None:
        return []
    body = render_composite_fitness_block(
        fitness, b.digest.evaluators, b.cycle_slice.composite_formula
    )
    lines = ["ELECTION — a round is won on θ lift over the parent, and on nothing else."]
    if (a := b.digest.ability) is not None:
        lines.append(f"  this round: θ {a.theta:+.3f}  ({a.scale()})")
    lines.append("REPORTED FITNESS — the headline number and the degradation scale:")
    lines.extend(f"  {ln}" for ln in body)
    return [Item("\n".join(lines))]


@signal("precision", kind=InjectionKind.DERIVED, char_cap=None, citable=True)
def _r_precision(b: InjectionBundle) -> list[Item]:
    """What the objective is known to WITHIN. A level with no interval beside it invites reading a
    move that sits inside its own error bar as a result — which is most moves."""
    d = b.digest
    rows: list[str] = []
    if (a := d.ability) is not None and a.se is not None:
        rows.append(f"ability θ {a.theta:+.3f} ±{a.se:.3f}  ({a.scale()})")
    for arm in d.arms[:PRECISION_ARM_ROWS]:
        if arm.mean_fitness_ci_lo is None or arm.mean_fitness_ci_hi is None:
            continue
        rows.append(
            f"{arm.label} fitness in [{arm.mean_fitness_ci_lo:.3f}, {arm.mean_fitness_ci_hi:.3f}]"
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
    """The smallest gain this round could tell from zero. Without it an edit is proposed against no
    bar at all, and every move inside the noise reads as a result."""
    se = b.digest.ability.se if b.digest.ability is not None else None
    if se is None or se <= 0.0:
        return []
    # A CONTRAST, not a level: an edit is judged against the parent, so both arms' error enters.
    # Equal precision is the honest approximation while the round carries one SE.
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
    """WHAT CHOSE THE ROWS. A rate is set as much by which rows were graded, and by where an arm
    stopped, as by the configuration under test."""
    d, cs = b.digest, b.cycle_slice
    n = len(d.latest_sample_ids)
    if not n:
        return []
    unit = b.measured_unit
    budget = f" of a {cs.sp_budget_ttest}-{unit} budget" if cs.sp_budget_ttest else ""
    rows = [f"{unit_count(n, unit)} graded this round{budget}"]
    if cs.subset_mode == "adaptive":
        rows.append(
            "chosen ADAPTIVELY — the acquisition re-picks each round by information gain, so two "
            "consecutive rounds are two different exams and their raw rates are not a series"
        )
    elif cs.subset_mode == "frozen":
        rows.append("FROZEN — every round is graded on the same campaign-start prefix")
    if d.prev_sample_ids:
        rows.append(f"{len(d.latest_sample_ids & d.prev_sample_ids)} also graded last round")
    # "Graded on a harder prefix" is true of an ε, lock-in or degradation cut and FALSE of a
    # collapse, which has no rate to read — and saying it invites the re-proposal `_candidate_fate`
    # refuses, from the same prompt.
    if cut := [a for a in d.arms if a.elimination_stopped and a.gate != EliminationGate.COLLAPSED]:
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
    """The states in which the numbers above are NOT what they look like — MEASURED here, rather
    than warned about in advance. Silent when none is live, which is the point: a caveat that
    renders every round is read as boilerplate by the third one."""
    d, cs = b.digest, b.cycle_slice
    rows: list[str] = []
    if b.ruler is None:
        rows.append(
            "COLD RULER — θ is logit-accuracy on each arm's OWN subset, not a shared scale. Two θ "
            "here are comparable to each other and to nothing else."
        )
    elif (
        span := b.ruler.band_span(s for s in d.latest_sample_ids if isinstance(s, int))
    ) is not None:
        round_span, ruler_span = span
        # The verdict is the SERVED one (`domain/ruler.py::theta_caveat`), never a second reading
        # of the same spans: the operator's screen and this panel must not be able to disagree
        # about whether a number means anything. Naming WHICH arm fired is the value — the
        # instrument and the acquisition need different fixes and the round looks identical.
        caveat = theta_caveat(
            calibration_model=b.ruler.calibration_model,
            round_span=round_span,
            ruler_span=ruler_span,
        )
        if caveat in (ThetaCaveat.FLAT_RULER, ThetaCaveat.COLLAPSED_BAND):
            cause = (
                "the ruler itself spans almost nothing, so no draw could have been wider"
                if caveat is ThetaCaveat.FLAT_RULER
                else "the draw took a thin slice of a wider ruler"
            )
            rows.append(
                f"COLLAPSED BAND — this round's {unit_plural(b.measured_unit)} span "
                f"{round_span:.2f} logits on a ruler spanning {ruler_span:.2f}; {cause}. Inside a "
                f"band that narrow every {b.measured_unit} is equally hard, "
                "so θ is logit-accuracy plus a constant and ranking on it ranks on accuracy."
            )
    if d.prev_sample_ids and not (d.latest_sample_ids & d.prev_sample_ids):
        rows.append(
            f"SUBSET MOVED WHOLE — this round shares no {b.measured_unit} with the one before it, "
            "so the two rounds' raw rates are two different exams."
        )
    if not rows:
        return []
    live = sorted(name for name, sev, _ in cs.couplings if sev == "collision")
    tail = f"\n  config couplings live: {', '.join(live)}" if live else ""
    return [
        Item(
            "LIVE CAVEATS — states where the numbers above are not what they look like:\n"
            + "\n".join(f"  {r}" for r in rows)
            + tail
        )
    ]


@signal("budget_state", kind=InjectionKind.DERIVED, char_cap=None, citable=False)
def _r_budget_state(b: InjectionBundle) -> list[Item]:
    """How much run is LEFT. Not citable: budget says how boldly to spend a round, never that a
    mutation is the right one — a citation pointing here would ground an edit in the clock."""
    cs = b.cycle_slice
    parts: list[str] = []
    if cs.max_rounds:
        parts.append(f"round {cs.round_num} of {cs.max_rounds}")
    if cs.spend_budget_usd:
        used = f"${cs.spend_used_usd:.2f}" if cs.spend_used_usd is not None else "?"
        parts.append(f"{used} of ${cs.spend_budget_usd:.2f} spent")
    if not parts:
        return []
    return [Item("BUDGET: " + " · ".join(parts))]
