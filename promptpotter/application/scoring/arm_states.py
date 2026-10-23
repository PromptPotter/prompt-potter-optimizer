from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from promptpotter.application.scoring.search_point_scorer import SCORING_ERROR_ABORT, ScoredWalk
from promptpotter.domain.phases import WalkEnd
from promptpotter.domain.results import (
    Broken,
    Concluded,
    Cut,
    DegradationContext,
    Eliminated,
    Elimination,
    Invalid,
    Measured,
    Measuring,
    Proposed,
    Skipped,
)
from promptpotter.domain.validators import BrokenSignal, StopSignal
from promptpotter.domain.wounds import NurseOwner, RuntimeFailure
from promptpotter.shared.errors import ErrorCategory

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.search_point import JobSearchPoint


_CONFIG_DETERMINISTIC_ABORT = frozenset({ErrorCategory.CLIENT.value, ErrorCategory.PIPELINE.value})


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


def read_breakage(
    signal: BrokenSignal,
    *,
    effective_pipeline_params: dict[str, Any] | None,
    round_num: int,
    changes_description: str,
) -> Broken:
    cr = signal.check_result
    params = effective_pipeline_params or {}
    aborted = signal.check_name == SCORING_ERROR_ABORT
    # An abort names an error, not a node, so it shows the whole config.
    node = None if aborted else cr["dominant_warning"].split(":", 1)[0]
    node_cfg = params if node is None else params.get(node, {})
    operator_terminal = cr["fatal"] or (aborted and _abort_is_config_break(cr))
    return Broken(
        failure=RuntimeFailure(
            source="scoring_error_abort" if aborted else "degradation_check",
            dominant_warning=cr["dominant_warning"],
            node=node,
            degraded_rate=cr["degraded_rate"],
            degraded_count=cr["degraded_count"],
            total_scored=cr["total_scored"],
            observed_config=dict(node_cfg),
            first_seen_round=round_num,
            changes_description=changes_description,
            owner=NurseOwner.OPERATOR if operator_terminal else NurseOwner.L1,
        ),
        degradation=cr,
    )


def measuring(
    arm: Proposed,
    sp: JobSearchPoint,
    schema: PipelineSchema,
    *,
    expected: int,
    sample_order: Sequence[int] = (),
    n_priors: int = 0,
    block: Mapping[str, int] | None = None,
) -> Measuring:
    return Measuring(
        role=arm.role,
        slot=arm.slot,
        individual=arm.individual,
        overlay=arm.overlay,
        validation_failures=arm.validation_failures,
        # Off the object the gateway hands `archive_entry`: one id for the arm and its cells' filing.
        sp_hash=sp.sp_hash(schema),
        resolved_params=sp.config_params or {},
        sample_order=list(sample_order),
        n_priors=n_priors,
        block=None if block is None else dict(block),
        expected_samples=expected,
    )


# What a walk carries into its end: every field the walked states share but their discriminator.
_CARRIED = (
    Measuring.model_fields.keys() & Cut.model_fields.keys() & Measured.model_fields.keys()
) - {"state"}


def _concluded_fields(arm: Measuring) -> dict[str, Any]:
    return {name: getattr(arm, name) for name in _CARRIED}


def refuse(arm: Measuring) -> Cut:
    return Cut(**_concluded_fields(arm), reason=Invalid())


def conclude(
    arm: Measuring,
    scored: ScoredWalk,
    *,
    elimination: Elimination | None = None,
    breakage: Broken | None = None,
) -> Concluded:
    fields = _concluded_fields(arm)
    stop = scored.signal.outcome if scored.signal is not None else None
    if stop is None and scored.stopped is WalkEnd.SKIP:
        return Cut(**fields, reason=Skipped())
    if stop is None:
        return Measured(**fields, locked_in=None)
    if stop == "locked_in":
        assert elimination is not None, "a lock-in is the eliminator's stop"
        return Measured(**fields, locked_in=elimination)
    if stop == "broken":
        assert breakage is not None, "a broken walk is read by `read_breakage`"
        return Cut(**fields, reason=breakage)
    assert elimination is not None, "an elimination is the eliminator's stop"
    return Cut(**fields, reason=Eliminated(elimination=elimination))
