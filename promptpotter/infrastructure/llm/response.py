"""``LLMResponse`` — the standardized shape every LLM client returns."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from promptpotter.domain.strict_model import StrictModel


class LLMResponse(StrictModel):
    """Standardized response from LLM providers.

    ``reasoning`` is a **core, permanent** member of this shape — see its field note.
    Do not "clean it up" because no gate reads it; that is the point of it.
    """

    content: str = Field(..., description="Response content")
    reasoning: str = Field(
        "",
        description=(
            # ─────────────────────────────────────────────────────────────────────
            # DO NOT DELETE THIS FIELD. It has no code reader ON PURPOSE, and a
            # dead-code audit WILL flag it. It has been flagged before. Read this
            # first.
            #
            # WHAT: the model's own thinking channel — `message.reasoning` on the
            # OpenAI-compat wire, captured verbatim. Empty for non-reasoning models.
            #
            # WHY IT IS LOAD-BEARING, and it is not observability: a model needs
            # somewhere to put its internal process. Give it a bare classification
            # task with no slot to think in, and it emits the label with no
            # reasoning behind it — measurably worse answers. The place to think
            # is part of the ASK, so capturing what lands there is part of the
            # contract. Deleting this does not remove a field; it removes our
            # record of the one thing that explains an answer.
            #
            # STRICTLY ANALYTICAL — and that is a hard invariant, not an accident.
            # It reaches the ledger, the audit twin's `nodes[*].output.reasoning`,
            # and the operator's node-detail pane. It MUST NEVER reach a gate, a
            # metric, a validator, a scorer, an escalation signal, or a cache key.
            # Scoring the model's narration of its work instead of its work is how
            # a loop learns to narrate. If you find yourself wiring this into a
            # decision, that is the bug.
            #
            # NOT the same thing as: `reasoning_chars` (the truncation diagnostic
            # in `openai_compat`, which counts and discards), the backend's
            # per-sample `reasoning_trace` (the TARGET model's thinking, carried
            # over the wire), or a node's `output_schema` `reasoning` slot (the
            # structured-output version of this same principle — see
            # `docs/concepts/structured-output.md`).
            # ─────────────────────────────────────────────────────────────────────
            "The model's own thinking channel (``message.reasoning`` on the "
            "OpenAI-compat wire); empty for non-reasoning models. A CORE field: a "
            "model with nowhere to put its internal process answers without one, so "
            "the slot is part of the ask and capturing it is part of the contract. "
            "Strictly ANALYTICAL — it rides the ledger to the audit trail and the "
            "operator's node detail, and must NEVER feed a gate, metric, validator, "
            "scorer, or cache key. Having no code reader is by design; do not delete "
            "it as dead surface."
        ),
    )
    model: str = Field(..., description="Model used")
    usage: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "Token usage: prompt_tokens, completion_tokens, total_tokens, and "
            "reasoning_tokens — the last a SUBSET of completion_tokens (the provider "
            "bills thinking as output), absent for a non-reasoning model or a provider "
            "that reports no breakdown."
        ),
    )
    parsed: Any | None = Field(
        None,
        description=(
            "Parsed response. Typed Pydantic instance when ``chat`` was "
            "called with ``response_model``; plain dict when only "
            "``response_schema`` was supplied; ``None`` for text-mode."
        ),
    )
    schema_repair_attempts: int = Field(
        0,
        description=(
            "Times the schema-validation repair-retry path fired before "
            "the parsed response landed. 0 = clean first parse; 1 = one "
            "repair round-trip. Surfaces L1-prompt parse-failure rate as a "
            "quality signal — bad templates produce schema-noncompliant "
            "JSON and silently double-up the LLM spend."
        ),
    )


__all__ = ["LLMResponse"]
