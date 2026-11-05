from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NamedTuple

from pydantic import ValidationError

from promptpotter.application.bench.task_context import (
    CheckinOutput,
    checkin_output,
    checkin_provider,
    draft_call_context,
    run_checkin,
)
from promptpotter.application.datasets.draft_campaign import draft_criterion
from promptpotter.application.datasets.origin_readiness import (
    ChecklistField,
    checkin_questions,
    checklist_field,
    field_values,
    origin_readiness,
    patch_at,
)
from promptpotter.application.jobs.quota import paid_call
from promptpotter.application.scoring.formula.matchers import MATCHERS
from promptpotter.domain.checkin_draft import (
    DraftFile,
    OriginLastResolution,
    OriginResolution,
    RaisedCommand,
)
from promptpotter.domain.command_kinds import EditDraftCampaignPayload
from promptpotter.domain.draft_edit import EditDraftPatch
from promptpotter.domain.opt_search_point import PROMPT_FIELDS
from promptpotter.domain.origin_provenance import Provenance
from promptpotter.infrastructure.store.stores import Stores

if TYPE_CHECKING:
    from collections.abc import Sequence

    from promptpotter.application.pipeline_resolve import DraftPipeline
    from promptpotter.domain.pipeline_schema import PipelineNode

_PREVIEW_ROWS = 10


@dataclass(frozen=True, slots=True)
class OriginProposal:
    field: str
    row: ChecklistField
    value: Any
    # The one edit writing *value* at the row's path, which the turn applies or raises.
    patch: EditDraftPatch
    confidence: str
    evidence: str

    def command(self, draft_id: str) -> RaisedCommand:
        return RaisedCommand(
            payload=EditDraftCampaignPayload(draft_id=draft_id, patch=self.patch),
            label=self.row.proposal.format(self.value),
            evidence=self.evidence,
        )


@dataclass(frozen=True, slots=True)
class OriginResolutionResult:
    resolution: OriginResolution
    draft: DraftFile


class OriginConsultation(NamedTuple):
    user_content: str
    instruction: str
    # Offers only the prompt fields the chain's prompt nodes declare: the ones a turn may write.
    response_model: type[CheckinOutput]


def build_origin_consultation(draft: DraftFile, pipeline: DraftPipeline) -> OriginConsultation:
    """Deterministic (no timestamp, no id), so an unchanged turn replays free; adding either re-bills every turn."""
    readiness = origin_readiness(draft, pipeline)
    response_model = checkin_output(
        f for node in pipeline.chain.prompt_nodes() for f in node.prompt_fields
    )
    slots = response_model.offered
    values = field_values(draft)
    provenance = {key: prov.value for key, prov in draft.field_provenance.items()}
    preview = [dict(row) for row in draft.sample_preview[:_PREVIEW_ROWS]]

    state: dict[str, Any] = {
        "uploaded_columns": list(draft.headers),
        "n_samples": draft.n_samples,
        "sample_rows": preview,
        "current_values": values,
        "provenance": provenance,
        # The step is the screen's: the prompt names the gap, never the fold that closes it.
        "open_gaps": [gap.model_dump(exclude={"step"}) for gap in readiness.gaps],
    }
    answer_space = draft.answer_space()
    if answer_space is not None:
        state["answer_space"] = {
            "target_column": draft.column_ground_truth,
            "labels": list(answer_space),
        }
    criterion = draft_criterion(pipeline.config.scoring)
    if (matcher := MATCHERS.get(criterion.matcher or "")) and matcher.note:
        state["answer_extraction_requirement"] = matcher.note
    user_content = (
        "DRAFT-CAMPAIGN ORIGIN to resolve. Propose values for the OPEN gaps "
        "below, each with cited evidence; write a plain-language "
        "task_description grounded in the sample rows.\n\n"
        f"{json.dumps(state, indent=2, ensure_ascii=False)}"
    )
    if draft.raw_task_description is not None:
        user_content += f"\n\nOperator's stated framing so far:\n{draft.raw_task_description}"

    consultation_instruction = (
        "This is a draft-campaign origin. Fill 'assessment', 'findings', and "
        "'next_action' (and 'recap' only when ready). ALSO decompose the "
        "task_description you propose this turn into "
        + (
            "the Layer 1 prompt fields + task_context — that decomposition seeds the "
            "campaign's starting prompt. "
            if slots
            else "task_context. "
        )
    )
    if "answer_format" in slots:
        consultation_instruction += (
            "Author 'answer_format' so the model emits an EXTRACTABLE answer: when an "
            "'answer_extraction_requirement' appears in the context, the format MUST "
            "satisfy it verbatim. A closed 'answer_space' (when present) is enumerated "
            "into the prompt deterministically, so frame the task around the labels "
            "rather than re-listing them."
        )
    return OriginConsultation(user_content, consultation_instruction.rstrip(), response_model)


async def resolve_origin_turn(
    *,
    stores: Stores,
    draft: DraftFile,
    pipeline: DraftPipeline,
) -> OriginResolutionResult:
    """Keeps no draft: its caller keeps the turn's once the answer is built."""
    consultation = build_origin_consultation(draft, pipeline)

    # No launch admitted this call and no run's book watches it; a check-in has no producer.
    async with paid_call(stores=stores, bucket="turn", provider=checkin_provider()):
        raw, repair_attempts = await run_checkin(
            response_model=consultation.response_model,
            consultation_instruction=consultation.instruction,
            user_content=consultation.user_content,
            context=draft_call_context(stores, draft.draft_id),
        )

    raised = raised_commands(draft, raw)
    updated = _apply_findings(draft, raw, raised, pipeline.chain.prompt_nodes())

    degraded_cause = _degraded_cause(
        output=raw, applied=updated is not draft, repair_attempts=repair_attempts
    )
    updated = updated.patch(resolver_ran=True)

    block = OriginResolution(
        last_resolution=OriginLastResolution(
            assessment=raw.assessment,
            questions=checkin_questions(updated, raw.next_action.questions),
            recap=raw.recap,
        ),
        raised=[
            proposal.command(updated.draft_id)
            for proposal in raised
            if updated.field_provenance.get(proposal.field) is not Provenance.CONFIRMED
        ],
        degraded_cause=degraded_cause,
    )
    return OriginResolutionResult(resolution=block, draft=updated)


def _degraded_cause(*, output: CheckinOutput, applied: bool, repair_attempts: int) -> str | None:
    """Served, never raised: the turn was paid, so its answer is kept with the cause beside it."""
    asking = output.next_action.kind == "ask" and bool(output.next_action.questions)
    if not applied and not asking and not output.recap.strip():
        reasons = ["it produced no usable setup, recap, or question"]
        if repair_attempts > 0:
            reasons.append(
                "the retry after the empty/truncated first response also failed to recover"
            )
        return "the check-in model returned an empty/degraded response — " + "; ".join(reasons)
    if repair_attempts > 0:
        return (
            "the model's first response was empty or truncated and was retried "
            "(~2x cost and latency); the resulting setup may be thin"
        )
    return None


def raised_commands(draft: DraftFile, output: CheckinOutput) -> list[OriginProposal]:
    raised: list[OriginProposal] = []
    for finding in output.findings:
        row = checklist_field(finding.field)
        if row is None or not finding.evidence.strip():
            continue
        # Drop the finding whole: skipping only its tag strands CONFIRMED on an unvouched value.
        settled = draft.field_provenance.get(finding.field) is Provenance.CONFIRMED
        if settled and finding.confidence != "high":
            continue
        coerced = _coerce(row, finding.proposed_value, draft)
        if coerced is None:
            continue
        try:
            patch = EditDraftPatch.model_validate(patch_at(row.patch_path, coerced))
        except ValidationError:
            continue
        raised.append(
            OriginProposal(
                field=finding.field,
                row=row,
                value=coerced,
                patch=patch,
                confidence=finding.confidence,
                evidence=finding.evidence,
            )
        )
    return raised


def _apply_findings(
    draft: DraftFile,
    output: CheckinOutput,
    raised: list[OriginProposal],
    prompt_nodes: Sequence[PipelineNode],
) -> DraftFile:
    values: dict[str, Any] = {}
    provenance: dict[str, Provenance] = {}
    for proposal in raised:
        if proposal.patch.config is not None:
            # A node's model moves spend: it lands only as its raised edit, through the patch's
            # pricing and free-tier refusals (`draft_patch.py::plan_draft_patch`).
            continue
        values.update(proposal.patch.model_dump(exclude_unset=True))
        provenance[proposal.field] = (
            Provenance.CONFIRMED if proposal.confidence == "high" else Provenance.PROPOSED
        )
    written = {
        node.name: fields
        for node in prompt_nodes
        if (
            fields := {
                name: text
                for name in node.prompt_fields
                if name in PROMPT_FIELDS and (text := getattr(output, name)).strip()
            }
        )
    }
    if written:
        values["origin_prompts"] = {
            **draft.origin_prompts,
            **{step: {**draft.origin_prompts.get(step, {}), **f} for step, f in written.items()},
        }
        provenance["origin_prompts"] = Provenance.CONFIRMED
    decomposed = output.task_context.model_dump()
    if any(str(value).strip() for value in decomposed.values()):
        values["decomposed_task_context"] = decomposed
    if not values:
        return draft
    return draft.apply_resolution(values=values, provenance=provenance)


def _coerce(row: ChecklistField, proposed: str, draft: DraftFile) -> Any | None:
    proposed = proposed.strip()
    if not proposed or (row.of_headers and proposed not in draft.headers):
        return None
    return proposed
