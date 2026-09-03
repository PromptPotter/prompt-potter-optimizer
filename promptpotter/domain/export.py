from __future__ import annotations

import json
from typing import Any

from pydantic import ConfigDict

from promptpotter.domain.opt_search_point import PromptTemplate
from promptpotter.domain.pipeline_overlay import node_config_items
from promptpotter.domain.results import RoundResult
from promptpotter.domain.ruler import AbilityReading
from promptpotter.domain.strict_model import StrictModel

EXPORT_ARTIFACT_VERSION = 1
"""Bumped when a reader written against the old shape would MISREAD the new one — not when a
field is added. :func:`parse_prompt_export` refuses anything else."""

__all__ = [
    "EXPORT_ARTIFACT_VERSION",
    "ExportMeasurement",
    "ExportVersionError",
    "PromptExport",
    "build_prompt_export",
    "parse_prompt_export",
]


class ExportVersionError(ValueError):
    """The artifact names a version this build does not read."""


class ExportMeasurement(StrictModel):
    """What the exported prompt scored — and the formula that word means, since fitness here is
    never one fixed number. Every field is copied off the round that crowned the winner, so the
    lift, the interval and θ all stand on one measurement rather than three."""

    model_config = ConfigDict(frozen=True)

    round: int
    # ``None`` when the cycle resolved no round formula at all — then ``composite_fitness`` is
    # the composite of nothing named, and a consumer that needs one should refuse rather than
    # read it. Naming an empty string here would make "unnamed" indistinguishable from a formula
    # called "".
    formula: str | None
    composite_fitness: float
    accuracy: float
    n: int
    # ``None`` on a round that crowned nobody, and on the origin round, whose lift over itself is
    # not a measurement. A consumer reading a lift must be able to tell "zero" from "not asked".
    matched_parent_lift: float | None = None
    matched_parent_lift_ci_lo: float | None = None
    matched_parent_lift_ci_hi: float | None = None
    # Subset-invariant ability, with the δ scale it was read on — an exported θ naming no ruler
    # is a level nothing outside this cycle can be compared against. ``None`` when never fit.
    ability: AbilityReading | None = None
    origin_accuracy: float
    # ``None`` where the origin was never scored — the bar the exported lift is read against, so a
    # stand-in 0.0 hands another program a lift measured off nothing.
    origin_composite_fitness: float | None


class PromptExport(StrictModel):
    """One campaign's answer, self-describing. ``model_dump_json()`` IS ``export.json``."""

    model_config = ConfigDict(frozen=True)

    artifact_version: int
    tool: str
    tool_version: str
    campaign_id: str
    cycle_id: str
    dataset_name: str
    # The rows, order-independent (``shared/hashing.py::dataset_hash``). An exported fitness is
    # only as trustworthy as the identity of what it was measured on.
    dataset_hash: str
    # The optimizer manifest that produced this prompt — a different one is a different search.
    optimizer_prompt_hash: str
    stop_reason: str
    finished_at: str
    # Named fields, restored by ``template()``. Carries ``few_shot_examples`` structured and
    # ``plan``, because this is the round document's dict — NOT ``CycleResult.winner_prompt_fields``,
    # which is the wire-side projection and has already flattened the examples to a rendered block.
    prompt_fields: dict[str, Any]
    # The other half of what this project evolves: the node config the winner ran under, minus
    # each node's rendered ``prompt`` (that is ``prompt_fields`` rendered, and one artifact does
    # not carry a fact twice). Model and provider ride here.
    tuned_params: dict[str, dict[str, Any]]
    measurement: ExportMeasurement

    def template(self) -> PromptTemplate:
        """The winning prompt as the type the rest of this package passes around."""
        return PromptTemplate.from_prompt_fields(self.prompt_fields)


def parse_prompt_export(text: str) -> PromptExport:
    """Read an ``export.json``, refusing a version this build cannot read.

    The version is checked BEFORE validation: a shape change that renamed a field would otherwise
    surface as a pydantic error about that field, which sends the reader looking for a typo
    instead of at the version they are holding.
    """
    raw = json.loads(text)
    if not isinstance(raw, dict):
        raise ExportVersionError(f"not a PromptPotter export: top level is {type(raw).__name__}")
    found = raw.get("artifact_version")
    if found != EXPORT_ARTIFACT_VERSION:
        raise ExportVersionError(
            f"export artifact_version {found!r}, this build reads {EXPORT_ARTIFACT_VERSION}"
        )
    return PromptExport.model_validate(raw)


def build_prompt_export(
    winner: RoundResult,
    *,
    tool_version: str,
    campaign_id: str,
    cycle_id: str,
    dataset_name: str,
    dataset_hash: str,
    optimizer_prompt_hash: str,
    stop_reason: str,
    finished_at: str,
    formula: str | None,
    origin_accuracy: float,
    origin_composite_fitness: float | None,
) -> PromptExport:
    """Project the round that crowned the winner into the artifact.

    *winner* is the round the composite high-water names, and **the origin round is one of them**
    — a campaign nothing beat exports its origin, under round 0, rather than exporting nothing.
    That is the whole special-casing: one round shape in, values that differ, no second path.
    """
    return PromptExport(
        artifact_version=EXPORT_ARTIFACT_VERSION,
        tool="promptpotter",
        tool_version=tool_version,
        campaign_id=campaign_id,
        cycle_id=cycle_id,
        dataset_name=dataset_name,
        dataset_hash=dataset_hash,
        optimizer_prompt_hash=optimizer_prompt_hash,
        stop_reason=stop_reason,
        finished_at=finished_at,
        prompt_fields=dict(winner.prompt_fields),
        tuned_params=_tuned_params(winner.pipeline_params),
        measurement=ExportMeasurement(
            round=winner.round,
            formula=formula,
            composite_fitness=winner.composite_fitness,
            accuracy=winner.accuracy,
            n=winner.total,
            matched_parent_lift=winner.matched_parent_lift,
            matched_parent_lift_ci_lo=winner.matched_parent_lift_ci_lo,
            matched_parent_lift_ci_hi=winner.matched_parent_lift_ci_hi,
            ability=winner.ability,
            origin_accuracy=origin_accuracy,
            origin_composite_fitness=origin_composite_fitness,
        ),
    )


def _tuned_params(pipeline_params: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Node configs off the canonical walk, each minus its rendered ``prompt``."""
    return {
        node: {k: v for k, v in cfg.items() if k != "prompt"}
        for node, cfg in node_config_items(pipeline_params)
    }
