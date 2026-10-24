from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, Literal, NamedTuple, get_args

from pydantic import ConfigDict, Field

from promptpotter.domain.bench import BenchScore, OwnLevel
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.paired_reading import PairedReading
from promptpotter.domain.phases import StopReason
from promptpotter.domain.pipeline_overlay import node_config_items
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.domain.results import RoundOutcome, RoundResult
from promptpotter.domain.ruler import SeriesReading
from promptpotter.domain.sample import Sample
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.domain.treatment import Treatment

EXPORT_ARTIFACT_VERSION = 8
"""Bumped only when a reader of the old shape would MISREAD the new one, never for an added field."""

ExportTier = Literal["chat", "report", "notebook", "deploy", "package"]


class ExportTierInfo(NamedTuple):
    label: str
    # Text a person pastes; otherwise files a program opens.
    pasted: bool


# One reading of a cycle per reader's next move, the same names on every surface.
EXPORT_TIER_INFO: dict[ExportTier, ExportTierInfo] = {
    "chat": ExportTierInfo("Chat — the candidates as compact text", pasted=True),
    "report": ExportTierInfo("Report — that text under its provenance header", pasted=True),
    "notebook": ExportTierInfo("Notebook — candidates.jsonl and its dataset card", pasted=False),
    "deploy": ExportTierInfo("Deploy — export.json", pasted=False),
    "package": ExportTierInfo("Package — a skill folder for another agent", pasted=False),
}
assert tuple(EXPORT_TIER_INFO) == get_args(ExportTier)

# JSON-LD to a reader that cares, a dict to one that does not: PROV-O names the lineage edges.
PROV_CONTEXT: dict[str, Any] = {
    "prov": "http://www.w3.org/ns/prov#",
    "id": "@id",
    "wasDerivedFrom": {"@id": "prov:wasDerivedFrom", "@type": "@id"},
    "wasGeneratedBy": {"@id": "prov:wasGeneratedBy"},
}


class _ProvModel(FrozenModel):
    model_config = ConfigDict(
        validate_by_name=True, validate_by_alias=True, serialize_by_alias=True
    )


class ExportVersionError(ValueError):
    """The artifact names a version this build does not read."""


class RoundActivity(_ProvModel):
    """The round that proposed a candidate, as a PROV activity; every round measured on the export's `dataset_hash`."""

    round: int
    members: list[str] = Field(
        description="`{manifest}:{node}` of each variation node that wrote the candidate, in the "
        "order they ran; empty on the origin."
    )


class LineageEntity(_ProvModel):
    """One candidate as a sparse delta over its first parent; the origin states every locus once."""

    id: str = Field(description="The arm's key, which is its individual's id where it was valid.")
    label: str
    was_derived_from: list[str] = Field(
        alias="wasDerivedFrom", description="The ids of the candidates it was mutated from."
    )
    was_generated_by: RoundActivity = Field(alias="wasGeneratedBy")
    changes: dict[str, Any] = Field(
        description="By locus (`node.key`), its value where it differs from its first parent: "
        "null where the parent set the locus and it does not. Compose a candidate by walking "
        "`wasDerivedFrom` to the origin and laying each delta on."
    )
    changes_description: str


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


class PromptExport(_ProvModel):
    """One campaign's answer, self-describing. ``to_json()`` IS ``export.json``."""

    context: dict[str, Any] = Field(default=PROV_CONTEXT, alias="@context")
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
    # Every candidate the cycle proposed, origin first: the prompts stated once, then deltas.
    lineage: list[LineageEntity]

    def render(self, node: str) -> str:
        return "\n\n".join(self.prompts[node].values())

    def to_json(self) -> str:
        return self.model_dump_json(indent=2) + "\n"


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
    if "lineage" not in raw:
        raise ExportVersionError(
            "its export.json is pre-lineage, written before candidates were exported"
        )
    return PromptExport.model_validate(raw)


def build_prompt_export(
    winner: RoundResult,
    rounds: Sequence[RoundResult],
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
    selected = (
        None if election is None or election.selected is None else winner.fit(election.selected)
    )
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
        lineage=export_lineage(rounds),
    )


def export_lineage(rounds: Sequence[RoundOutcome]) -> list[LineageEntity]:
    """Each proposed arm once, by its first walk: a re-read parent and a re-proposal add nothing."""
    seen: dict[str, OptSearchPoint] = {}
    out: list[LineageEntity] = []
    for rr in rounds:
        for key, arm in rr.panel.items():
            if key in seen:
                continue
            individual = seen[key] = arm.individual
            lineage = individual.lineage
            parent = next((seen[p] for p in lineage.parent_ids[:1] if p in seen), None)
            out.append(
                LineageEntity(
                    id=key,
                    label=arm.label,
                    was_derived_from=list(lineage.parent_ids),
                    was_generated_by=RoundActivity(
                        round=rr.round if arm.slot is None else arm.slot.round,
                        members=[v.node for v in lineage.variations],
                    ),
                    changes=_delta(individual, parent),
                    changes_description=lineage.changes_description,
                )
            )
    return out


def _delta(individual: OptSearchPoint, parent: OptSearchPoint | None) -> dict[str, Any]:
    """Against no parent on record the candidate is a base layer: every locus it sets."""
    mine = individual.loci()
    if parent is None:
        return {locus.name: value for locus, value in mine.items()}
    by_name = {locus.name: value for locus, value in mine.items()}
    return {name: by_name.get(name) for name in individual.loci_moved_from(parent)}


def _tuned_params(pipeline_params: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    return {
        node: {k: v for k, v in cfg.items() if k != "prompt"}
        for node, cfg in node_config_items(pipeline_params)
    }
