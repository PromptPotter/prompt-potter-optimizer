"""Pure projections from scalar state + ``RoundBuffer`` to the ``dashboard.json`` shape — side-effect free, returning
plain dicts."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from promptpotter.domain.dashboard_rows import DashboardCandidate, DashboardSample, SampleStatus
from promptpotter.domain.results import candidate_label
from promptpotter.domain.scoring import is_hit, is_verifier_graded
from promptpotter.infrastructure.projections.live_dashboard.state import PobbBlock
from promptpotter.shared.composite import inline_short_formula_values

if TYPE_CHECKING:
    from promptpotter.infrastructure.projections.live_dashboard.round_buffer import RoundBuffer
    from promptpotter.infrastructure.projections.live_state import LiveStateCore

# Per-sample terminator badge for the compact in-flight rendering;
# unmapped nodes render as the first two characters of the node name.
_NODE_BADGES: dict[str, str] = {
    "llm_only": "ai",
    "llm_ranking": "ai",
    "entity_profiling": "ai",
    "cache_lookup": "cache",
    "fuzzy_matching": "fz",
    "token_matching": "tk",
    "web_search": "ws",
}


def _trim(text: str, n: int) -> str:
    t = str(text or "").replace("\n", " ").strip()
    return t if len(t) <= n else t[: n - 1] + "…"


def sample_row(s: dict[str, Any]) -> DashboardSample:
    """One buffered sample as the dashboard serves it — the ONE place the tape's facts are
    decided, and where the display trim happens."""
    sid = s.get("sample_id")
    time_s = s.get("time_s")
    # `ERR` is asked BEFORE the grade: an errored row was never graded, so putting its absent
    # fitness through `is_hit` reports a backend fault as a candidate answering wrong.
    status: SampleStatus = (
        "ERR" if s.get("error") else ("HIT" if is_hit(s.get("fitness")) else "MISS")
    )
    # A verifier-graded row has no label, so the answer/truth pair is both halves of a comparison
    # nobody made — and `prediction` there is the `NO_RESULT` sentinel a ranking mechanism that is
    # not in play left behind. Served EMPTY rather than sentinel-and-blank, so a client can still
    # tell the two apart: `NO_RESULT` beside a real truth is an extraction that broke.
    ground_truth = _trim(s.get("ground_truth") or "", 20)
    graded_by_verifier = is_verifier_graded(ground_truth)
    fitness = s.get("fitness")
    return DashboardSample(
        qi=int(s.get("qi", 0)),
        sample_id=None if sid is None else int(sid),
        status=status,
        # Off the same row `status` was decided from — an errored row carries none, which is
        # what `status == "ERR"` already says.
        fitness=float(fitness) if isinstance(fitness, int | float) else None,
        terminal_node=str(s.get("terminal_node") or ""),
        cached=bool(s.get("cached", False)),
        time_s=float(time_s) if isinstance(time_s, int | float) else None,
        predicted="" if graded_by_verifier else _trim(s.get("prediction") or "", 28),
        ground_truth=ground_truth,
        query=_trim(s.get("query") or "", 42),
        input_tokens=s.get("input_tokens"),
        output_tokens=s.get("output_tokens"),
    )


def fmt_sample_line(row: DashboardSample) -> str:
    """One compact line per query, keeping `dashboard.json` scannable for the folder-UI reader.

    A RENDERING of the row beside it, never a second source. It was the only form the live block
    carried, so the browser regexed it back into the row — which made this column layout a wire
    contract that no formatting change could touch."""
    sid_seg = f" sid:{row.sample_id:03d}" if row.sample_id is not None else ""
    badge = _NODE_BADGES.get(row.terminal_node, row.terminal_node[:2] or "?")
    cache_icon = "📖" if row.cached else " "
    in_tok, out_tok = row.input_tokens, row.output_tokens
    tok_seg = ""
    if in_tok is not None or out_tok is not None:
        tok_seg = (
            f" io={in_tok if in_tok is not None else '-'}/{out_tok if out_tok is not None else '-'}"
        )
    # Blank rather than `0.0s` where the row recorded no time — a cached replay's real 0.0
    # must stay distinguishable from a row that never reached the pipeline.
    time_col = f"{row.time_s:4.1f}s" if row.time_s is not None else "     "
    head = f"  {time_col} #{row.qi:03d}{sid_seg} {row.status:<4} [{badge}]{cache_icon}{tok_seg}"
    # Nothing to contrast on a verifier-graded row — the status IS the verdict there.
    if is_verifier_graded(row.ground_truth):
        return f"{head} q:'{row.query}'"
    return f"{head} -> '{row.predicted}' gt:'{row.ground_truth}' q:'{row.query}'"


def _served(cand: dict[str, Any]) -> dict[str, Any]:
    """The candidate's own numbers, best available. Mid-scoring the final ``scores`` are empty, so the scorer's running
    fitness stands in — the same shape, ridden out per sample on ``_running`` — and ``scores`` wins the moment it lands."""
    return cand.get("scores") or cand.get("running") or {}


def build_candidate_rows(buffer: RoundBuffer) -> list[DashboardCandidate]:
    """This round's candidates in the SAME shape a closed round serves (``rounds[].candidates``), so a reader takes a
    whole row from one half instead of filling one in from the other per field.

    ``scores`` is the ``candidate_scored`` report, folded onto by the election; before it lands, ``running`` is the
    gateway's own per-sample fold. Both carry the composite CI, so the whisker widens with the bar. The ``or`` between
    them is a PRECEDENCE, not two spellings of one thing — only ``scores`` carries ``label``, ``candidate_id``,
    ``invalid`` and ``partial_reason``. ``label`` is canonical — display sites read it verbatim, and no ``idx + 1``
    arithmetic exists."""
    rows: list[DashboardCandidate] = []
    for idx in sorted(buffer.candidates.keys()):
        cand = buffer.candidates[idx]
        served = _served(cand)
        samples = cand.get("samples") or []
        cached = served.get("cached_samples")
        rows.append(
            DashboardCandidate(
                label=candidate_label(buffer.round_num, idx),
                candidate_id=served.get("candidate_id"),
                accuracy=served.get("accuracy"),
                composite_fitness=served.get("composite_fitness"),
                invalid=bool(served.get("invalid", False)),
                scored_samples=int(served.get("scored_samples") or len(samples)),
                cached_samples=int(
                    cached if cached is not None else sum(1 for s in samples if s.get("cached"))
                ),
                expected_samples=cand.get("expected_samples"),
                evaluators=dict(served.get("evaluators") or {}),
                changes_description=(
                    cand.get("changes_description") or served.get("changes_description") or ""
                ),
                partial_reason=served.get("partial_reason") or "",
                mean_fitness_ci_lo=served.get("mean_fitness_ci_lo"),
                mean_fitness_ci_hi=served.get("mean_fitness_ci_hi"),
                # Everything below lands at `l1_score:exit`, folded in by `RoundBuffer.stamp_fit`
                # and `mark_winner` off the one `ElectionRecord` — so the whole verdict is live
                # from the election rather than from the round close, two LLM calls later. Absent
                # before it: the fit needs two arms, and a cold ruler stamps no θ at all.
                theta=served.get("theta"),
                theta_se=served.get("theta_se"),
                theta_caveat=served.get("theta_caveat"),
                matched_parent_accuracy=served.get("matched_parent_accuracy"),
                matched_parent_composite=served.get("matched_parent_composite"),
                matched_parent_lift=served.get("matched_parent_lift"),
                matched_parent_lift_ci_lo=served.get("matched_parent_lift_ci_lo"),
                matched_parent_lift_ci_hi=served.get("matched_parent_lift_ci_hi"),
                is_winner=bool(cand.get("is_winner")),
            )
        )
    return rows


def build_l1_score_block(
    buffer: RoundBuffer,
    short_formula_template: str | None,
) -> dict[str, Any]:
    """The l1_score NODE block — what the node was handed and what it emitted, nothing else.

    The candidate VALUES left this block for ``current_round.candidates`` (:func:`build_candidate_rows`). What stays is
    node I/O the webapp reads (the sample tape, the seed-able input half) plus two facts only the FOLDER-UI reader has —
    the value-inlined formula and the self-healing state — which no closed round has a twin for."""
    input_candidates: list[dict[str, Any]] = []
    output_candidates: list[dict[str, Any]] = []
    for idx in sorted(buffer.candidates.keys()):
        cand = buffer.candidates[idx]
        served = _served(cand)
        label = candidate_label(buffer.round_num, idx)
        input_candidates.append(
            {
                "idx": idx,
                "label": label,
                "changes_description": (
                    cand.get("changes_description") or served.get("changes_description") or ""
                ),
                "pp_override": cand.get("pp_override"),
                # The evolved prompt (OptSearchPoint.prompt_field_dict() shape).
                # Live peer of round_NNNN.json::candidate_scores[].prompt_fields
                # — `liveCandidateSearchPoint` reads it for steer-fork seeding.
                "prompt_fields": cand.get("prompt_fields"),
                # Config-only resolved config the OBSERVE view reads live — the
                # in-flight peer of round_NNNN.json::candidate_scores[].resolved_pipeline_params.
                "resolved_pipeline_params": cand.get("resolved_pipeline_params"),
            }
        )
        rows = [sample_row(s) for s in cand.get("samples") or []]
        output_candidates.append(
            {
                "idx": idx,
                "label": label,
                # Per-candidate value-inlined short formula; the short codes are
                # the evaluator keys ``inline_short_formula_values`` substitutes.
                "composite_fitness_formula_short": inline_short_formula_values(
                    short_formula_template, dict(served.get("evaluators") or {})
                ),
                "invalid": served.get("invalid", False),
                "validation_failures": served.get("validation_failures") or [],
                "samples": [row.model_dump() for row in rows],
                # The tape BESIDE the rows, never instead of them: the browser reads `samples`,
                # the operator reads this in the file, and one producer builds both so they
                # cannot disagree. Two branches of `samples` is what forced the browser to
                # regex a rendering to recover what the other branch already had.
                "sample_lines": [fmt_sample_line(row) for row in rows],
            }
        )
    return {
        "input": {"candidates": input_candidates},
        "output": {"candidates": output_candidates},
    }


def build_pobb_block(core: LiveStateCore, p_best_top: list[dict[str, Any]]) -> PobbBlock:
    """Round-wide PoBB telemetry. ``leader_prob`` is the best standing among CANDIDATES — never a max over one snapshot's
    dict, whose other entries are that same candidate's odds against each prior."""
    if not core.current_p_best_id:
        return PobbBlock()
    leader_prob = max(
        [float(row["p_best"]) for row in p_best_top] or list(core.round_p_best.values()) or [0.0]
    )
    return PobbBlock(
        current_id=core.current_p_best_id,
        n_samples=core.current_p_best_n,
        leader_prob=float(leader_prob),
        posterior_width=float(1.0 - leader_prob),
        top=list(p_best_top),
    )


__all__ = [
    "build_candidate_rows",
    "build_l1_score_block",
    "build_pobb_block",
    "fmt_sample_line",
    "sample_row",
]
