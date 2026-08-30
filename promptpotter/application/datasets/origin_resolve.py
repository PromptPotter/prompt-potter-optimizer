"""One resolver turn against a ``DraftCampaign`` — the proposer half of the proposer/gate split,
running the same ``checkin`` node CLI ``new`` does. ``origin_readiness`` decides completeness."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

from promptpotter.application.datasets.draft_campaign import DraftCampaign
from promptpotter.application.datasets.origin_readiness import (
    field_values,
    origin_readiness,
    resolution_block,
)
from promptpotter.application.jobs.launcher.checkin import save_checkin_draft
from promptpotter.application.jobs.quota import admit_llm_turn
from promptpotter.application.optimization.dispatch.llm_call.call import (
    LLMCallContext,
    run_optimizer_node,
)
from promptpotter.application.optimization.dispatch.schemas import CheckinOutput
from promptpotter.application.optimization.task_context import checkin_call_context
from promptpotter.application.scoring.formula.matchers import extraction_note_for_scoring
from promptpotter.config.settings import PROMPT_STRING_FIELDS
from promptpotter.domain.origin_provenance import Provenance
from promptpotter.infrastructure.llm.telemetry import reset_cycle_ledger, set_cycle_ledger
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.infrastructure.tracing.bridge import observed_node

logger = logging.getLogger(__name__)

# Checklist field id → the ``edit-draft-campaign`` patch key that sets it, which is
# also the ``DraftCampaign`` attribute name. ONLY the genuinely-variable fields: the
# two column picks and the task framing. Config (connector / scoring / optimizer LLM
# / max_rounds) is NOT proposed — those are defaults the operator edits in the
# optional Advanced block, not facts the LLM infers from the data.
#
# Every finding is therefore expressible as a command, which is why the resolver
# never has to name one: it proposes a field, code derives the button. Pinned by an
# import-time assert in the commands router against ``_EditDraftPatch``.
FINDING_PATCH_KEYS: dict[str, str] = {
    "column.query": "column_query",
    "column.ground_truth": "column_ground_truth",
    "task_description": "raw_task_description",
}

_PREVIEW_ROWS = 10


@dataclass(frozen=True, slots=True)
class RaisedCommand:
    """One proposal, already shaped as the command that would apply it. The operator clicks it; the
    assistant never fires it. ``confidence`` is the CALLER's apply-inline policy, not the model's."""

    field: str
    patch_key: str
    value: Any
    confidence: str
    evidence: str

    def to_wire(self, draft_id: str) -> dict[str, Any]:
        # No `confidence`: a high-confidence proposal is applied CONFIRMED inside the turn
        # and filtered out of `raised`, so every proposal that reaches the wire is a
        # low-confidence one — the field would be a constant.
        return {
            "kind": "edit-draft-campaign",
            "payload": {"draft_id": draft_id, "patch": {self.patch_key: self.value}},
            "evidence": self.evidence,
        }


@dataclass(frozen=True, slots=True)
class OriginResolutionResult:
    resolution: dict[str, Any]
    draft: DraftCampaign


def build_origin_consultation(draft: DraftCampaign, message: str | None = None) -> tuple[str, str]:
    """Deterministic origin-context message + the origin-mode instruction; no LLM call. ``message`` is
    the operator's turn, kept apart from the ``raw_task_description`` the resolver proposes."""
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
        "open_gaps": [gap.to_wire() for gap in readiness.gaps],
    }
    # When the target column reads as a closed label set, hand the proposer the
    # FULL enumerated answer space (computed over the whole upload at ingest, not
    # the truncated preview) so it stops inferring a partial taxonomy from the
    # few visible rows and collapsing it to "(e.g., X)".
    answer_space = draft.answer_space()
    if answer_space is not None:
        state["answer_space"] = {
            "target_column": draft.column_ground_truth,
            "labels": list(answer_space),
        }
    # The answer-extraction contract is decided by the SCORING MATCHER (it's the
    # matcher that reads a label out of the raw output), so it rides the resolver's
    # raw context keyed off the draft's scorer — not the backend, which passes the
    # raw answer through. The resolver folds it into `answer_format`, fixing the root
    # (the resolver never knew the requirement) instead of overwriting downstream.
    # Empty for a compare-raw scorer → the resolver authors a plain format.
    extraction_note = extraction_note_for_scoring(draft.scoring_composite)
    if extraction_note:
        state["answer_extraction_requirement"] = extraction_note
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


def _checkin_call_context(stores: Stores, campaign_id: str) -> LLMCallContext:
    """The turn's audit home — the check-in campaign's own cycle ledger. ``draft_id`` IS the
    ``campaign_id`` (re-keyed at ``create_checkin_campaign``); both modes bill through the one call."""
    campaign = stores.campaigns.load_campaign(campaign_id)
    if campaign is None:
        raise ValueError(f"check-in campaign {campaign_id!r} not found — cannot resolve its origin")
    return checkin_call_context(stores, campaign.root_hop)


async def resolve_origin_turn(
    *,
    stores: Stores,
    draft: DraftCampaign,
    message: str | None = None,
) -> OriginResolutionResult:
    """Persists the mutated draft + resolution block under the draft's ``draft_id``."""
    # The one optimizer call reachable before a campaign exists, so no launch admission has run
    # and no `BudgetGate` is watching — an exhausted account would otherwise keep spending the
    # host's key here indefinitely. Offloaded: admission globs every cycle ledger.
    await asyncio.to_thread(admit_llm_turn, stores=stores)
    user_content, consultation_instruction = build_origin_consultation(draft, message)

    # Bound here as well as in `CommandDispatcher` so the CLI path (`new <file>`,
    # no dispatcher) files its spend on the same cycle the web path does.
    # The consultation is deterministic (no timestamps, no ids), so an unchanged turn
    # replays free off `optimizer_reuse/`; a schema or optimizer prompt edit changes
    # `hash_call` and correctly misses.
    context = _checkin_call_context(stores, draft.draft_id)
    token = set_cycle_ledger(context.ledger)
    try:
        async with observed_node(
            "origin_checkin",
            "llm/optimizer",
            obs=None,
            campaign_id=draft.draft_id,
            round_num=0,
        ):
            raw, _prompt, repair_attempts = await run_optimizer_node(
                template_name="checkin",
                prompt_vars={"consultation_instruction": consultation_instruction},
                user_content=user_content,
                context=context,
            )
    finally:
        reset_cycle_ledger(token)

    assert isinstance(raw, CheckinOutput), (
        f"checkin must return CheckinOutput, got {type(raw).__name__}"
    )

    raised = raised_commands(draft, raw)
    updated = _apply_findings(draft, raw, raised)

    # Degradation gate. The resolver LLM can return a structurally-valid but content-empty
    # CheckinOutput (every field defaults ``""``), which ``_apply_findings`` silently no-ops
    # on (``updated is draft``) — a thin origin the draft must carry a cause for.
    degraded_cause = _degraded_cause(
        output=raw, applied=updated is not draft, repair_attempts=repair_attempts
    )

    block = resolution_block(updated)
    block["last_resolution"] = _resolution_wire(raw)
    # Proposals the operator may still click. High-confidence ones already landed
    # CONFIRMED inside this operator-invoked turn, so only the unsettled remain
    # actionable — the assistant offers, it never fires.
    block["raised"] = [
        proposal.to_wire(updated.draft_id)
        for proposal in raised
        if updated.field_provenance.get(proposal.field) is not Provenance.CONFIRMED
    ]
    if degraded_cause is not None:
        block["degraded_cause"] = degraded_cause
    save_checkin_draft(stores, updated, resolution=block)

    return OriginResolutionResult(resolution=block, draft=updated)


def _degraded_cause(*, output: CheckinOutput, applied: bool, repair_attempts: int) -> str | None:
    """Why this turn came back thin, or ``None`` where it did not — the check-in panel's warning
    text.

    A turn that produced nothing usable RAISES rather than returning one; the route's catch turns
    that into the 502 the webapp shows. So no unusable turn ever reaches a client, which is why the
    served fact is a cause and not a grade — every value a client can see means the same thing.
    ``DegradationHealth`` grades SAMPLES and shares nothing with this but the word."""
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


def raised_commands(draft: DraftCampaign, output: CheckinOutput) -> list[RaisedCommand]:
    """The turn's proposals as clickable commands, and the single admission gate: patchable field, cited
    evidence, coercible type, no downgrade. Button surface and inline apply read this one list."""
    raised: list[RaisedCommand] = []
    for finding in output.findings:
        patch_key = FINDING_PATCH_KEYS.get(finding.field)
        if patch_key is None or not finding.evidence.strip():
            continue
        # Provenance ratchets: a settled field is not reopened by a low-confidence
        # re-proposal. Drop the finding whole — skipping only its tag would strand a
        # CONFIRMED marker on a value nobody vouched for.
        settled = draft.field_provenance.get(finding.field) is Provenance.CONFIRMED
        if settled and finding.confidence != "high":
            continue
        coerced = _coerce(finding.field, finding.proposed_value, draft)
        if coerced is None:
            continue
        raised.append(
            RaisedCommand(
                field=finding.field,
                patch_key=patch_key,
                value=coerced,
                confidence=finding.confidence,
                evidence=finding.evidence,
            )
        )
    return raised


def _apply_findings(
    draft: DraftCampaign, output: CheckinOutput, raised: list[RaisedCommand]
) -> DraftCampaign:
    values: dict[str, Any] = {}
    provenance: dict[str, Provenance] = {}
    for proposal in raised:
        values[proposal.patch_key] = proposal.value
        provenance[proposal.field] = (
            Provenance.CONFIRMED if proposal.confidence == "high" else Provenance.PROPOSED
        )
    # The same check-in node returns the decomposition half (the six Layer-1
    # prompt strings) alongside the origin findings — see the CheckinOutput
    # two-mode contract. Capture it as the draft's starting prompt; the operator
    # edits it in the review step and it's written to prompts/default.yaml at
    # mint. A turn that only authored the prompt (no findings) still applies.
    prompt_fields = {
        name: getattr(output, name)
        for name in PROMPT_STRING_FIELDS
        if str(getattr(output, name)).strip()
    }
    if prompt_fields:
        values["origin_prompt_fields"] = {**draft.origin_prompt_fields, **prompt_fields}
        provenance["origin_prompt_fields"] = Provenance.CONFIRMED
    # The check-in also decomposes the task into a 7-field ``task_context`` domain
    # framing. Capture it onto the draft (it rides commit → ``task_context.yaml``)
    # so the run reads it instead of recomputing via a second LLM call at run-start.
    decomposed = output.task_context.model_dump()
    if any(str(value).strip() for value in decomposed.values()):
        values["decomposed_task_context"] = decomposed
    if not values:
        return draft
    # The resolver authors the answer_format PROSE (the scorer's extraction
    # instruction — the bold/box it was handed in context); the closed answer-space
    # ENUMERATION is appended deterministically downstream (`committed_prompt_fields`
    # → `closed_answer_format`) because the LLM reliably drops labels from a many-way
    # set. Leaving the prose empty blocks nothing — the optimizer evolves it, and the
    # round-0 health grade is the empirical backstop.
    return draft.apply_resolution(values=values, provenance=provenance)


def _coerce(field_key: str, proposed: str, draft: DraftCampaign) -> Any | None:
    proposed = proposed.strip()
    if not proposed:
        return None
    if field_key in ("column.query", "column.ground_truth"):
        return proposed if proposed in draft.headers else None
    return proposed


def _resolution_wire(output: CheckinOutput) -> dict[str, Any]:
    """The turn's output for ``cache.json``. Findings ride ``block["raised"]`` as clickable proposals,
    so mirroring them here would serve the same fact twice."""
    return {
        "assessment": output.assessment,
        "next_action": {
            "kind": output.next_action.kind,
            "questions": [
                {"field": q.field, "prompt": q.prompt, "options": list(q.options)}
                for q in output.next_action.questions
            ],
        },
        "recap": output.recap,
    }


__all__ = ["resolve_origin_turn"]
