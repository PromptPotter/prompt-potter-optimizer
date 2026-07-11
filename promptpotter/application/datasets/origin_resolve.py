"""Origin-resolution loop — one resolver turn against a ``DraftCampaign``.

The proposer half of the proposer/gate split (spec:
``docs/specs/roadmap.md``). One turn:

1. Assemble a deterministic origin-context message from the draft + open gaps +
   the operator's latest message (``build_origin_consultation`` — no LLM
   summarisation of its own data).
2. Run the origin-aware ``checkin`` node (``checkin/2``) — the same node CLI
   ``new`` uses for task decomposition, reused here per the operator's steer
   rather than a parallel ``origin_resolve`` node. Wrapped in ``observed_node``;
   token/cost ride the **check-in cycle's own ledger** (``cycle_chk_*``, minted
   by ``mint_checkin_skeleton`` at the first ingest action and retained across
   the flip to ``active``), beside the ``CommandRecord`` the dispatcher writes
   for the turn — so an origin's authoring spend belongs to the campaign that
   incurred it.
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
from promptpotter.application.jobs.launcher import save_checkin_draft
from promptpotter.application.optimization.dispatch.llm_call import (
    LLMCallContext,
    run_optimizer_node,
)
from promptpotter.application.optimization.dispatch.schemas import CheckinOutput
from promptpotter.application.optimization.task_context import checkin_call_context
from promptpotter.application.scoring.formula.matchers import extraction_note_for_scoring
from promptpotter.config.settings import PROMPT_STRING_FIELDS
from promptpotter.domain.origin_provenance import Provenance
from promptpotter.infrastructure.llm.models import reset_cycle_ledger, set_cycle_ledger
from promptpotter.infrastructure.store import Stores
from promptpotter.infrastructure.tracing import observed_node

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
    """One proposal, already shaped as the command that would apply it.

    The operator clicks it; the assistant never fires it. ``confidence`` decides
    whether the operator-invoked resolver turn applies it inline (``high``) or
    leaves it PROPOSED for a click (``low``) — a policy of the caller, not of the
    model, which only ever emits a field and a value.
    """

    field: str
    patch_key: str
    value: Any
    confidence: str
    evidence: str

    def to_wire(self, draft_id: str) -> dict[str, Any]:
        return {
            "kind": "edit-draft-campaign",
            "payload": {"draft_id": draft_id, "patch": {self.patch_key: self.value}},
            "confidence": self.confidence,
            "evidence": self.evidence,
        }


@dataclass(frozen=True, slots=True)
class OriginResolutionResult:
    """One turn's outcome: the resolver output (wire dict) + the post-apply draft."""

    resolution: dict[str, Any]
    draft: DraftCampaign


def build_origin_consultation(draft: DraftCampaign, message: str | None = None) -> tuple[str, str]:
    """Deterministic origin-context message + the origin-mode instruction.

    Returns ``(user_content, consultation_instruction)``. Pure formatting — no
    LLM call, no summarisation; the resolver reads the raw facts and proposes.

    ``message`` is the operator's turn, kept separate from the draft's
    ``raw_task_description`` (the task-framing field the resolver proposes and the
    operator confirms). Folding a correction into the framing was the only way to
    be heard before, which meant "the target is the `answer` column" had to be
    written as a task description.
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
    """The resolver turn's audit home — the check-in campaign's own cycle ledger.

    ``draft_id`` IS the ``campaign_id`` (re-keyed at ``create_checkin_campaign``),
    and the campaign's ``root_cycle_id`` is the ``cycle_chk_*`` minted alongside it.
    Both check-in modes bill through the one :func:`checkin_call_context`.
    """
    campaign = stores.campaigns.load_campaign(campaign_id)
    if campaign is None:
        raise ValueError(f"check-in campaign {campaign_id!r} not found — cannot resolve its origin")
    return checkin_call_context(stores, campaign_id, campaign.root_cycle_id)


async def resolve_origin_turn(
    *,
    stores: Stores,
    draft: DraftCampaign,
    message: str | None = None,
) -> OriginResolutionResult:
    """Run one resolver turn, apply findings, re-gate, persist. Returns the
    resolution + post-apply draft.

    Persists the mutated draft + resolution block to the check-in store under the
    draft's ``draft_id`` (which IS the owning ``campaign_id``)."""
    user_content, consultation_instruction = build_origin_consultation(draft, message)

    # Bound here as well as in `CommandDispatcher` so the CLI path (`new <file>`,
    # no dispatcher) files its spend on the same cycle the web path does.
    # The consultation is deterministic (no timestamps, no ids), so an unchanged turn
    # replays free off `archive/optimizer_calls/`; a schema or meta-prompt edit changes
    # `hash_call` and correctly misses.
    context = _checkin_call_context(stores, draft.draft_id)
    token = set_cycle_ledger(context.ledger)
    try:
        async with observed_node(
            "origin_checkin",
            "llm/meta",
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

    # Degradation gate. The resolver LLM can return a structurally-valid but
    # content-empty CheckinOutput (every field defaults ``""``), which
    # ``_apply_findings`` silently no-ops on (``updated is draft``) — historically
    # minting a thin origin while the only trace was a stdout warning. Grade the
    # turn so a degraded/empty resolution is LOUD, not mute. ``critical`` (nothing
    # usable produced) raises → the route's existing catch surfaces it as a 502 the
    # webapp shows. ``degraded`` (e.g. a paid repair retry — the provider's first
    # response was empty/truncated, ~2x cost+latency) rides the resolution block as
    # ``degraded`` and the check-in panel renders it as a warning + re-run option.
    degraded = _grade_resolution(
        output=raw, applied=updated is not draft, repair_attempts=repair_attempts
    )
    if degraded is not None and degraded["grade"] == "critical":
        raise RuntimeError(
            "the check-in model returned an empty/degraded response — "
            + "; ".join(degraded["reasons"])
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
    if degraded is not None:
        block["degraded"] = degraded
    save_checkin_draft(stores, updated, resolution=block)

    return OriginResolutionResult(resolution=block, draft=updated)


def _grade_resolution(
    *, output: CheckinOutput, applied: bool, repair_attempts: int
) -> dict[str, Any] | None:
    """Grade one resolver turn for degradation. ``None`` when healthy, else
    ``{grade, reasons, repair_attempts}`` with ``grade ∈ {"degraded", "critical"}``.

    Mirrors the ``healthy|degraded|critical`` vocabulary of
    :class:`~promptpotter.domain.results_health.DegradationHealth` (which is
    sample-results-shaped, so not reusable on a ``CheckinOutput``).

    * **critical** — the turn produced nothing usable: no field applied, no
      operator question asked, and an empty recap. This is the empty/truncated
      provider response the apply loop silently dropped.
    * **degraded** — the turn carried something but a full schema-repair retry was
      paid (``repair_attempts > 0``): the provider's first response was
      empty/truncated, so the cost doubled and the recovered output may be thin.
      Surfaced even when the retry recovered, so the operator sees the ~2x hit.
    """
    asking = output.next_action.kind == "ask" and bool(output.next_action.questions)
    if not applied and not asking and not output.recap.strip():
        reasons = ["the check-in produced no usable setup, recap, or question"]
        if repair_attempts > 0:
            reasons.append(
                "the retry after the empty/truncated first response also failed to recover"
            )
        return {"grade": "critical", "reasons": reasons, "repair_attempts": repair_attempts}
    if repair_attempts > 0:
        return {
            "grade": "degraded",
            "reasons": [
                "the model's first response was empty or truncated and was retried "
                "(~2x cost and latency); the resulting setup may be thin"
            ],
            "repair_attempts": repair_attempts,
        }
    return None


def raised_commands(draft: DraftCampaign, output: CheckinOutput) -> list[RaisedCommand]:
    """The turn's proposals, as commands the operator could click.

    The single admission gate: a finding must map to a patchable field, cite
    evidence, coerce to the attribute's type, and not downgrade a settled field.
    Both the button surface and the inline apply read this one list, so what the
    operator sees offered is exactly what a click would do.
    """
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
    """Apply the turn's proposals to the draft. High-confidence → CONFIRMED, low →
    PROPOSED (the value lands, the click confirms it)."""
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
    # edits it in the review step and it's written to prompts/default.json at
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
