from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from pydantic import Field

from promptpotter.domain.bench import BenchScore, OwnLevel
from promptpotter.domain.paired_reading import PairedReading
from promptpotter.domain.phases import StopReason
from promptpotter.domain.pipeline_overlay import node_config_items
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.domain.results import RoundResult
from promptpotter.domain.ruler import SeriesReading
from promptpotter.domain.sample import Sample
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.domain.treatment import Treatment

EXPORT_ARTIFACT_VERSION = 8
"""Bumped only when a reader of the old shape would MISREAD the new one, never for an added field."""


class ExportVersionError(ValueError):
    """The artifact names a version this build does not read."""


class ExportMeasurement(FrozenModel):
    """Every field is read off the round that crowned the winner, so all stand on one measurement."""

    round: int
    # ``None`` when the cycle resolved no round formula; never ``""``, which reads as a formula called "".
    formula: str | None
    own: OwnLevel = Field(
        description="The winner's level on the rows its round read it on, in both columns."
    )
    # ``None`` on a round that crowned nobody and on the origin round: "not asked" is not "zero".
    vs_reference: PairedReading | None = Field(
        description="The winner over its reference, paired per cell both scored: accuracy as "
        "the headline, the composite beside it.",
    )
    vs_origin: PairedReading = Field(
        description="The winner over the origin on the origin panel (the round's overlap lead), "
        "or the state that says why it was not read. The origin's level is its `rate_a`."
    )
    # ``None`` where no scored cell sits on the series' scale.
    ability: SeriesReading | None


class PromptExport(FrozenModel):
    """One campaign's answer, self-describing. ``model_dump_json()`` IS ``export.json``."""

    artifact_version: int
    tool: str
    tool_version: str
    campaign_id: str
    cycle_id: str
    dataset_name: str
    dataset_hash: str
    treatment: Treatment | None
    stop_reason: StopReason
    finished_at: str
    # Per prompt node, each field AS SCORED in render order — framing spliced, shots resolved as
    # `few_shot_block`, since a reader outside this campaign has no demo pool to resolve a key against.
    prompts: dict[str, dict[str, str]]
    # Minus each node's rendered ``prompt``, which is ``prompts`` rendered.
    tuned_params: dict[str, dict[str, Any]]
    # The optimizer's own reading on the rows that chose it: not an estimate.
    measurement: ExportMeasurement
    # The deployment estimate: the same prompt on a bench set no optimizer node read.
    bench: BenchScore

    def render(self, node: str) -> str:
        return "\n\n".join(self.prompts[node].values())


def parse_prompt_export(text: str) -> PromptExport:
    """The version is checked BEFORE validation, so a renamed field reads as a version mismatch."""
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
    own: OwnLevel,
    level: SeriesReading | None,
    schema: PipelineSchema,
    framing: TaskDecomposition,
    demo: Sequence[Sample],
    bench: BenchScore,
) -> PromptExport:
    """*winner* may be the origin round: a campaign that never selected past it exports round 0."""
    election, head = winner.election, winner.head
    selected = None if election is None else next(map(winner.fit, election.selected), None)
    # Framing splices in at render: the stored fields alone re-render a prompt nothing was scored on.
    individual = None if head is None else head.individual
    prompts = {} if individual is None else individual.scored_prompts(schema, framing, demo=demo)
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
        prompts=prompts,
        tuned_params=_tuned_params(None if individual is None else individual.pipeline_params),
        measurement=ExportMeasurement(
            round=winner.round,
            formula=formula,
            own=own,
            vs_reference=selected.vs_reference if selected else None,
            vs_origin=winner.overlap.lead,
            ability=level,
        ),
        bench=bench,
    )


def _tuned_params(pipeline_params: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    return {
        node: {k: v for k, v in cfg.items() if k != "prompt"}
        for node, cfg in node_config_items(pipeline_params)
    }
