from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from promptpotter.application.bench.task_context import (
    CheckinOutput,
    draft_call_context,
    run_checkin,
)
from promptpotter.application.commands.payloads import EditDraftCampaignPayload
from promptpotter.application.datasets.draft_campaign import DraftCampaign, EditDraftPatch
from promptpotter.application.datasets.origin_readiness import (
    OriginLastResolution,
    OriginResolution,
    RaisedCommand,
    field_values,
    origin_readiness,
    resolution_block,
    save_checkin_draft,
)
from promptpotter.application.jobs.quota import paid_call
from promptpotter.application.scoring.formula.matchers import MATCHERS
from promptpotter.domain.opt_search_point import PROMPT_FIELDS
from promptpotter.domain.origin_provenance import Provenance
from promptpotter.infrastructure.llm.telemetry import reset_cycle_ledger, set_cycle_ledger
from promptpotter.infrastructure.store.stores import Stores

logger = logging.getLogger(__name__)

# Each value is both the ``EditDraftPatch`` key and the ``DraftCampaign`` attribute name.
FINDING_PATCH_KEYS: dict[str, str] = {
    "column.query": "column_query",
    "column.ground_truth": "column_ground_truth",
    "task_description": "raw_task_description",
}

_PREVIEW_ROWS = 10


@dataclass(frozen=True, slots=True)
class OriginProposal:
    field: str
    patch_key: str
    value: Any
    confidence: str
    evidence: str

    def command(self, draft_id: str) -> RaisedCommand:
        patch = EditDraftPatch.model_validate({self.patch_key: self.value})
        return RaisedCommand(
            payload=EditDraftCampaignPayload(draft_id=draft_id, patch=patch),
            evidence=self.evidence,
        )


@dataclass(frozen=True, slots=True)
class OriginResolutionResult:
    resolution: OriginResolution
    draft: DraftCampaign


def build_origin_consultation(draft: DraftCampaign, message: str | None = None) -> tuple[str, str]:
    """Deterministic (no timestamp, no id), so an unchanged turn replays free; adding either re-bills every turn."""
    readiness = origin_readiness(draft)
    values = field_values(draft)
    provenance = {key: prov.value for key, prov in draft.field_provenance.items()}
    preview = [dict(row) for row in draft.sample_preview[:_PREVIEW_ROWS]]

    state: dict[str, Any] = {
        "uploaded_columns": list(draft.headers),
        "n_samples": draft.n_samples,
        "sample_rows": preview,
        "current_values": values,
        "provenance": provenance,
        "open_gaps": [gap.model_dump() for gap in readiness.gaps],
    }
    answer_space = draft.answer_space()
    if answer_space is not None:
        state["answer_space"] = {
            "target_column": draft.column_ground_truth,
            "labels": list(answer_space),
        }
    if (matcher := MATCHERS.get(draft.scoring_matcher)) and matcher.note:
        state["answer_extraction_requirement"] = matcher.note
    user_content = (
        "DRAFT-CAMPAIGN ORIGIN to resolve. Propose values for the OPEN gaps "
        "below, each with cited evidence; write a plain-language "
        "task_description grounded in the sample rows.\n\n"
        f"{json.dumps(state, indent=2, ensure_ascii=False)}"
    )
    if draft.raw_task_description:
        user_content += f"\n\nOperator's stated framing so far:\n{draft.raw_task_description}"
    if message and message.strip():
        user_content += (
            "\n\nOperator's message this turn (takes precedence over the framing "
            f"above where they conflict):\n{message.strip()}"
        )

    consultation_instruction = (
        "This is a draft-campaign origin. Fill 'assessment', 'findings', and "
        "'next_action' (and 'recap' only when ready). ALSO decompose the "
        "task_description you propose this turn into the Layer 1 prompt fields + "
        "task_context — that decomposition seeds the campaign's starting prompt. "
        "Author 'answer_format' so the model emits an EXTRACTABLE answer: when an "
        "'answer_extraction_requirement' appears in the context, the format MUST "
        "satisfy it verbatim. A closed 'answer_space' (when present) is enumerated "
        "into the prompt deterministically, so frame the task around the labels "
        "rather than re-listing them."
    )
    return user_content, consultation_instruction


async def resolve_origin_turn(
    *,
    stores: Stores,
    draft: DraftCampaign,
    message: str | None = None,
) -> OriginResolutionResult:
    user_content, consultation_instruction = build_origin_consultation(draft, message)

    # Bound here as well as in `CommandDispatcher`: the CLI path (`new <file>`) has no dispatcher.
    context = draft_call_context(stores, draft.draft_id)
    token = set_cycle_ledger(context.ledger)
    try:
        # No launch admitted this call and no run's book watches it; a check-in has no producer.
        async with paid_call(stores=stores, bucket="turn"):
            raw, repair_attempts = await run_checkin(
                consultation_instruction=consultation_instruction,
                user_content=user_content,
                context=context,
            )
    finally:
        reset_cycle_ledger(token)

    raised = raised_commands(draft, raw)
    updated = _apply_findings(draft, raw, raised)

    degraded_cause = _degraded_cause(
        output=raw, applied=updated is not draft, repair_attempts=repair_attempts
    )

    block = resolution_block(updated).model_copy(
        update={
            "last_resolution": OriginLastResolution(
                assessment=raw.assessment, next_action=raw.next_action, recap=raw.recap
            ),
            "raised": [
                proposal.command(updated.draft_id)
                for proposal in raised
                if updated.field_provenance.get(proposal.field) is not Provenance.CONFIRMED
            ],
            "degraded_cause": degraded_cause,
        }
    )
    save_checkin_draft(stores, updated, resolution=block)

    return OriginResolutionResult(resolution=block, draft=updated)


def _degraded_cause(*, output: CheckinOutput, applied: bool, repair_attempts: int) -> str | None:
    asking = output.next_action.kind == "ask" and bool(output.next_action.questions)
    if not applied and not asking and not output.recap.strip():
        reasons = ["it produced no usable setup, recap, or question"]
        if repair_attempts > 0:
            reasons.append(
                "the retry after the empty/truncated first response also failed to recover"
            )
        raise RuntimeError(
            "the check-in model returned an empty/degraded response — " + "; ".join(reasons)
        )
    if repair_attempts > 0:
        return (
            "the model's first response was empty or truncated and was retried "
            "(~2x cost and latency); the resulting setup may be thin"
        )
    return None


def raised_commands(draft: DraftCampaign, output: CheckinOutput) -> list[OriginProposal]:
    raised: list[OriginProposal] = []
    for finding in output.findings:
        patch_key = FINDING_PATCH_KEYS.get(finding.field)
        if patch_key is None or not finding.evidence.strip():
            continue
        # Drop the finding whole: skipping only its tag strands CONFIRMED on an unvouched value.
        settled = draft.field_provenance.get(finding.field) is Provenance.CONFIRMED
        if settled and finding.confidence != "high":
            continue
        coerced = _coerce(finding.field, finding.proposed_value, draft)
        if coerced is None:
            continue
        try:
            EditDraftPatch.model_validate({patch_key: coerced})
        except ValidationError:
            continue
        raised.append(
            OriginProposal(
                field=finding.field,
                patch_key=patch_key,
                value=coerced,
                confidence=finding.confidence,
                evidence=finding.evidence,
            )
        )
    return raised


def _apply_findings(
    draft: DraftCampaign, output: CheckinOutput, raised: list[OriginProposal]
) -> DraftCampaign:
    values: dict[str, Any] = {}
    provenance: dict[str, Provenance] = {}
    for proposal in raised:
        values[proposal.patch_key] = proposal.value
        provenance[proposal.field] = (
            Provenance.CONFIRMED if proposal.confidence == "high" else Provenance.PROPOSED
        )
    written = {
        node.name: fields
        for node in draft.prompt_nodes()
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


def _coerce(field_key: str, proposed: str, draft: DraftCampaign) -> Any | None:
    proposed = proposed.strip()
    if not proposed:
        return None
    if field_key in ("column.query", "column.ground_truth"):
        return proposed if proposed in draft.headers else None
    return proposed
