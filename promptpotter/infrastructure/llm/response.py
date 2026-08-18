"""``LLMResponse`` — the standardized shape every LLM client returns."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from promptpotter.domain.strict_model import StrictModel


class LLMResponse(StrictModel):
    """The standardized response every provider returns. ``reasoning`` is a CORE, permanent member: do not clean it up
    because no gate reads it — that is the point of it."""

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
            "Token usage: prompt_tokens, completion_tokens, total_tokens, "
            "reasoning_tokens, cache_read_tokens and cache_write_tokens. The last three "
            "are SUBSETS, never further totals — reasoning of completion_tokens (the "
            "provider bills thinking as output), the two cache counts of prompt_tokens "
            "(every client normalizes to that, Anthropic included, which reports them "
            "beside its input count rather than inside it). A subset reads 0 when the "
            "provider reports no breakdown, which is not the same as none having happened."
        ),
    )
    cost_usd: float | None = Field(
        None,
        description=(
            "What the PROVIDER says the call cost, summed across a repair retry (both "
            "round-trips are billed). Its own field rather than a ``usage`` key because "
            "``usage`` counts tokens and this is money. ``None`` means the provider "
            "reported nothing — the honest answer, which routes the reader back to the "
            "rate table (``shared/pricing.py::compute_usd`` takes it as ``override_usd``) "
            "instead of quoting a zero nobody measured."
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
