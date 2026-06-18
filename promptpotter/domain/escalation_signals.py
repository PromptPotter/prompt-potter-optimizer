"""Escalation signals + self-healing failure types — pure data, no I/O."""

from __future__ import annotations

import enum
from dataclasses import asdict, dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EscalationTarget(enum.StrEnum):
    """What a per-candidate PoBB check decides about the candidate in flight."""

    ELIMINATE_CANDIDATE = "eliminate_candidate"
    LEADER_LOCKED = "leader_locked"


class ExplorationBudget(enum.StrEnum):
    """How freely ``l1_generate`` may explore, widening with L1 stall depth.

    The single source for the ``escalation_panel.exploration_budget`` signal the
    l1_generate supplemental rules cite, and for the value ``review.py`` feeds
    ``ValidatorContext`` so ``evidence_grounding_present`` can gate the
    ``stall_exploration`` escape hatch and the PEAKED-axis rebut.
    """

    TIGHT = "tight"  # improving — exploit the parent; speculative gambles rejected
    NORMAL = "normal"  # stalling — stall_exploration citations permitted
    WIDE = "wide"  # patience exhausted — explore freely; a PEAKED axis is mutable with a wide rebut


def exploration_budget(stall_count: int, l1_patience: int) -> ExplorationBudget:
    """Widen the budget with measured L1 stall depth (NOT a round-count schedule).

    ``0`` stall → TIGHT; a partial stall → NORMAL; at/over ``l1_patience`` (the loop
    is about to escalate to L2) → WIDE. Pure: both the bundle build (prompt side) and
    ``review.py`` (validator side) call this with the stall depth in effect for the
    round, so the two consumers can never disagree on the mapping.
    """
    if stall_count <= 0:
        return ExplorationBudget.TIGHT
    if stall_count >= l1_patience:
        return ExplorationBudget.WIDE
    return ExplorationBudget.NORMAL


class NurseOwner(enum.StrEnum):
    """Who heals a wound: the in-loop generator (L1) or the operator.

    Stamped on a :class:`RuntimeFailure` — the one wound whose owner genuinely
    varies (an L1-retunable degradation vs an operator-terminal break no in-loop
    layer can reach). The other two wounds carry no owner field because theirs is
    structural, not a choice: a :class:`ValidationFailure` is always L1's own
    malformed output, and a guard-breach :class:`ValidatorOutcome` always routes
    to L3 via the non-empty-stream → ``escalate_l2`` mechanism. A member earns its
    place only once a producer stamps it — `L3` isn't here because nothing does.
    """

    L1 = "l1"
    OPERATOR = "operator"


@dataclass
class EscalationSignal:
    """Signal emitted when an escalation check triggers mid-round."""

    check_name: str
    target: EscalationTarget
    check_result: dict[str, Any]
    candidate_idx: int
    candidates_scored: int
    candidates_skipped: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def is_elimination(self) -> bool:
        return self.target is EscalationTarget.ELIMINATE_CANDIDATE

    @property
    def is_leader_lock(self) -> bool:
        return self.target is EscalationTarget.LEADER_LOCKED


class ValidationFailure(BaseModel):
    """L1-output parse-time invariant violation; drives synthetic-0 in ``score_search_point``.

    Surfaced to L2's prompt as JSON (via ``model_dump``) so the field
    semantics here are part of the L2 contract — not just a Python
    schema.
    """

    model_config = ConfigDict(frozen=True)

    axis: str = Field(
        description=(
            "The parameter path that failed, ``{node_name}.{param}`` "
            "(e.g. ``llm_only.model``), or a meta-axis on the generator "
            "output itself: ``l1_generate.output`` (parse/shape failures) "
            "or ``variant`` (no-op / duplicate mutations)."
        ),
    )
    value: str = Field(
        description=(
            "The offending value as rendered for the prompt; truncated to "
            "≤300 chars for raw LLM output. Always a string — original "
            "type is encoded into the rendering when relevant."
        ),
    )
    allowed: list[str] = Field(
        description=(
            "The accept-set the validator checked against. Empty list "
            "when the failure is mode-based (parse error, forbidden axis) "
            "rather than membership-based."
        ),
    )
    reason: str = Field(
        description=(
            "Reason code that steers L2's healing direction. One of: "
            "``forbidden_axis`` (operator-locked param touched), "
            "``type_mismatch`` (wrong declared type), "
            "``not_in_available_models`` / ``not_in_param_allowed_values`` "
            "(value outside schema enum), "
            "``reproposes_known_failing_config`` (matches a prior "
            "``RuntimeFailure.observed_config`` row), "
            "``l1_provider_empty_response`` / ``meta_prompt_parse_failure`` "
            "/ ``meta_prompt_unexpected_type`` (generator-side failure), "
            "``no_op_variant`` / ``duplicate_variant`` (invariant-detect)."
        ),
    )


class RuntimeFailure(BaseModel):
    """Post-eval degradation evidence, per-candidate.

    On ``OptSearchPoint.wounds.runtime_failures``; surfaced in the score
    report + ingested by L2 next round. Does NOT drive synthetic-0 —
    real score stands. Field semantics are part of the L2 contract
    (rendered as JSON via ``model_dump``).
    """

    model_config = ConfigDict(frozen=True)

    source: str = Field(
        description=(
            "Failure family: ``degradation`` (mid-eval DegradationCheck "
            "fired on warning-rate) or ``scoring_error_abort`` (scoring "
            "raised mid-eval and the candidate was retired)."
        ),
    )
    dominant_warning: str = Field(
        description=(
            "``{node_name}:{warning_type}`` of the most frequent warning "
            "across the candidate's measurements; the prefix selects the "
            "node whose config is captured in ``observed_config``."
        ),
    )
    warning_types: dict[str, int] = Field(
        description="Tally of each ``{node}:{warning}`` seen, by occurrence count.",
    )
    degraded_rate: float = Field(
        description="Fraction of scored samples that emitted any pipeline warning.",
    )
    degraded_count: int = Field(description="Sample count with at least one warning.")
    total_scored: int = Field(description="Sample count actually measured for the candidate.")
    observed_config: dict[str, Any] = Field(
        description=(
            "Snapshot of the failing node's effective config — the "
            "(param, value) tuples the `l1_config_not_in_runtime_failures` "
            "validator scans on subsequent rounds to reject re-proposals."
        ),
    )
    first_seen_round: int = Field(
        default=0,
        description="Round number that first recorded this failure (dedup key component).",
    )
    candidate_label: str = Field(
        default="",
        description="Label of the originating candidate; informational, not a join key.",
    )
    owner: NurseOwner = Field(
        default=NurseOwner.L1,
        description=(
            "Who heals this runtime failure. Defaults to ``L1`` (a rate-based "
            "degradation L1 retunes the node config around); a deterministic-for-"
            "config break whose only fix is a locked surface (schema/model) is "
            "stamped ``OPERATOR`` so it escalates instead of churning in-loop."
        ),
    )


__all__ = [
    "EscalationSignal",
    "EscalationTarget",
    "ExplorationBudget",
    "NurseOwner",
    "RuntimeFailure",
    "ValidationFailure",
    "exploration_budget",
]
