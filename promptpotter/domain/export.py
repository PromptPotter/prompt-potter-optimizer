from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from pydantic import ConfigDict, Field

from promptpotter.domain.bench import BenchScore, OwnLevel
from promptpotter.domain.campaign import Treatment
from promptpotter.domain.opt_search_point import FEW_SHOT_BLOCK, PromptTemplate
from promptpotter.domain.paired_reading import PairedReading
from promptpotter.domain.phases import StopReason
from promptpotter.domain.pipeline_overlay import node_config_items
from promptpotter.domain.results import RoundResult
from promptpotter.domain.ruler import AbilityReading
from promptpotter.domain.sample import Sample
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.domain.strict_model import StrictModel

EXPORT_ARTIFACT_VERSION = 6
"""Bumped only when a reader of the old shape would MISREAD the new one, never for an added field."""

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
    """Every field is read off the round that crowned the winner, so all stand on one measurement."""

    model_config = ConfigDict(frozen=True)

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
    # ``None`` when never fit, or where the winner's selector stamps no θ.
    ability: AbilityReading | None


class PromptExport(StrictModel):
    """One campaign's answer, self-describing. ``model_dump_json()`` IS ``export.json``."""

    model_config = ConfigDict(frozen=True)

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
    # The round file's dict, NOT ``CycleResult.result_prompt_fields`` (the wire-side projection).
    prompt_fields: dict[str, Any]
    # Resolved: a reader outside this campaign has no demo pool to resolve a shot id against.
    few_shot_block: str
    # Minus each node's rendered ``prompt``, which is ``prompt_fields`` rendered.
    tuned_params: dict[str, dict[str, Any]]
    # The optimizer's own reading on the rows that chose it: not an estimate.
    measurement: ExportMeasurement
    # The deployment estimate: the same prompt on a bench set no optimizer node read.
    bench: BenchScore

    def template(self) -> PromptTemplate:
        return PromptTemplate.from_prompt_fields(self.prompt_fields)

    def render(self) -> str:
        return "\n\n".join(p for p in (self.template().render(), self.few_shot_block) if p)


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
    framing: TaskDecomposition,
    demo: Sequence[Sample],
    bench: BenchScore,
) -> PromptExport:
    """*winner* may be the origin round: a campaign that never selected past it exports round 0."""
    fields = dict(winner.prompt_fields)
    fields.pop("shot_ids", None)
    selected = next(iter(winner.selected_scores), None)
    # Framing splices in at render: the stored fields alone re-render a prompt nothing was scored on.
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
            own=own,
            vs_reference=selected.vs_reference if selected else None,
            vs_origin=winner.overlap.lead,
            ability=winner.ability,
        ),
        bench=bench,
    )


def _tuned_params(pipeline_params: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    return {
        node: {k: v for k, v in cfg.items() if k != "prompt"}
        for node, cfg in node_config_items(pipeline_params)
    }
