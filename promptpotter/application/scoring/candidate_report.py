"""One candidate's measurement, reported — the ``ScoredCandidate`` every arm, parent and origin
takes, and the bench's reading of a walk its own checks stopped."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from promptpotter.application.scoring.metrics import ScoreSummary
from promptpotter.application.scoring.query_loop import WalkEnd
from promptpotter.application.scoring.search_point_scorer import SCORING_ERROR_ABORT, ScoredWalk
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.results import (
    ArmOutcome,
    DegradationContext,
    ScoredCandidate,
    is_floor_pinned,
)
from promptpotter.domain.ruler import ThetaCaveat
from promptpotter.domain.spend import TokenAccount
from promptpotter.domain.validators import BrokenSignal, StopSignal
from promptpotter.domain.wounds import NurseOwner, RuntimeFailure, ValidationFailure
from promptpotter.shared.errors import ErrorCategory

__all__ = [
    "Breakage",
    "build_score_report",
    "fatal_validation_failures",
    "is_transient_scoring_abort",
    "read_breakage",
    "walk_outcome",
]

# An abort the operator must fix: dominated by CLIENT (4xx, bad schema) or PIPELINE (node
# ERROR). A CONNECTION- or SERVER-dominated one is a provider hiccup, not a broken program.
_CONFIG_DETERMINISTIC_ABORT = frozenset({ErrorCategory.CLIENT.value, ErrorCategory.PIPELINE.value})


def walk_outcome(scored: ScoredWalk) -> ArmOutcome:
    """How a walk ended, off the one signal that ended it: a stop rule names its outcome, an
    unsignalled stop is the operator's skip."""
    if scored.signal is not None:
        return scored.signal.outcome
    return ArmOutcome.SKIPPED if scored.stopped is WalkEnd.SKIP else ArmOutcome.MEASURED


def is_transient_scoring_abort(signal: StopSignal | None) -> bool:
    """True when a scoring abort is dominated by transient TRANSPORT rather than a config-deterministic break. The origin
    path reads this to refuse banking a floor a hiccup corrupted."""
    if not isinstance(signal, BrokenSignal) or signal.check_name != SCORING_ERROR_ABORT:
        return False
    return not _abort_is_config_break(signal.check_result)


def _abort_is_config_break(cr: DegradationContext) -> bool:
    """True when a scoring-error abort is operator-fixable. A transport-dominated abort is a blip, not a program fault:
    treating it as terminal escalates a hiccup to the HITL path. Empty histogram ⇒ transient, never halt on ambiguity."""
    wt = cr["warning_types"]
    if not wt:
        return False
    dominant_cat = max(wt.items(), key=lambda kv: kv[1])[0]
    return str(dominant_cat) in _CONFIG_DETERMINISTIC_ABORT


@dataclass(frozen=True)
class Breakage:
    """What a ``BROKEN`` arm is charged: the wound the next proposals read, so the optimizer learns
    from it, and the context the report carries."""

    runtime_failure: RuntimeFailure
    context: DegradationContext


def read_breakage(
    signal: BrokenSignal,
    *,
    effective_pipeline_params: dict[str, Any] | None,
    round_num: int,
    candidate_label: str,
) -> Breakage:
    cr = signal.check_result
    params = effective_pipeline_params or {}
    aborted = signal.check_name == SCORING_ERROR_ABORT
    # An abort names an error, not a node, so it shows the whole config; a degradation names
    # ``{node}:{warning}`` and shows that node's.
    node_cfg = params if aborted else params.get(cr["dominant_warning"].split(":", 1)[0], {})
    # A config-deterministic break (a fatal fast-path, a CLIENT/PIPELINE abort) is the OPERATOR's
    # to fix; a rate-based or transport-dominated one is noise L1 retunes around.
    operator_terminal = cr["fatal"] or (aborted and _abort_is_config_break(cr))
    return Breakage(
        RuntimeFailure(
            source="scoring_error_abort" if aborted else "degradation_check",
            dominant_warning=cr["dominant_warning"],
            warning_types=dict(cr["warning_types"]),
            degraded_rate=cr["degraded_rate"],
            degraded_count=cr["degraded_count"],
            total_scored=cr["total_scored"],
            observed_config=dict(node_cfg),
            first_seen_round=round_num,
            candidate_label=candidate_label,
            owner=NurseOwner.OPERATOR if operator_terminal else NurseOwner.L1,
        ),
        cr,
    )


def build_score_report(
    opt_sp: OptSearchPoint,
    validation_failures: Sequence[ValidationFailure],
    pipeline_overlay: dict[str, Any] | None,
    score_summary: ScoreSummary,
    query_results: list[Any],
    dataset: list[Any],
    *,
    label: str,
    sp_hash: str,
    run_id: str | None,
    outcome: ArmOutcome,
    resolved_pipeline_params: dict[str, Any] | None = None,
    elimination_context: dict[str, Any] | None = None,
    elimination_reason: str | None = None,
    breakage: Breakage | None = None,
) -> ScoredCandidate:
    """Typed candidate score report. The CI is CARRIED from the gateway's own fold
    (`metrics.py::compute_composite_fitness`), never re-derived here — one writer, one band, and
    the same band the live row already showed. ``sp_hash`` is the scored searchpoint's own
    ``sp_hash(session.pipeline_schema)`` — the call ``build_dataset_run_data`` makes to key the
    rows — so the report and the archive name one identity; ``""`` where nothing was measured.
    ``run_id`` is the walk's own (``ScoredWalk.run_id``), ``None`` where nothing was walked."""
    return ScoredCandidate(
        mean_fitness_ci_lo=score_summary["mean_fitness_ci_lo"],
        mean_fitness_ci_hi=score_summary["mean_fitness_ci_hi"],
        # Decided HERE, at the one construction site, rather than at the election: round 0 holds
        # no election fit, and an ORIGIN at 0.0 on every cell is the case that matters most.
        theta_caveat=ThetaCaveat.FLOOR_PINNED if is_floor_pinned(query_results) else None,
        candidate_id=opt_sp.lineage.id,
        label=label,
        changes_description=opt_sp.lineage.changes_description or "",
        pipeline_overlay=pipeline_overlay,
        resolved_pipeline_params=resolved_pipeline_params,
        sp_hash=sp_hash,
        run_id=run_id,
        prompt_fields=opt_sp.prompt_field_dict(),
        accuracy=score_summary["accuracy"],
        composite_fitness=score_summary["composite_fitness"],
        total=score_summary["total"],
        evaluators=dict(score_summary["evaluators"]),
        outcome=outcome,
        scored_samples=len(query_results),
        expected_samples=len(dataset),
        cached_samples=sum(1 for r in query_results if r.get("cached")),
        # Folded HERE, beside the replay count it is the peer of, so the two readings of "what did
        # this searchpoint cost to measure" come off one walk of one list.
        input_tokens=measured.input
        if (measured := TokenAccount.from_measured_rows(query_results))
        else None,
        output_tokens=measured.output if measured else None,
        cache_read_tokens=measured.cache_read if measured else None,
        validation_failures=list(validation_failures),
        runtime_failures=[breakage.runtime_failure] if breakage else [],
        elimination_context=elimination_context or {},
        elimination_reason=elimination_reason,
        degradation_context=breakage.context if breakage else {},
    )


def fatal_validation_failures(failures: Sequence[ValidationFailure]) -> list[ValidationFailure]:
    """The failures that cost a candidate its measurement, as opposed to riding along as signal.

    ``hallucinated_node`` is the one non-fatal reason — the phantom edit is stripped and the real
    edits still ran. One definition, because the scorer and the yield count must agree on which
    candidates measured."""
    return [vf for vf in failures if vf.reason != "hallucinated_node"]
