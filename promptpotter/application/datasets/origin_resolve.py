"""Origin-resolution loop — one resolver turn against a ``DraftCampaign``.

The proposer half of the proposer/gate split (spec:
``docs/specs/m10-origin-resolution-checkin.md``). One turn:

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

from promptpotter.application.datasets.draft_campaign import (
    DraftCampaign,
    DraftCampaignRegistry,
)
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
from promptpotter.config.settings import PROMPT_STRING_FIELDS
from promptpotter.domain.cycle_paths import WorkspaceDir
from promptpotter.domain.origin_provenance import Provenance, ProvenanceSource
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.llm import get_llm_client
from promptpotter.infrastructure.llm.models import reset_cycle_ledger, set_cycle_ledger
from promptpotter.infrastructure.store import Stores
from promptpotter.infrastructure.tracing import observed_node

logger = logging.getLogger(__name__)

# Checklist field id → DraftCampaign attribute the resolver may set. Fields not
# here (backend.node_config) are not string-applicable from a finding this slice
# and stay on their template default (operator edits via edit-draft-campaign).
_FINDING_SETTERS: dict[str, str] = {
    "column.query": "column_query",
    "column.ground_truth": "column_ground_truth",
    "task_description": "task_description",
    "connector": "connector",
    "scoring_composite": "scoring_composite",
    "optimizer.provider": "optimizer_provider",
    "optimizer.model": "optimizer_model",
    "max_rounds": "max_rounds",
}

_PREVIEW_ROWS = 5


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
    provenance = {key: prov.value for key, prov in draft.resolved.items()}
    preview = [dict(row) for row in draft.sample_preview[:_PREVIEW_ROWS]]

    state = {
        "uploaded_columns": list(draft.headers),
        "n_samples": draft.n_samples,
        "sample_rows": preview,
        "current_values": values,
        "provenance": provenance,
        "open_gaps": [gap.to_wire() for gap in readiness.gaps],
    }
    operator_message = draft.task_description  # latest operator framing, if any
    user_content = (
        "DRAFT-CAMPAIGN ORIGIN to resolve. Propose values for the OPEN gaps "
        "below, each with cited evidence; write a plain-language "
        "task_description grounded in the sample rows.\n\n"
        f"{json.dumps(state, indent=2, ensure_ascii=False)}"
    )
    if operator_message:
        user_content += f"\n\nOperator's stated framing so far:\n{operator_message}"

    consultation_instruction = (
        "This is a draft-campaign origin. Respond in ORIGIN-RESOLUTION mode: "
        "fill 'assessment', 'findings', and 'next_action' (and 'recap' only "
        "when ready). Leave the Layer 1 + task_context fields empty."
    )
    return user_content, consultation_instruction


async def resolve_origin_turn(
    *,
    stores: Stores,
    draft: DraftCampaign,
    draft_registry: DraftCampaignRegistry,
) -> OriginResolutionResult:
    """Run one resolver turn, apply findings, re-gate, persist. Returns the
    resolution + post-apply draft."""
    user_content, consultation_instruction = build_origin_consultation(draft)

    client = get_llm_client(draft.optimizer_provider)
    model = draft.optimizer_model or None

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
                llm_client=client,
                model=model,
                user_content=user_content,
                context=LLMCallContext(ledger=ledger, round_num=0),
            )
    finally:
        reset_cycle_ledger(token)

    assert isinstance(raw, CheckinOutput), (
        f"checkin must return CheckinOutput, got {type(raw).__name__}"
    )

    updated = _apply_findings(draft, raw)
    draft_registry.update(updated)

    block = resolution_block(updated)
    block["last_resolution"] = _resolution_wire(raw)
    stores.tenant_datasets.write_draft_resolution(updated.draft_id, block)

    return OriginResolutionResult(resolution=block, draft=updated)


async def resolve_origin_until_gated(
    *,
    stores: Stores,
    draft: DraftCampaign,
    draft_registry: DraftCampaignRegistry,
    max_turns: int = 4,
) -> tuple[DraftCampaign, OriginResolutionResult | None]:
    """Auto-drive resolver turns until ``origin_readiness`` passes or progress
    stalls. The web does one turn per operator click; a headless caller (the CLI
    ``ingest`` verb) drives the loop itself. High-confidence findings auto-confirm
    each turn, so the gaps shrink turn over turn.

    Returns ``(final_draft, last_result)`` — ``last_result`` is ``None`` when the
    draft was already complete (no turn ran). Bounded by ``max_turns`` (mirrors
    the ``MAX_AUTO_REBASES`` backstop); a turn that applies nothing (``_apply_findings``
    returns the same object) is the stall signal and stops the loop early — no
    silent spin, the remaining gaps surface to the caller.
    """
    current = draft
    last: OriginResolutionResult | None = None
    for _ in range(max_turns):
        if origin_readiness(current).complete:
            break
        result = await resolve_origin_turn(
            stores=stores, draft=current, draft_registry=draft_registry
        )
        last = result
        if result.draft is current:  # nothing applied this turn → stalled
            break
        current = result.draft
    return current, last


def _apply_findings(draft: DraftCampaign, output: CheckinOutput) -> DraftCampaign:
    """Apply evidence-cited findings to the draft. High-confidence → CONFIRMED
    (auto), low → PROPOSED. Either way the source is AUTO (machine-proposed) —
    an operator confirmation later rides ``edit-draft-campaign`` and stamps
    STATED. Evidence-less or unmappable findings are dropped."""
    values: dict[str, Any] = {}
    provenance: dict[str, Provenance] = {}
    sources: dict[str, ProvenanceSource] = {}
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
        sources[finding.field] = ProvenanceSource.AUTO
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
        values["starting_prompt"] = {**draft.starting_prompt, **prompt_fields}
        provenance["starting_prompt"] = Provenance.CONFIRMED
        sources["starting_prompt"] = ProvenanceSource.AUTO
    if not values:
        return draft
    return draft.apply_resolution(values=values, provenance=provenance, sources=sources)


def _coerce(field_key: str, proposed: str, draft: DraftCampaign) -> Any | None:
    """Coerce a string ``proposed_value`` to the draft attribute's type; return
    ``None`` to drop a finding that can't be applied safely."""
    proposed = proposed.strip()
    if not proposed:
        return None
    if field_key in ("column.query", "column.ground_truth"):
        return proposed if proposed in draft.headers else None
    if field_key == "max_rounds":
        try:
            n = int(proposed)
        except ValueError:
            return None
        return n if 1 <= n <= 100 else None
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
    "resolve_origin_until_gated",
]
