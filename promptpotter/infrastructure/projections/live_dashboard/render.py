"""Pure projections from scalar state + ``RoundBuffer`` to the ``dashboard.json`` shape — side-effect free, returning
plain dicts."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from promptpotter.domain.dashboard_rows import DashboardCandidate
from promptpotter.domain.results import candidate_label
from promptpotter.domain.scoring import is_hit
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


def _partial_mean_fitness(samples: list[dict[str, Any]]) -> float | None:
    """Running mean fitness over samples scored so far, bridging the gap before a candidate's final ``accuracy`` lands. That
    accuracy IS mean fitness, so this converges on it."""
    if not samples:
        return None
    return sum(float(s.get("fitness") or 0.0) for s in samples) / len(samples)


def fmt_sample_line(s: dict[str, Any]) -> str:
    """One compact line per query for the in-flight samples list, keeping the dashboard scannable instead of bloating with
    full query strings. The round-complete flush emits the full dicts."""
    qi = int(s.get("qi", 0))
    sid = s.get("sample_id")
    sid_seg = f" sid:{int(sid):03d}" if sid is not None else ""
    # The `HIT `/`MISS` mark is a PARSED CONTRACT with `webapp/lib/sample-line.ts`, which
    # regexes this tape to drive the live heatmap. It is derived here, never stored.
    hit = is_hit(s.get("fitness"))
    cached = bool(s.get("cached"))
    time_s = float(s.get("time_s") or 0.0)
    badge = _NODE_BADGES.get(s.get("terminal_node") or "", (s.get("terminal_node") or "?")[:2])
    cache_icon = "📖" if cached else " "
    mark = "HIT " if hit else "MISS"
    query = _trim(s.get("query") or "", 42)
    pred = _trim(s.get("prediction") or "", 28)
    gt = _trim(s.get("ground_truth") or "", 20)
    in_tok = s.get("input_tokens")
    out_tok = s.get("output_tokens")
    tok_seg = ""
    if in_tok is not None or out_tok is not None:
        tok_seg = (
            f" io={in_tok if in_tok is not None else '-'}/{out_tok if out_tok is not None else '-'}"
        )
    return (
        f"  {time_s:4.1f}s #{qi:03d}{sid_seg} {mark} [{badge}]{cache_icon}"
        f"{tok_seg} -> '{pred}' gt:'{gt}' q:'{query}'"
    )


def _served(cand: dict[str, Any]) -> dict[str, Any]:
    """The candidate's own numbers, best available. Mid-scoring the final ``scores`` are empty, so the scorer's running
    fitness stands in — the same shape, ridden out per sample on ``_running`` — and ``scores`` wins the moment it lands."""
    return cand.get("scores") or cand.get("running") or {}


def build_candidate_rows(buffer: RoundBuffer) -> list[DashboardCandidate]:
    """This round's candidates in the SAME shape a closed round serves (``rounds[].candidates``), so a reader takes a
    whole row from one half instead of filling one in from the other per field.

    ``scores`` is the verbatim ``ScoredCandidate.model_dump()`` from ``candidate_scored``, and the composite CI is
    stamped there rather than at round close (``l1/population.py``), so a finished candidate carries its whisker
    mid-round. ``label`` is canonical — display sites read it verbatim, and no ``idx + 1`` arithmetic exists."""
    rows: list[DashboardCandidate] = []
    for idx in sorted(buffer.candidates.keys()):
        cand = buffer.candidates[idx]
        served = _served(cand)
        samples = cand.get("samples") or []
        cached = served.get("cached_samples")
        # Never null mid-scoring: the running fitness carries accuracy; the partial mean over
        # samples-so-far is the safety net before it lands.
        accuracy = served.get("accuracy")
        rows.append(
            DashboardCandidate(
                label=candidate_label(buffer.round_num, idx),
                candidate_id=served.get("candidate_id"),
                accuracy=accuracy if accuracy is not None else _partial_mean_fitness(samples),
                composite_fitness=served.get("composite_fitness"),
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
                # Absent until the round's election fit runs — it needs two arms.
                theta=served.get("theta"),
                theta_se=served.get("theta_se"),
                mean_fitness_ci_lo=served.get("mean_fitness_ci_lo"),
                mean_fitness_ci_hi=served.get("mean_fitness_ci_hi"),
                matched_parent_accuracy=served.get("matched_parent_accuracy"),
                matched_parent_composite=served.get("matched_parent_composite"),
                # Set at `l1_score:exit` by `RoundBuffer.mark_winner`, so the crown is live from
                # the election rather than from the round close.
                is_winner=bool(cand.get("is_winner")),
            )
        )
    return rows


def build_l1_score_block(
    buffer: RoundBuffer,
    short_formula_template: str | None,
    *,
    live: bool,
) -> dict[str, Any]:
    """The l1_score NODE block — what the node was handed and what it emitted, nothing else.

    The candidate VALUES left this block for ``current_round.candidates`` (:func:`build_candidate_rows`). What stays is
    node I/O the webapp reads (the sample tape, the seed-able input half) plus two facts only the FOLDER-UI reader has —
    the value-inlined formula and the self-healing state — which no closed round has a twin for. ``live`` picks the tape
    shape: compact parsed lines for the dashboard (``webapp/lib/sample-line.ts`` regexes them), full dicts for the audit
    twin."""
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
        samples = cand.get("samples") or []
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
                "samples": [fmt_sample_line(s) for s in samples] if live else list(samples),
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
]
