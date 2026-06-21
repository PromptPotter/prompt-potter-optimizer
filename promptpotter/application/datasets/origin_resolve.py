"""Origin-resolution loop — one resolver turn against a ``DraftCampaign``.

The proposer half of the proposer/gate split (spec:
``docs/specs/roadmap.md``). One turn:

1. Assemble a deterministic origin-context message from the draft + open gaps +
   the operator's latest message (``build_origin_consultation`` — no LLM
   summarisation of its own data).
2. Run the origin-aware ``checkin`` node (``checkin/2``) — the same node CLI
   ``new`` uses for task decomposition, reused here per the operator's steer
   rather than a parallel ``origin_resolve`` node. Wrapped in ``observed_node``;
   token/cost ride the **tenant workspace ledger**
   (``projects/{tenant}/.workspace/events.jsonl``), matching how
   ``register-backend`` / ``sync-backend-experiments`` ride it.
3. Apply the resolver's evidence-cited findings: ``confidence=="high"``
   auto-confirms (``CONFIRMED``); ``"low"`` lands the field ``PROPOSED`` and
   waits for an operator click. Findings citing no evidence are rejected.
4. Re-run the deterministic ``origin_readiness`` checklist — the checklist, not
   the resolver, decides completeness. Persist provenance + the last resolution
   to the draft ``cache.json`` (AI-readable on disk).

One turn per call: the operator/UI drives subsequent turns, so the loop is
bounded by interaction rather than an internal auto-spin.
"""

from __future__ import annotations

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
from promptpotter.application.optimization.dispatch.llm_call import (
    LLMCallContext,
    run_optimizer_node,
)
from promptpotter.application.optimization.dispatch.schemas import CheckinOutput
from promptpotter.application.scoring.formula.matchers import extraction_note_for_scoring
from promptpotter.config.settings import PROMPT_STRING_FIELDS
from promptpotter.domain.cycle_paths import WorkspaceDir
from promptpotter.domain.origin_provenance import Provenance
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.llm.models import reset_cycle_ledger, set_cycle_ledger
from promptpotter.infrastructure.store import Stores
from promptpotter.infrastructure.tracing import observed_node

logger = logging.getLogger(__name__)

# Checklist field id → DraftCampaign attribute the resolver may set. ONLY the
# genuinely-variable fields: the two column picks and the task framing. Config
# (connector / scoring / optimizer LLM / max_rounds) is NOT proposed — those are
# defaults the operator edits in the optional Advanced block, not facts the LLM
# infers from the data. The proposer also authors the Layer-1 prompt (incl.
# answer_format, enumerating the answer space + honoring the scorer's extraction
# requirement); it reads columns + frames the task, it does not negotiate config.
_FINDING_SETTERS: dict[str, str] = {
    "column.query": "column_query",
    "column.ground_truth": "column_ground_truth",
    "task_description": "raw_task_description",
}

_PREVIEW_ROWS = 10


@dataclass(frozen=True, slots=True)
class OriginResolutionResult:
    """One turn's outcome: the resolver output (wire dict) + the post-apply draft."""

    resolution: dict[str, Any]
    draft: DraftCampaign


def build_origin_consultation(draft: DraftCampaign) -> tuple[str, str]:
    """Deterministic origin-context message + the origin-mode instruction.

    Returns ``(user_content, consultation_instruction)``. Pure formatting — no
    LLM call, no summarisation; the resolver reads the raw facts and proposes.
    """
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
    operator_message = draft.raw_task_description  # latest operator framing, if any
    user_content = (
        "DRAFT-CAMPAIGN ORIGIN to resolve. Propose values for the OPEN gaps "
        "below, each with cited evidence; write a plain-language "
        "task_description grounded in the sample rows.\n\n"
        f"{json.dumps(state, indent=2, ensure_ascii=False)}"
    )
    if operator_message:
        user_content += f"\n\nOperator's stated framing so far:\n{operator_message}"

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
) -> OriginResolutionResult:
    """Run one resolver turn, apply findings, re-gate, persist. Returns the
    resolution + post-apply draft.

    Persists the mutated draft + resolution block to the check-in store under the
    draft's ``draft_id`` (which IS the owning ``campaign_id``)."""
    user_content, consultation_instruction = build_origin_consultation(draft)

    # Token/cost ride the tenant workspace ledger (no cycle exists pre-mint).
    ledger = CycleEventLog.open_workspace(WorkspaceDir(stores.base_dir))
    token = set_cycle_ledger(ledger)
    try:
        async with observed_node(
            "origin_checkin",
            "llm/meta",
            obs=None,
            campaign_id=draft.draft_id,
            round_num=0,
        ):
            raw, _prompt = await run_optimizer_node(
                template_name="checkin",
                prompt_vars={"consultation_instruction": consultation_instruction},
                user_content=user_content,
                context=LLMCallContext(ledger=ledger, round_num=0),
            )
    finally:
        reset_cycle_ledger(token)

    assert isinstance(raw, CheckinOutput), (
        f"checkin must return CheckinOutput, got {type(raw).__name__}"
    )

    updated = _apply_findings(draft, raw)
    stores.checkin.write_draft(updated.draft_id, updated.to_disk())

    block = resolution_block(updated)
    block["last_resolution"] = _resolution_wire(raw)
    stores.checkin.write_resolution(updated.draft_id, block)

    return OriginResolutionResult(resolution=block, draft=updated)


def _apply_findings(draft: DraftCampaign, output: CheckinOutput) -> DraftCampaign:
    """Apply evidence-cited findings to the draft. High-confidence → CONFIRMED,
    low → PROPOSED (waits for an operator confirmation via ``edit-draft-campaign``).
    Evidence-less or unmappable findings are dropped."""
    values: dict[str, Any] = {}
    provenance: dict[str, Provenance] = {}
    for finding in output.findings:
        attr = _FINDING_SETTERS.get(finding.field)
        if attr is None or not finding.evidence.strip():
            continue
        coerced = _coerce(finding.field, finding.proposed_value, draft)
        if coerced is None:
            continue
        values[attr] = coerced
        provenance[finding.field] = (
            Provenance.CONFIRMED if finding.confidence == "high" else Provenance.PROPOSED
        )
    # The same check-in node returns the decomposition half (the six Layer-1
    # prompt strings) alongside the origin findings — see the CheckinOutput
    # two-mode contract. Capture it as the draft's starting prompt; the operator
    # edits it in the review step and it's written to prompts/default.json at
    # mint. A turn that only authored the prompt (no findings) still applies.
    prompt_fields = {
        name: getattr(output, name)
        for name in PROMPT_STRING_FIELDS
        if str(getattr(output, name, "")).strip()
    }
    if prompt_fields:
        values["origin_prompt_fields"] = {**draft.origin_prompt_fields, **prompt_fields}
        provenance["origin_prompt_fields"] = Provenance.CONFIRMED
    # The check-in also decomposes the task into a 7-field ``task_context`` domain
    # framing. Capture it onto the draft (it rides commit → ``task_context.json``)
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
    # set. `_check_commit_format` nudges when the prose is left empty; the round-0
    # health grade is the empirical backstop.
    return draft.apply_resolution(values=values, provenance=provenance)


def _coerce(field_key: str, proposed: str, draft: DraftCampaign) -> Any | None:
    """Coerce a string ``proposed_value`` to the draft attribute's type; return
    ``None`` to drop a finding that can't be applied safely."""
    proposed = proposed.strip()
    if not proposed:
        return None
    if field_key in ("column.query", "column.ground_truth"):
        return proposed if proposed in draft.headers else None
    return proposed


def _resolution_wire(output: CheckinOutput) -> dict[str, Any]:
    """The resolver turn's output, persisted to ``cache.json`` for the panel + AI."""
    return {
        "assessment": output.assessment,
        "findings": [
            {
                "field": f.field,
                "proposed_value": f.proposed_value,
                "confidence": f.confidence,
                "evidence": f.evidence,
            }
            for f in output.findings
        ],
        "next_action": {
            "kind": output.next_action.kind,
            "questions": [
                {"field": q.field, "prompt": q.prompt, "options": list(q.options)}
                for q in output.next_action.questions
            ],
        },
        "recap": output.recap,
    }


__all__ = [
    "OriginResolutionResult",
    "build_origin_consultation",
    "resolve_origin_turn",
]
