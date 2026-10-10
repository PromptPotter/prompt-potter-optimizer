from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from promptpotter.application.scoring.search_point_scorer import SCORING_ERROR_ABORT, ScoredWalk
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.phases import WalkEnd
from promptpotter.domain.results import (
    ArmOutcome,
    CandidateProposal,
    DegradationContext,
    ScoredCandidate,
    ScoreSummary,
    is_floor_pinned,
    measured_cells,
)
from promptpotter.domain.ruler import DeltaRuler, ThetaCaveat
from promptpotter.domain.spend import TokenAccount
from promptpotter.domain.validators import BrokenSignal, StopSignal
from promptpotter.domain.wounds import (
    INVARIANT_REASONS,
    NurseOwner,
    RuntimeFailure,
    ValidationFailure,
)
from promptpotter.shared.errors import ErrorCategory
from promptpotter.shared.hashing import stable_hash

if TYPE_CHECKING:
    from promptpotter.domain.scoring import GradedCell

__all__ = [
    "Breakage",
    "arm_id",
    "arm_theta_caveat",
    "build_score_report",
    "fatal_validation_failures",
    "is_transient_scoring_abort",
    "read_breakage",
    "walk_outcome",
]

_CONFIG_DETERMINISTIC_ABORT = frozenset({ErrorCategory.CLIENT.value, ErrorCategory.PIPELINE.value})


def walk_outcome(scored: ScoredWalk) -> ArmOutcome:
    if scored.signal is not None:
        return scored.signal.outcome
    return ArmOutcome.SKIPPED if scored.stopped is WalkEnd.SKIP else ArmOutcome.MEASURED


def is_transient_scoring_abort(signal: StopSignal | None) -> bool:
    if not isinstance(signal, BrokenSignal) or signal.check_name != SCORING_ERROR_ABORT:
        return False
    return not _abort_is_config_break(signal.check_result)


def _abort_is_config_break(cr: DegradationContext) -> bool:
    """An empty histogram is transient: never halt on ambiguity."""
    wt = cr["warning_types"]
    if not wt:
        return False
    dominant_cat = max(wt.items(), key=lambda kv: kv[1])[0]
    return str(dominant_cat) in _CONFIG_DETERMINISTIC_ABORT


@dataclass(frozen=True)
class Breakage:
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
    # An abort names an error, not a node, so it shows the whole config.
    node_cfg = params if aborted else params.get(cr["dominant_warning"].split(":", 1)[0], {})
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


def arm_theta_caveat(rows: Sequence[GradedCell], ruler: DeltaRuler | None) -> ThetaCaveat | None:
    """Order is severity: a floor-pinned arm has no θ on any scale, so it outranks an unlinked cell."""
    if is_floor_pinned(rows):
        return ThetaCaveat.FLOOR_PINNED
    if ruler is not None and ruler.unlinked(measured_cells(rows)):
        return ThetaCaveat.UNMEASURED_DELTA
    return None


def build_score_report(
    opt_sp: OptSearchPoint,
    validation_failures: Sequence[ValidationFailure],
    pipeline_overlay: dict[str, Any] | None,
    score_summary: ScoreSummary,
    query_results: Sequence[GradedCell],
    dataset: list[Any],
    *,
    label: str,
    sp_hash: str,
    outcome: ArmOutcome,
    resolved_pipeline_params: dict[str, Any] | None = None,
    elimination_context: dict[str, Any] | None = None,
    elimination_reason: str | None = None,
    breakage: Breakage | None = None,
) -> ScoredCandidate:
    return ScoredCandidate(
        **score_summary.model_dump(),
        # Read HERE, not at the election: round 0 holds no election fit.
        theta_caveat=arm_theta_caveat(query_results, None),
        candidate_id=opt_sp.id,
        label=label,
        changes_description=opt_sp.lineage.changes_description or "",
        pipeline_overlay=pipeline_overlay,
        resolved_pipeline_params=resolved_pipeline_params,
        sp_hash=sp_hash,
        prompt_fields=opt_sp.prompt_field_dict(),
        outcome=outcome,
        scored_samples=len(query_results),
        expected_samples=len(dataset),
        cached_samples=sum(1 for cell in query_results if cell.facts.cached),
        input_tokens=measured.input
        if (measured := TokenAccount.from_measured_rows(cell.facts for cell in query_results))
        else None,
        output_tokens=measured.output if measured else None,
        cache_read_tokens=measured.cache_read if measured else None,
        validation_failures=list(validation_failures),
        runtime_failures=[breakage.runtime_failure] if breakage else [],
        elimination_context=elimination_context or {},
        elimination_reason=elimination_reason,
        degradation_context=breakage.context if breakage else {},
    )


_NON_FATAL_REASONS = frozenset({"hallucinated_node"})
assert not INVARIANT_REASONS & _NON_FATAL_REASONS


def fatal_validation_failures(failures: Sequence[ValidationFailure]) -> list[ValidationFailure]:
    return [vf for vf in failures if vf.reason not in _NON_FATAL_REASONS]


def arm_id(proposal: CandidateProposal, round_num: int, idx: int) -> str:
    """A rejected proposal is named by its SLOT: its content is often its parent's or a sibling's own."""
    if fatal_validation_failures(proposal.validation_failures):
        return stable_hash([round_num, idx, proposal.opt_sp.id])
    return proposal.opt_sp.id
