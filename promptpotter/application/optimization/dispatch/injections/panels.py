from __future__ import annotations

import re
from collections import Counter
from typing import Any, cast

from promptpotter.application.intelligence.exploration import ruler_entry
from promptpotter.application.optimization.dispatch.bundle import (
    INNER_NARRATIVE_CAP,
    INNER_NARRATIVE_FULL_CELLS,
    INNER_NARRATIVE_SUMMARY_CAP,
    MEMORY_FIELD_CAP,
    MEMORY_ROUND_CAP,
    MEMORY_VALUE_CAP,
    MISS_GT_CAP,
    MISS_PREDICTED_CAP,
    MISS_QUERY_CAP,
    MISS_RENDER_CAP,
    NEAR_MISS_RENDER_CAP,
    NODE_FAILURE_RENDER_CAP,
    SAMPLE_RENDER_CAP,
    TRANSCRIPT_PREDICTED_CAP,
    TRANSCRIPT_QUERY_CAP,
    TRANSCRIPT_REASONING_CAP,
    TRANSCRIPT_RENDER_CAP,
    InjectionBundle,
    InjectionKind,
    fence_untrusted,
    signal,
)
from promptpotter.application.scoring.evaluators import compute_accuracy
from promptpotter.domain.escalation_signals import ExplorationBudget
from promptpotter.domain.opt_search_point import (
    IDEA_MATCH_MARK,
    candidate_delta,
    candidate_idea,
    flatten_sp_summary,
    same_idea,
)
from promptpotter.domain.results import CritiqueReadout, ScoredCandidate
from promptpotter.domain.results_health import evidence_starved_node
from promptpotter.domain.scoring import QueryMeasurement, enumerable_truth_labels, is_hit
from promptpotter.shared.errors import is_error_result


@signal(
    "escalation_panel",
    kind=InjectionKind.DERIVED,
    char_cap=400,
    citable=True,
)
def _r_escalation_panel(b: InjectionBundle) -> str:
    cs = b.cycle_slice
    budget = cs.exploration_budget
    guidance = {
        ExplorationBudget.TIGHT: "exploit the parent — stall_exploration citations are rejected",
        ExplorationBudget.NORMAL: "stalling — stall_exploration citations are permitted",
        ExplorationBudget.WIDE: "explore freely — a PEAKED axis may be mutated with an "
        "exploration_budget=wide rebut",
    }[ExplorationBudget(budget)]
    return f"ESCALATION: exploration_budget={budget} (L1 stall: {cs.l1_stall_count} rounds) — {guidance}"


@signal(
    "evidence_health",
    kind=InjectionKind.DERIVED,
    char_cap=500,
    citable=True,
)
def _r_evidence_health(b: InjectionBundle) -> str:
    """A node failing on ≥ ``EVIDENCE_STARVED_RATE`` of samples is an upstream fault no param
    mutation can fix; the degradation grade reads the SAME helper. Empty on a healthy round."""
    rates = b.digest.node_failure_rates
    if not rates:
        return ""
    ranked = sorted(rates.items(), key=lambda kv: kv[1], reverse=True)
    lines = [
        f"  {node}: failed on {rate:.0%} of samples"
        for node, rate in ranked[:NODE_FAILURE_RENDER_CAP]
    ]
    body = "PIPELINE NODE FAILURES (round-level):\n" + "\n".join(lines)
    # The typed predicate, not a second comparison against the same constant — the health
    # grade, the L2 router and `terminate_capability` all ask this one function, so the panel
    # cannot say STARVED on a round the router calls healthy, or stay silent on one it doesn't.
    if starved := evidence_starved_node(rates):
        worst_rate = rates[starved]
        body += (
            f"\nEVIDENCE STARVED — '{starved}' failed on {worst_rate:.0%} of samples this "
            "round. That is an upstream/backend fault (e.g. quota / rate-limit exhausted), NOT a "
            "prompt problem L1 can fix. Do not propose a param change to chase it: name the dead "
            "node in priority_fix and flag that this round's measurement is unreliable."
        )
    return body


@signal(
    "diagnostics",
    kind=InjectionKind.DERIVED,
    char_cap=2000,
    citable=True,
)
def _r_diagnostics(b: InjectionBundle) -> str:
    """Section order is actionability-first BECAUSE the façade truncates section-aware at
    ``char_cap`` — least-actionable last is the first dropped, and no section is ever half-rendered."""
    sections: list[str] = []
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
    sections.append("\n".join(status))

    d = b.digest.diagnostics
    if d is None:
        return "\n\n".join(sections)
    parts: list[str] = []

    if d.anomalies:
        parts.append("ANOMALIES:\n  " + "\n  ".join(d.anomalies))

    miss_samples = [s for s in d.samples if not is_hit(s.fitness)][:SAMPLE_RENDER_CAP]
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
                f"  MISS [{s.terminated_at}] {s.query[:70]} → {s.predicted[:60]}"
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

    if d.n_valid:
        rb = d.rank_buckets
        # Renders only where ranking DISCRIMINATED — some ground truth landed below r=1. With
        # every rank at 1-or-absent the buckets are the hit/miss split wearing ranking
        # vocabulary, which L2 already has. That is the common case, not the edge: `llm_only`
        # maps its one output onto `final_ranking`, so the schema-level "has a ranker?" test
        # reads True on a width-1 list and the 0s only steer L2 toward "fix the ranker".
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
            parts.append(rank_line)

    # PIPELINE HEALTH: skip when single-terminator + 0% errors + 0% warnings (default success).
    if d.termination_dist and (
        d.error_rate > 0 or d.warning_rate > 0 or len(d.termination_dist) > 1
    ):
        td_lines = ["PIPELINE HEALTH:"]
        for step, count in sorted(d.termination_dist.items(), key=lambda x: -x[1]):
            td_lines.append(f"  terminate@{step}: {count}")
        if d.error_rate > 0 or d.warning_rate > 0:
            td_lines.append(
                f"  error_rate: {d.error_rate:.0%} | warning_rate: {d.warning_rate:.0%}"
            )
        parts.append("\n".join(td_lines))

    if d.l1_diversity != 1.0:
        parts.append(f"POPULATION: diversity={d.l1_diversity:.2f}")

    # TRAJECTORY + EVOLUTION last (least actionable: historical narrative, first to be tail-cut).
    # Skipped at R1 — "too few rounds to classify" is dead weight.
    if len(d.evolution_rows) > 1:
        line = f"TRAJECTORY: {d.trajectory}"
        if d.trajectory_description:
            line += f" — {d.trajectory_description}"
        parts.append(line)
        tbl = ["EVOLUTION (last rounds):", "  round  acc      Δ       degraded"]
        for row in d.evolution_rows[-5:]:
            tbl.append(
                f"  {row.round:>5}  {row.accuracy:>6.1%}  {row.delta:>+6.1%}  {row.degraded:>5}"
            )
        parts.append("\n".join(tbl))

    if parts:
        sections.append(fence_untrusted("\n\n".join(parts)))
    return "\n\n".join(sections)


# Order keys surface to the LLM — effect-driven items first (rankings, top values, exhausted) so
# attention lands on what to mutate; sample-side findings second; narrative tail last.
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
    char_cap=1200,  # digest() already caps to top-5 axes; this is the hard backstop.
    citable=True,
)
def _r_axis_memory(b: InjectionBundle) -> str:
    """Critique-aware: when L1_CRITIQUE flagged only semantic failures, param rankings are noise it
    already vetoed. Sample-side rows stay — they suggest no axis mutation."""
    if b.axes is None:
        return ""
    digest = b.axes.digest()
    if not digest:
        return ""
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
    return "\n".join(lines) if len(lines) > 1 else ""


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
    # Sized for TRANSCRIPT_RENDER_CAP=3 typical transcripts (~2.6-3k each on
    # justlogic); worst case (~3.8k each) degrades by section-drop of the whole
    # 3rd transcript — today's behavior, never a severed fence.
    char_cap=10000,
    citable=True,
)
def _r_sample_transcripts(b: InjectionBundle) -> str:
    """Silent on the recursion — the mirror of ``inner_narratives``, which is silent off it. A MISS
    selects these rows, and one level up a miss is a scoring artifact of the placeholder label."""
    if _inner_narrated(b):
        return ""
    rows = _misses(b)
    if not rows:
        return ""
    shown = rows[:TRANSCRIPT_RENDER_CAP]
    header = (
        f"SAMPLE TRANSCRIPTS ({len(shown)}/{len(rows)} current misses shown complete — "
        "quote the broken reasoning step, not just the label):"
    )
    sections = [header]
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
        sections.append(fence_untrusted("\n".join(parts)))
    return "\n\n".join(sections)


def _proxy_lift(r: dict[str, Any]) -> float | None:
    """The L4 discriminator: only the ``promptpotter`` connector stamps it. A zero-lift seed — the
    one an edit most needs to see — arrives as int 0, so accept any real number but not bool."""
    d = (r.get("pipeline_data") or {}).get("mean_round_delta")
    return float(d) if isinstance(d, int | float) and not isinstance(d, bool) else None


def _inner_narrated(b: InjectionBundle) -> list[tuple[float, dict[str, Any]]]:
    """The one place the recursion split is spelled: ``inner_narratives`` renders exactly these rows,
    ``sample_transcripts`` renders only when there are none — so neither node is left with no panel."""
    return [
        (lift, r)
        for r in b.trajectory_results
        if (lift := _proxy_lift(r)) is not None
        and (r.get("pipeline_data") or {}).get("reasoning_trace")
    ]


@signal(
    "inner_narratives",
    kind=InjectionKind.MEASUREMENT,
    # Sized for the full outer-seed panel; only the weakest few carry their whole narrative
    # now, so a per-section overrun is a safety rail rather than an expected drop.
    char_cap=13000,
    citable=True,
)
def _r_inner_narratives(b: InjectionBundle) -> str:
    """Each cell's delta is shown BESIDE the origin's own delta on that seed — alone it is mostly
    the seed. Fires on ``mean_round_delta``, not ``reasoning_trace``, which any backend returns."""
    origin_lift = {
        sid: lift
        for r in b.origin_per_sample
        if (sid := r.get("sample_id")) is not None and (lift := _proxy_lift(r)) is not None
    }
    scored = _inner_narrated(b)
    if not scored:
        return ""
    scored.sort(key=lambda t: t[0])
    header = (
        f"INNER RUN NARRATIVES ({len(scored)} inner campaigns this round, weakest first — each is "
        "one outer sample. D is that inner run's lift; `origin` is what the SAME seed scored "
        "under the unedited optimizer prompts, so a low D beside a low origin is a hard seed, not "
        "your edit. Equal values mean the incumbent on that seed still IS the origin. The "
        "weakest carry their full story; ground every candidate in a specific observation):"
    )
    sections = [header]
    for i, (delta, r) in enumerate(scored):
        pd = r.get("pipeline_data") or {}
        label = str(r.get("query") or r.get("sample_id") or "inner")[:80]
        base = origin_lift.get(r.get("sample_id"))
        mark = f"D{delta:+.3f}" + (f" origin{base:+.3f}" if base is not None else "")
        trace = str(pd.get("reasoning_trace") or "")
        body = (
            _head_at_line(trace, INNER_NARRATIVE_CAP)
            if i < INNER_NARRATIVE_FULL_CELLS
            else _head_at_line(trace, INNER_NARRATIVE_SUMMARY_CAP)
        )
        sections.append(fence_untrusted(f"[{label}] {mark}\n{body}"))
    return "\n\n".join(sections)


def _misses(b: InjectionBundle) -> list[dict[str, Any]]:
    """An errored sample is NOT a miss — the measurement never happened (``is_error_result``), so no
    mutation can win it back, and rendered as one it invents a task deficiency to attack."""
    return [
        r for r in b.trajectory_results if not is_hit(r.get("fitness")) and not is_error_result(r)
    ]


def _errored(b: InjectionBundle) -> list[dict[str, Any]]:
    return [r for r in b.trajectory_results if is_error_result(r)]


def _label_counts(rows: list[dict[str, Any]], key: str) -> Counter[str]:
    return Counter(str(v) for r in rows if (v := r.get(key)) not in (None, ""))


def _tally(counts: Counter[str], total: int) -> str:
    return " | ".join(f"{lbl} {n} ({100 * n / total:.0f}%)" for lbl, n in counts.most_common())


@signal(
    "answer_distribution",
    kind=InjectionKind.MEASUREMENT,
    char_cap=700,
    citable=True,
)
def _r_answer_distribution(b: InjectionBundle) -> str:
    """The collapse detector: accuracy alone cannot tell a pipeline that reasons from one emitting the
    same label every time, and nothing else in the prompt carries that fact. Empty on free text."""
    rows = [r for r in b.trajectory_results if r.get("ground_truth") not in (None, "")]
    # `None` = no repeated label to be constant about (free-text, or identity-keyed answers such
    # as an L4 outer round's per-seed tokens). Same rule the scorer's collapse gate reads.
    truth = enumerable_truth_labels(rows)
    if truth is None:
        return ""
    said = _label_counts(rows, "predicted")
    n = len(rows)

    top_label, top_n = truth.most_common(1)[0]
    constant = top_n / n
    # Mean fitness — the SAME quantity `accuracy` reports, and the only one comparable
    # to the constant-answer floor beside it. Counting `fitness >= 1.0` instead made
    # this line read "You score 0.00" on every graded scorer, so the panel told the
    # generator a constant answer beat it while the run was in fact climbing.
    scored = compute_accuracy(results=cast("list[QueryMeasurement]", rows))
    if scored is None:
        return ""  # nothing scoreable — no score to compare the floor against

    lines = [
        f"ANSWER DISTRIBUTION (over the {n} samples scored so far):",
        f"  you answer : {_tally(said, n) if said else '(nothing parsed)'}",
        f"  the truth  : {_tally(truth, n)}",
        f'  Answering "{top_label}" to EVERY sample would score {constant:.2f}. '
        f"You score {scored:.2f}.",
    ]
    return fence_untrusted("\n".join(lines))


def _miss_difficulty(b: InjectionBundle, row: dict[str, Any]) -> float | None:
    """This miss's δ on the cycle's locked ruler — ``None`` while the ruler is cold or the
    sample is off it. A 2PL entry carries ``(δ, a)``; only δ is a difficulty."""
    ruler = b.delta_scale
    sid = row.get("sample_id")
    if not ruler or sid is None:
        return None
    entry = ruler.get(int(sid))
    if entry is None:
        return None
    return ruler_entry(entry)[0]


@signal(
    "failing_samples",
    kind=InjectionKind.MEASUREMENT,
    char_cap=2400,
    citable=True,
)
def _r_failing_samples(b: InjectionBundle) -> str:
    """Ordered easiest-first — the one thing here L1 cannot compute for itself. A cold ruler renders
    the misses unordered rather than quoting a difficulty that would move next round."""
    rows = _misses(b)
    errored = _errored(b)
    if not rows:
        # Silence here would leave the generator with no account of the round at all, so the
        # panel reports the non-measurement in its own voice (unfenced — it is a directive
        # about PromptPotter's state, not dataset content the fence tells L1 to distrust).
        if not errored:
            return ""
        return (
            f"NOT MEASURED — {len(errored)}/{len(b.trajectory_results)} samples errored before "
            "producing an answer. The measurement never happened: there is no miss here to win "
            "back and no prompt edit that reaches it. Do not propose a mutation to chase this "
            "round — treat its score as ABSENT, not as a failure."
        )
    scored = [(_miss_difficulty(b, r), r) for r in rows]
    graded = [(d, r) for d, r in scored if d is not None]
    ungraded = [r for d, r in scored if d is None]
    graded.sort(key=lambda dr: dr[0])
    ordered: list[tuple[float | None, dict[str, Any]]] = [
        *graded,
        *((None, r) for r in ungraded),
    ]
    shown = ordered[:MISS_RENDER_CAP]

    ruled = "difficulty δ from the cycle's fixed ruler; easiest first — the top rows are the "
    cold = "the difficulty ruler is still cold, so these are unordered"
    header = (
        f"FAILING SAMPLES ({len(shown)}/{len(rows)} current misses — "
        + (f"{ruled}winnable ones" if graded else cold)
        + "):"
    )
    lines = [header]
    for delta, r in shown:
        d_str = f"δ={delta:+.2f}" if delta is not None else "δ=?"
        lines.append(
            f"  [#{r.get('sample_id')}] {d_str} | {_query_stem(r, MISS_QUERY_CAP)}"
            f" | said: {str(r.get('predicted') or '')[:MISS_PREDICTED_CAP]}"
            f" | true: {str(r.get('ground_truth') or '')[:MISS_GT_CAP]}"
        )
    if len(ordered) > len(shown):
        lines.append(f"  (+{len(ordered) - len(shown)} harder misses not shown)")
    body = fence_untrusted("\n".join(lines))
    if errored:
        body += (
            f"\n({len(errored)} further samples errored before answering — not misses, and not "
            "reachable by a prompt edit; the measurement never happened there.)"
        )
    return body


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


def _candidate_fate(cand: ScoredCandidate) -> str:
    """Not keyed on ``partial_reason``: its ``pobb`` arm is dead, so every eliminated candidate on
    disk carries the empty string."""
    if cand.invalid:
        return "invalid — rejected before it cost a sample"
    if cand.total == 0:
        # Zero samples means zero evidence, and `accuracy` defaults to 0.0 — so an unmeasured
        # candidate is byte-identical to one that got everything wrong, and must never be
        # quoted as an outcome. Same rule `matched_origin_accuracy` states one level up.
        return "never measured — no samples scored, its 0% is absence of evidence"
    if cand.elimination_stopped:
        cut = f"cut at {cand.scored_samples}/{cand.expected_samples} samples"
        return f"{cut} — P(best) fell below ε; measurement stopped, NOT a verdict"
    if cand.escalation_aborted:
        return "aborted mid-run"
    return "scored in full"


@signal(
    "mutation_memory",
    kind=InjectionKind.DERIVED,
    char_cap=1800,
    citable=True,
)
def _r_mutation_memory(b: InjectionBundle) -> str:
    """ONE compact line per prior candidate, so every retained round fits and the record stays
    COMPLETE — recognition, not reproduction. A candidate that changed nothing is not an attempt."""
    prior = list(b.prior_rounds)
    # Attempts per round, oldest first. Built for EVERY prior round before the retained
    # window is taken, because a round's row count is not knowable from the round: C0 and
    # a no-op variant both carry a candidate that changed nothing. Windowing on rounds
    # that merely HAVE candidates spends a retained slot rendering nothing.
    by_round: list[tuple[int, list[tuple[str, frozenset[str]]]]] = []
    for i, rr in enumerate(prior):
        parent = rr.prompt_fields
        # The candidates' parent params = the PRIOR round's resolved pipeline_params
        # (the winner / retained incumbent this round mutated from).
        parent_pp = prior[i - 1].pipeline_params if i > 0 else None
        attempts: list[tuple[str, frozenset[str]]] = []
        for cand in rr.candidate_scores:
            changed = _candidate_mutation(cand, parent, parent_pp)
            if not changed:
                continue
            mutation = [f'{field}: "{value[:MEMORY_VALUE_CAP]}"' for field, value in changed]
            # `total == 0` is checked BEFORE the paired quote, not inside `_candidate_fate`'s
            # fallback: a never-measured candidate can still carry a `matched_origin_accuracy`
            # (the origin was scored even though the candidate was not), which would otherwise
            # take the branch above and render a fully-formed comparison out of nothing.
            scored = (
                f"{cand.accuracy:.0%} vs origin {cand.matched_origin_accuracy:.0%}"
                if cand.total and cand.matched_origin_accuracy is not None
                else _candidate_fate(cand)
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
        return ""
    lines: list[str] = []
    # (round, fingerprint) per rendered row, oldest first — the pool each later row is
    # matched against. First match wins, so a marker always points at the EARLIEST occurrence
    # and a long repeat chain keeps naming one round rather than the previous link. Matched
    # only within the window, so a marker never names a round the panel does not show.
    seen: list[tuple[int, frozenset[str]]] = []
    for round_num, attempts in by_round[-MEMORY_ROUND_CAP:]:
        for body, fp in attempts:
            echoes = [r for r, prev in seen if same_idea(fp, prev, threshold=IDEA_MATCH_MARK)]
            mark = f"  ↺ same idea as r{echoes[0]} (x{len(echoes) + 1})" if echoes else ""
            seen.append((round_num, fp))
            lines.append(f"  r{round_num} {body}{mark}")
    header = (
        "ALREADY TRIED (this cycle — a mutation measured and lost here does not improve by "
        "being proposed again; ↺ marks an idea already tried in an earlier round, in whatever "
        "field it was written into):"
    )
    return fence_untrusted("\n".join([header, *lines]))


@signal(
    "origin_strengths",
    kind=InjectionKind.MEASUREMENT,
    char_cap=None,
    citable=True,
)
def _r_origin_strengths(b: InjectionBundle) -> str:
    """Reports the origin's MEAN fitness, never a count of maxed-out samples: the count silenced the
    panel on every graded scorer, which is the one thing a don't-strip-this warning must never do."""
    rows = b.origin_per_sample
    if not rows:
        return ""
    acc = compute_accuracy(results=cast("list[QueryMeasurement]", rows))
    if acc is None:
        return ""
    return (
        f"ORIGIN STRENGTHS: origin scores {acc:.0%} across {len(rows)} samples "
        "— preserve the parent scaffolding earning that."
    )


@signal(
    "archive_top_runs",
    kind=InjectionKind.MEASUREMENT,
    char_cap=None,
    citable=True,
)
def _r_archive_top_runs(b: InjectionBundle) -> str:
    if b.axes is None:
        return ""
    runs = b.axes.top_runs(3)
    if not runs:
        return ""
    lines = [f"HISTORICAL BEST (top {len(runs)} runs across the dataset's archive):"]
    for i, r in enumerate(runs, 1):
        label = r.name or r.run_id
        lines.append(
            f"  #{i}  acc={r.accuracy:.1%}  comp={r.composite:.3f}  n={r.total}  run={label}"
        )
    return "\n".join(lines)


@signal(
    "rare_hit_samples",
    kind=InjectionKind.MEASUREMENT,
    char_cap=None,
    citable=True,
)
def _r_rare_hit_samples(b: InjectionBundle) -> str:
    if b.axes is None:
        return ""
    rare = b.axes.sample_index.rare_hit_samples(max_hits=3, min_observations=10)
    if not rare:
        return ""
    lines = ["RARE-HIT SAMPLES (cracked by ≤3 of ≥10 attempts — replicate the unlock pattern):"]
    for sid, query, hits, total, hit_run_ids in rare[:6]:
        if hits == 0:
            lines.append(f"  [#{sid}] {query}… → 0/{total} (capacity-bound; do not engineer for)")
        else:
            run_str = ", ".join(rid[:24] for rid in hit_run_ids[:2])
            lines.append(f"  [#{sid}] {query}… → {hits}/{total} hit by {run_str}")
    return fence_untrusted("\n".join(lines))
