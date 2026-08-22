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
