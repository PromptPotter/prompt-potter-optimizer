from __future__ import annotations

import enum
import json
from collections.abc import Iterable
from typing import Any

from pydantic import Field

from promptpotter.domain.strict_model import FrozenModel


class NurseOwner(enum.StrEnum):
    """Who heals a runtime failure."""

    # A member earns its place once a producer stamps it, which is why ``L3`` is absent.
    L1 = "l1"
    OPERATOR = "operator"


class ValidationReason(enum.StrEnum):
    """Why an L1 output was refused, the code that steers L2's healing direction."""

    # A node's param, set off the axis's resolved space or its declared shape.
    FORBIDDEN_AXIS = "forbidden_axis"
    UNKNOWN_PARAM = "unknown_param"
    TYPE_MISMATCH = "type_mismatch"
    NOT_IN_AVAILABLE_MODELS = "not_in_available_models"
    NOT_IN_PARAM_ALLOWED_VALUES = "not_in_param_allowed_values"
    # Declared by the node, refused by the chosen model: the healing move is the model.
    NOT_ACCEPTED_BY_MODEL = "not_accepted_by_model"
    # Matches a prior `RuntimeFailure.observed_config` row.
    REPROPOSES_KNOWN_FAILING_CONFIG = "reproposes_known_failing_config"
    # Non-fatal: the phantom node's edit is stripped and the candidate still scores.
    HALLUCINATED_NODE = "hallucinated_node"
    DROPPED_MANDATORY_PLACEHOLDER = "dropped_mandatory_placeholder"
    # Few-shot demos and pooled blocks, out of what the round offered.
    SHOTS_WITHOUT_DEMO_POOL = "shots_without_demo_pool"
    SHOT_NOT_IN_DEMO_POOL = "shot_not_in_demo_pool"
    SHOT_REPEATED = "shot_repeated"
    SHOTS_OVER_CAP = "shots_over_cap"
    NOT_IN_BLOCK_POOL = "not_in_block_pool"
    CITATION_NOT_IN_PANEL = "citation_not_in_panel"
    OVER_CEILING = "over_ceiling"
    # The generator's own reply.
    OPTIMIZER_PROMPT_PARSE_FAILURE = "optimizer_prompt_parse_failure"
    L1_PROVIDER_EMPTY_RESPONSE = "l1_provider_empty_response"
    # An L4 override: stopping the inner loop, reasoning over the seed panel one level up, or
    # replacing a long prompt field with a fraction of it.
    STEERS_INNER_STOPPING = "steers_inner_stopping"
    STEERS_ACROSS_SEEDS = "steers_across_seeds"
    GUTS_INHERITED_CONTRACT = "guts_inherited_contract"
    # An L4 layout edit: on a node no layout is offered for, or one `resolve_layout_override` refuses.
    LAYOUT_NODE_NOT_L4_EDITABLE = "layout_node_not_l4_editable"
    L1_LAYOUT_UNPARSEABLE = "l1_layout_unparseable"
    L1_LAYOUT_MISSING_MANDATORY = "l1_layout_missing_mandatory"
    L1_LAYOUT_UNKNOWN_PLACEHOLDER = "l1_layout_unknown_placeholder"
    L1_LAYOUT_VOIDS_PREFIX = "l1_layout_voids_prefix"
    L1_LAYOUT_UNCHANGED_FROM_PRIOR = "l1_layout_unchanged_from_prior"
    # Invariant-detect: a collapse, counted by `collapse_counts`.
    NO_OP_VARIANT = "no_op_variant"
    DUPLICATE_VARIANT = "duplicate_variant"
    REPEAT_VARIANT = "repeat_variant"


# In `domain/`: `RoundResult` counts collapses off these and may not import their validator.
INVARIANT_REASONS: frozenset[str] = frozenset(
    {"no_op_variant", "duplicate_variant", "repeat_variant"}
)

# The word for each, on every surface: the inner narrative renders it into the outer's prompt.
COLLAPSE_WORDS: dict[str, str] = {
    "no_op_variant": "no-op",
    "duplicate_variant": "duplicate",
    "repeat_variant": "repeat",
}
assert COLLAPSE_WORDS.keys() == INVARIANT_REASONS


class ValidationFailure(FrozenModel):
    """An L1 output that broke a parse-time invariant, scoring its candidate a synthetic zero."""

    # Rendered into L2's prompt as JSON (``model_dump``): a field renamed here rewrites that prompt.
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
    reason: ValidationReason


def collapse_reason(failures: Iterable[ValidationFailure]) -> str | None:
    """One per proposal, or the collapse counts would sum past the population."""
    return next((vf.reason for vf in failures if vf.reason in INVARIANT_REASONS), None)


def collapse_counts(populations: Iterable[Iterable[ValidationFailure]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for failures in populations:
        if reason := collapse_reason(failures):
            counts[reason] = counts.get(reason, 0) + 1
    return counts


class RuntimeFailure(FrozenModel):
    """One candidate's degradation evidence from its evaluation, beside a score that stands."""

    # Rendered into L2's prompt as JSON (``model_dump``): a field renamed here rewrites that prompt.
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
    node: str | None = Field(
        description=(
            "The node whose config ``observed_config`` captures: the ``dominant_warning`` "
            "prefix. ``None`` on a ``scoring_error_abort``, which names an error, not a node, "
            "and captures the whole config."
        ),
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
    changes_description: str = Field(
        default="",
        description="What the originating candidate changed; informational, not a join key.",
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


_NON_FATAL_REASONS = frozenset({"hallucinated_node"})
assert not INVARIANT_REASONS & _NON_FATAL_REASONS


def fatal_validation_failures(failures: Iterable[ValidationFailure]) -> list[ValidationFailure]:
    return [vf for vf in failures if vf.reason not in _NON_FATAL_REASONS]


def rf_dedup_key(rf_dict: dict[str, Any]) -> tuple[str, str, str]:
    """Canonical, so intra-cycle dedup and cross-cycle sibling-wound inheritance match."""
    return (
        rf_dict["source"],
        rf_dict["dominant_warning"],
        json.dumps(rf_dict["observed_config"], sort_keys=True, default=str),
    )
