from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from pydantic import ConfigDict, Field

from promptpotter.domain.bench import BenchScore
from promptpotter.domain.campaign import Treatment
from promptpotter.domain.opt_search_point import FEW_SHOT_BLOCK, PromptTemplate
from promptpotter.domain.phases import StopReason
from promptpotter.domain.pipeline_overlay import node_config_items
from promptpotter.domain.results import RoundResult
from promptpotter.domain.ruler import AbilityReading
from promptpotter.domain.sample import Sample
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.domain.strict_model import StrictModel

EXPORT_ARTIFACT_VERSION = 3
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
    composite_fitness: float | None
    accuracy: float | None
    n: int
    # ``None`` on a round that crowned nobody, and on the origin round, whose lift over itself is
    # not a measurement. A consumer reading a lift must be able to tell "zero" from "not asked".
    reference_lift: float | None = Field(
        default=None,
        description="The winner over its PARENT in `accuracy`, paired per cell both measured — "
        "never in `composite_fitness`; its bar is `reference_accuracy`.",
    )
    reference_lift_ci_lo: float | None = None
    reference_lift_ci_hi: float | None = None
    reference_accuracy: float | None = Field(
        default=None,
        description="The parent's `accuracy` on its own panel; `None` unless the winner covered it.",
    )
    # Subset-invariant ability, with the δ scale it was read on — an exported θ naming no ruler
    # is a level nothing outside this cycle can be compared against. ``None`` when never fit, or
    # where the winner's selector stamps no θ.
    ability: AbilityReading | None = None
    origin_accuracy: float | None
    # ``None`` where the origin was never scored — the level ``composite_fitness`` is compared
    # against, so a stand-in 0.0 hands another program a gain measured off nothing.
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
    # The optimizer that produced this prompt — a different one is a different search.
    treatment: Treatment | None
    stop_reason: StopReason
    finished_at: str
    # Named fields, restored by ``template()`` — the round document's dict, NOT
    # ``CycleResult.result_prompt_fields``, which is the wire-side projection.
    prompt_fields: dict[str, Any]
    # The shots as scored. An individual names them by demo-pool id, and a reader outside this
    # campaign has no pool to resolve an id against.
    few_shot_block: str
    # The other half of what this project evolves: the node config the winner ran under, minus
    # each node's rendered ``prompt`` (that is ``prompt_fields`` rendered, and one artifact does
    # not carry a fact twice). Model and provider ride here.
    tuned_params: dict[str, dict[str, Any]]
    # The optimizer's own reading of its selection, on the rows that chose it — not an estimate.
    measurement: ExportMeasurement
    # The deployment estimate: the same prompt on a bench set no optimizer node read. `None` where
    # the campaign holds nothing out, or the cycle ended before its selection could be graded.
    bench: BenchScore | None

    def template(self) -> PromptTemplate:
        """The winning prompt's fields as the type the rest of this package passes around."""
        return PromptTemplate.from_prompt_fields(self.prompt_fields)

    def render(self) -> str:
        """The winning prompt as it was scored: the fields, then the shots."""
        return "\n\n".join(p for p in (self.template().render(), self.few_shot_block) if p)


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
    treatment: Treatment | None,
    stop_reason: StopReason,
    finished_at: str,
    formula: str | None,
    origin_accuracy: float | None,
    origin_composite_fitness: float | None,
    framing: TaskDecomposition,
    demo: Sequence[Sample],
    bench: BenchScore | None,
) -> PromptExport:
    """Project the round that crowned the winner into the artifact.

    *winner* is the round whose selection the optimizer declared last, and **the origin round is
    one of them** — a campaign that never selected past it exports its origin, under round 0.
    That is the whole special-casing: one round shape in, values that differ, no second path.
    """
    fields = dict(winner.prompt_fields)
    fields.pop("shot_ids", None)
    selected = next(iter(winner.selected_scores), None)
    # The operator's framing splices into `problem_description` at render, so the stored fields
    # alone re-render a prompt nothing was scored on.
    rendered = dict(winner.opt_sp.target_fields(framing, demo=demo)) if winner.opt_sp else {}
    if spliced := rendered.get("problem_description"):
        fields["problem_description"] = spliced
    return PromptExport(
        artifact_version=EXPORT_ARTIFACT_VERSION,
        tool="promptpotter",
        tool_version=tool_version,
        campaign_id=campaign_id,
        cycle_id=cycle_id,
        dataset_name=dataset_name,
        dataset_hash=dataset_hash,
        treatment=treatment,
        stop_reason=stop_reason,
        finished_at=finished_at,
        prompt_fields=fields,
        few_shot_block=rendered.get(FEW_SHOT_BLOCK, ""),
        tuned_params=_tuned_params(winner.pipeline_params),
        measurement=ExportMeasurement(
            round=winner.round,
            formula=formula,
            composite_fitness=winner.composite_fitness,
            accuracy=winner.accuracy,
            n=winner.total,
            reference_lift=selected.reference_lift if selected else None,
            reference_lift_ci_lo=selected.reference_lift_ci_lo if selected else None,
            reference_lift_ci_hi=selected.reference_lift_ci_hi if selected else None,
            reference_accuracy=selected.reference_accuracy if selected else None,
            ability=winner.ability if winner.stamps_theta else None,
            origin_accuracy=origin_accuracy,
            origin_composite_fitness=origin_composite_fitness,
        ),
        bench=bench,
    )


def _tuned_params(pipeline_params: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Node configs off the canonical walk, each minus its rendered ``prompt``."""
    return {
        node: {k: v for k, v in cfg.items() if k != "prompt"}
        for node, cfg in node_config_items(pipeline_params)
    }
