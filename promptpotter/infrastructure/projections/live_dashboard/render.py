"""Pure projections from scalar state + ``RoundBuffer`` to the ``dashboard.json`` shape — side-effect free, returning
plain dicts."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from promptpotter.domain.results import candidate_label
from promptpotter.domain.scoring import is_hit
from promptpotter.shared.composite import inline_short_formula_values

if TYPE_CHECKING:
    from promptpotter.domain.results import RoundResult
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
    badge = _NODE_BADGES.get(s.get("terminated_at") or "", (s.get("terminated_at") or "?")[:2])
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


def build_l1_score_block(
    buffer: RoundBuffer,
    active_formula: str | None,
    short_formula_template: str | None,
    round_result: RoundResult | None = None,
) -> dict[str, Any]:
    """Project current candidates to the dashboard's l1_score shape. ``label`` is canonical — display sites read it verbatim,
    and no ``idx + 1`` arithmetic exists anywhere."""
    live = round_result is None
    candidates = buffer.candidates
    round_num = buffer.round_num
    input_candidates: list[dict[str, Any]] = []
    output_candidates: list[dict[str, Any]] = []
    for idx in sorted(candidates.keys()):
        cand = candidates[idx]
        scores = cand.get("scores") or {}
        label = candidate_label(round_num, idx)
        input_candidates.append(
            {
                "idx": idx,
                "label": label,
                "changes_description": (
                    cand.get("changes_description") or scores.get("changes_description") or ""
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
        # Mid-scoring the final ``scores`` are empty; fall back to the scorer's
        # running fitness (same shape, ridden out per sample on ``_running``) so
        # composite / accuracy / hits / total are LIVE and converge to the final.
        # ``scores`` wins the moment the candidate completes.
        served = scores or cand.get("running") or {}
        cand_evaluators = dict(served.get("evaluators") or {})
        samples = cand.get("samples") or []
        served_accuracy = served.get("accuracy")
        stats: dict[str, Any] = {
            # Never null mid-scoring: the running fitness carries accuracy; the
            # partial mean over samples-so-far is the safety net before it lands.
            "accuracy": served_accuracy
            if served_accuracy is not None
            else _partial_mean_fitness(samples),
            "composite_fitness": served.get("composite_fitness"),
            "composite_fitness_formula": active_formula,
            # Per-candidate value-inlined short formula. The legend for short
            # codes (``acc``, ``H``, ``lat``, ``R``, ``pc``) lives in
            # ``docs/operations/improvement-tracking.md``.
            "composite_fitness_formula_short": inline_short_formula_values(
                short_formula_template, cand_evaluators
            ),
            "hits": served.get("hits"),
            "total": served.get("total"),
            "invalid": served.get("invalid", False),
            "validation_failures": served.get("validation_failures") or [],
        }
        output_candidates.append(
            {
                "idx": idx,
                "label": label,
                "stats": stats,
                "samples": [fmt_sample_line(s) for s in samples] if live else list(samples),
            }
        )
    return {
        "input": {"candidates": input_candidates},
        "output": {"candidates": output_candidates},
    }


def build_pobb_block(core: LiveStateCore, p_best_top: list[dict[str, Any]]) -> dict[str, Any]:
    """Round-wide PoBB telemetry. ``leader_prob`` is the best standing among CANDIDATES — never a max over one snapshot's
    dict, whose other entries are that same candidate's odds against each prior."""
    if not core.current_p_best_id:
        return {
            "current_id": "",
            "n_samples": 0,
            "leader_prob": 0.0,
            "posterior_width": 1.0,
            "top": [],
        }
    leader_prob = max(
        [float(row["p_best"]) for row in p_best_top] or list(core.round_p_best.values()) or [0.0]
    )
    return {
        "current_id": core.current_p_best_id,
        "n_samples": core.current_p_best_n,
        "leader_prob": float(leader_prob),
        "posterior_width": float(1.0 - leader_prob),
        "top": list(p_best_top),
    }


__all__ = ["build_l1_score_block", "build_pobb_block", "fmt_sample_line"]
