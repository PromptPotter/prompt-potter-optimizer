from __future__ import annotations

import enum
import json
from dataclasses import asdict, dataclass
from typing import Any

from pydantic import ConfigDict, Field

from promptpotter.domain.strict_model import StrictModel


class EscalationTarget(enum.StrEnum):
    ELIMINATE_CANDIDATE = "eliminate_candidate"
    LEADER_LOCKED = "leader_locked"


class ExplorationBudget(enum.StrEnum):
    """How freely ``l1_generate`` may explore. The single source for the ``escalation_panel.exploration_budget`` signal AND
    for the value the review writer feeds ``ValidatorContext``, so prompt and validator cannot disagree."""

    TIGHT = "tight"  # improving — exploit the parent; speculative gambles rejected
    NORMAL = "normal"  # stalling — stall_exploration citations permitted
    WIDE = "wide"  # patience exhausted — explore freely; a PEAKED axis is mutable with a wide rebut


def exploration_budget(stall_count: int, l1_patience: int) -> ExplorationBudget:
    """Widen the budget with MEASURED L1 stall depth, never a round-count schedule. Pure, and called by both the prompt side
    and the validator side with the same stall depth, so the two consumers can never disagree on the mapping."""
    if stall_count <= 0:
        return ExplorationBudget.TIGHT
    if stall_count >= l1_patience:
        return ExplorationBudget.WIDE
    return ExplorationBudget.NORMAL


class NurseOwner(enum.StrEnum):
    """Who heals a wound. Stamped only on ``RuntimeFailure`` — the one wound whose owner genuinely varies; the other two are structural.
    A member earns its place once a producer stamps it, which is why ``L3`` is absent."""

    L1 = "l1"
    OPERATOR = "operator"


@dataclass
class EscalationSignal:
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


# The reasons that mark a candidate COLLAPSED — generated, then rejected before it could cost
# a backend call. Two are round-local (`no_op_variant`: the delta is empty; `duplicate_variant`:
# two identical signatures in one population) and one is cross-round (`repeat_variant`: an idea
# a prior round already measured and lost).
#
# The vocabulary lives in `domain/` beside `ValidationFailure` because it is domain language,
# not a detail of the validator that happens to emit it. Two layers must agree on it: the
# validator that WRITES these reasons (`validators/l1_strict.py`) and `RoundResult`, which
# DERIVES its collapse counts by reading them back off `candidate_scores`. Left in the
# application layer, the domain side could not name the set it counts without an upward import.
INVARIANT_REASONS: frozenset[str] = frozenset(
    {"no_op_variant", "duplicate_variant", "repeat_variant"}
)


class ValidationFailure(StrictModel):
    """L1-output parse-time invariant violation; drives synthetic-0 in ``score_search_point``.

    Surfaced to L2's prompt as JSON (via ``model_dump``) so the field
    semantics here are part of the L2 contract — not just a Python
    schema.
    """

    model_config = ConfigDict(frozen=True)

    axis: str = Field(
        description=(
            "The parameter path that failed, ``{node_name}.{param}`` "
            "(e.g. ``llm_only.model``), or an axis on the generator's own "
            "output rather than a pipeline param: ``l1_generate.output`` "
            "(parse/shape failures) or ``variant`` (no-op / duplicate mutations)."
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
            "``l1_provider_empty_response`` / ``optimizer_prompt_parse_failure`` "
            "/ ``optimizer_prompt_unexpected_type`` (generator-side failure), "
            "``steers_inner_stopping`` (an L4 override telling the inner loop when to STOP "
            "rather than how to search — it edits the frame `mean_round_delta` averages over "
            "instead of the search it measures), "
            "``steers_across_seeds`` (an L4 override making an inner node reason over the seed "
            "panel, which exists only one level up — the rule renders empty where it lands), "
            "``guts_inherited_contract`` (an L4 override replacing a long prompt field with a "
            "fraction of its length, deleting contracts the incumbent carried in plain prose), "
            "``no_op_variant`` / ``duplicate_variant`` (invariant-detect), "
            "``hallucinated_node`` (named a node absent from the schema — "
            "NON-fatal: the phantom edit is stripped, the candidate still scores; "
            "routed as signal, not a synthetic-0)."
        ),
    )


class RuntimeFailure(StrictModel):
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


def rf_dedup_key(rf_dict: dict[str, Any]) -> tuple[str, str, str]:
    """Dedup key for a serialized runtime failure. Pure and canonical, so intra-cycle dedup and cross-cycle sibling-wound
    inheritance match without a hand-synced mirror."""
    return (
        rf_dict["source"],
        rf_dict["dominant_warning"],
        json.dumps(rf_dict["observed_config"], sort_keys=True, default=str),
    )


__all__ = [
    "INVARIANT_REASONS",
    "EscalationSignal",
    "EscalationTarget",
    "ExplorationBudget",
    "NurseOwner",
    "RuntimeFailure",
    "ValidationFailure",
    "exploration_budget",
    "rf_dedup_key",
]
